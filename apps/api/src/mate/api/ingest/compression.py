"""Transparent, bounded decompression for uploaded event logs.

Any supported source format may arrive compressed (``log.csv.gz``,
``events.json.xz``, ``export.xes.bz2``, ``data.zip``, ``logs.tar.gz``, …).
Compression is handled at two choke points:

- filename level - :func:`split_compression` strips the compression suffix so
  ``detect_format`` can classify the *inner* extension;
- content level - :func:`decompressed` magic-sniffs the staged bytes and
  yields a plain-file path (a streamed temp copy for compressed inputs), so
  ``sniff_format``, the probe routes, and the import parsers never see
  compressed bytes.

Magic bytes (not the filename) drive the actual decompression, so legacy
``original.xes.gz`` re-imports and misnamed uploads both work. Archives (zip,
tar, compressed tar - see :mod:`mate.api.ingest.archive`) unwrap here only when
they hold exactly one importable member; an archive with several logs is split
into one staged upload per member by the import wizard instead.

Everything produced here is counted against :class:`DecompressionLimits`. The
cap is on bytes actually written, never on what a file claims about itself, so
a zip bomb fails fast with :class:`DecompressionLimitError` instead of filling
the disk.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import struct
import tempfile
import zipfile
import zlib
from collections.abc import Buffer, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast

from mate.api.config import get_settings

# Filename suffix → algorithm. Values are informational; decompression itself
# is magic-byte-driven.
COMPRESSION_SUFFIXES: dict[str, str] = {
    "gz": "gzip",
    "gzip": "gzip",
    "bz2": "bzip2",
    "xz": "xz",
    "lzma": "xz",
    "zip": "zip",
}

_MAGIC: list[tuple[bytes, str]] = [
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"PK\x03\x04", "zip"),
]

# Safety cap for nested wrapping (e.g. `log.csv.gz` inside a zip).
_MAX_NESTING = 3

_CHUNK = 1024 * 1024

# Outputs up to this size are never ratio-checked: a small but very repetitive
# log can legitimately compress beyond any sane ratio, and a bomb that has to
# stop here costs nothing.
RATIO_FLOOR_BYTES = 64 * 1024 * 1024

# Decoder memory cap for LZMA - `.xz` / `.lzma` streams and LZMA members of a
# zip alike. A hostile header can ask for a dictionary of gigabytes, and the
# decoder's memory grows into it as output is produced; even `xz -9e` and
# 7-Zip's "ultra" only use 64 MiB.
LZMA_MEMLIMIT = 128 * 1024 * 1024


class DecompressionLimitError(ValueError):
    """An upload would unpack past the zip-bomb guards."""


@dataclass(frozen=True)
class DecompressionLimits:
    """How far an upload may expand (see ``ingest_max_*`` in settings)."""

    max_bytes: int
    # Unpacked / packed size. 0 disables the ratio check.
    max_ratio: int
    max_entries: int

    @classmethod
    def from_settings(cls) -> DecompressionLimits:
        settings = get_settings()
        return cls(
            max_bytes=settings.ingest_max_decompressed_bytes,
            max_ratio=settings.ingest_max_compression_ratio,
            max_entries=settings.ingest_max_archive_entries,
        )

    def budget_for(self, packed_bytes: int) -> int:
        """The most ``packed_bytes`` of input may unpack to, every layer included."""
        if self.max_ratio <= 0:
            return self.max_bytes
        return min(self.max_bytes, max(RATIO_FLOOR_BYTES, packed_bytes * self.max_ratio))

    def implausible_ratio(self, unpacked: int, packed: int) -> bool:
        """Does a declared ``unpacked`` size give its ``packed`` bytes away as a bomb?"""
        return (
            self.max_ratio > 0
            and unpacked > RATIO_FLOOR_BYTES
            and unpacked > packed * self.max_ratio
        )

    def overflow(self, what: str, budget: int) -> DecompressionLimitError:
        """The error for ``what`` producing more than ``budget`` bytes."""
        if budget < self.max_bytes:
            return DecompressionLimitError(
                f"{what} expands to more than {self.max_ratio}x its compressed size - "
                "refusing to unpack what looks like a zip bomb."
            )
        return DecompressionLimitError(
            f"{what} unpacks to more than {human_bytes(self.max_bytes)}, the limit for "
            "compressed uploads. Unpack it locally and upload the log itself, or ask the "
            "operator to raise INGEST_MAX_DECOMPRESSED_BYTES."
        )


def human_bytes(n: int) -> str:
    """``10737418240`` → ``"10 GiB"`` - for limits quoted in error messages."""
    value = float(n)
    unit = "B"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            break
        value /= 1024
    return f"{value:.0f} {unit}" if value.is_integer() else f"{value:.1f} {unit}"


def split_compression(filename: str) -> tuple[str, str | None]:
    """``log.csv.gz`` → (``log.csv``, "gzip"); ``log.csv`` → (``log.csv``, None)."""
    lower = filename.lower()
    for sfx, algo in COMPRESSION_SUFFIXES.items():
        if lower.endswith(f".{sfx}"):
            return filename[: -(len(sfx) + 1)], algo
    return filename, None


def detect_compression(path: Path) -> str | None:
    """Magic-byte sniff of ``path``. ``None`` for plain (or unreadable) files."""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    for magic, algo in _MAGIC:
        if head.startswith(magic):
            return algo
    return None


class _XzReader(io.RawIOBase):
    """``lzma.open`` with a decoder memory cap - the stdlib reader has no knob.

    Mirrors the stdlib reader otherwise: concatenated streams decode back to
    back, and trailing garbage after a complete stream reads as end of file.
    """

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._fp = path.open("rb")
        self._decoder = self._new_decoder()
        self._done = False

    @staticmethod
    def _new_decoder() -> lzma.LZMADecompressor:
        return lzma.LZMADecompressor(lzma.FORMAT_AUTO, memlimit=LZMA_MEMLIMIT)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Buffer, /) -> int:
        with memoryview(buffer) as view, view.cast("B") as byte_view:
            data = self._read(len(byte_view))
            byte_view[: len(data)] = data
        return len(data)

    def _read(self, size: int) -> bytes:
        try:
            while not self._done:
                if self._decoder.eof:
                    rest = self._decoder.unused_data or self._fp.read(_CHUNK)
                    if not rest:
                        self._done = True
                        break
                    self._decoder = self._new_decoder()
                    try:
                        data = self._decoder.decompress(rest, size)
                    except lzma.LZMAError:
                        self._done = True  # trailing garbage after a complete stream
                        break
                elif self._decoder.needs_input:
                    raw = self._fp.read(_CHUNK)
                    if not raw:
                        raise EOFError(
                            "Compressed file ended before the end-of-stream marker was reached"
                        )
                    data = self._decoder.decompress(raw, size)
                else:
                    data = self._decoder.decompress(b"", size)
                if data:
                    return data
        except lzma.LZMAError as exc:
            if "memory usage limit" in str(exc).lower():
                raise DecompressionLimitError(
                    "The xz stream needs more than "
                    f"{human_bytes(LZMA_MEMLIMIT)} of memory to decode - refusing to unpack it."
                ) from exc
            raise
        return b""

    def close(self) -> None:
        if not self.closed:
            self._fp.close()
        super().close()


def open_compressed(path: Path, algo: str) -> IO[bytes]:
    """A readable stream of ``path``'s decompressed bytes (not size-bounded)."""
    if algo == "gzip":
        return cast(IO[bytes], gzip.open(path, "rb"))
    if algo == "bzip2":
        return cast(IO[bytes], bz2.open(path, "rb"))
    if algo == "xz":
        return io.BufferedReader(_XzReader(path), _CHUNK)
    raise ValueError(f"Unsupported compression: {algo}")


def _is_damage(exc: BaseException) -> bool:
    """Is this how a damaged or truncated stream fails - as opposed to the disk
    (an ``OSError`` carrying an errno: full, read-only, permissions)?"""
    if isinstance(
        exc,
        EOFError
        | zlib.error
        | lzma.LZMAError
        | zipfile.BadZipFile
        | struct.error
        | UnicodeDecodeError,  # a zip entry flagged UTF-8 that isn't
    ):
        return True
    return isinstance(exc, OSError) and exc.errno is None


@contextmanager
def reading(what: str) -> Generator[None]:
    """Turn the many ways a damaged upload fails while being decoded (gzip's
    ``BadGzipFile``, bzip2's errno-less ``OSError``, ``EOFError`` on a cut-off
    stream, …) into one ``ValueError``, which the routes answer with a 4xx."""
    try:
        yield
    except Exception as exc:
        if isinstance(exc, DecompressionLimitError) or not _is_damage(exc):
            raise
        raise ValueError(f"{what} is damaged or truncated: {exc}") from exc


def bounded_copy(
    src: IO[bytes],
    dst: IO[bytes],
    *,
    budget: int,
    limits: DecompressionLimits,
    what: str,
) -> int:
    """Stream ``src`` into ``dst``, failing once more than ``budget`` bytes came out."""
    written = 0
    while chunk := src.read(_CHUNK):
        written += len(chunk)
        if written > budget:
            raise limits.overflow(what, budget)
        dst.write(chunk)
    return written


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _inner_suffix(path: Path, member: str | None) -> str:
    """Suffix for the decompressed temp file (kept meaningful for readers that
    care about extensions, e.g. pm4py's sqlite OCEL reader)."""
    name = member.rsplit("/", 1)[-1] if member else split_compression(path.name)[0]
    suffix = Path(name).suffix
    return suffix if suffix else ".bin"


@contextmanager
def decompressed(path: Path, *, limits: DecompressionLimits | None = None) -> Generator[Path]:
    """Yield a plain-file path for ``path``.

    Uncompressed inputs are yielded as-is. Compressed inputs are streamed into
    a temp file that lives only within the context. Nested wrapping (a
    ``log.csv.gz`` inside a zip) is unwrapped up to ``_MAX_NESTING`` layers,
    every one of them bounded by the budget of the *outermost* file, so the
    amplification is always measured against what was actually uploaded.
    """
    # Runtime import: archive.py builds on this module.
    from mate.api.ingest import archive

    limits = limits or DecompressionLimits.from_settings()
    budget = limits.budget_for(file_size(path))
    temps: list[Path] = []
    try:
        current = path
        # Only the unwrapping: whatever the caller does with the plain file
        # keeps its own error semantics.
        with reading("The upload"):
            for _ in range(_MAX_NESTING):
                kind = archive.archive_kind(current)
                algo = None if kind is not None else detect_compression(current)
                if kind is None and algo is None:
                    break
                member = archive.resolve_single_member(current, limits=limits) if kind else None
                with tempfile.NamedTemporaryFile(
                    prefix="mate-decompress-",
                    suffix=_inner_suffix(current, member),
                    delete=False,
                ) as fd:
                    tmp = Path(fd.name)
                    temps.append(tmp)
                    out = cast(IO[bytes], fd)
                    if member is not None:
                        archive.extract_member(current, member, out, limit=budget, limits=limits)
                    else:
                        with open_compressed(current, cast(str, algo)) as src:
                            bounded_copy(src, out, budget=budget, limits=limits, what="The upload")
                if current != path:
                    # The layer just unwrapped is dead weight - don't keep every
                    # intermediate copy on disk at once.
                    with suppress(OSError):
                        current.unlink()
                current = tmp
            else:
                raise ValueError("Upload is nested in too many compression layers.")
        yield current
    finally:
        for tmp in temps:
            with suppress(OSError):
                tmp.unlink()


__all__ = [
    "COMPRESSION_SUFFIXES",
    "LZMA_MEMLIMIT",
    "RATIO_FLOOR_BYTES",
    "DecompressionLimitError",
    "DecompressionLimits",
    "bounded_copy",
    "decompressed",
    "detect_compression",
    "file_size",
    "human_bytes",
    "open_compressed",
    "reading",
    "split_compression",
]
