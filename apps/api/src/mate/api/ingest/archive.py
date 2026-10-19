"""Multi-member upload archives: zip, tar and compressed tar.

An uploaded archive is either a wrapper around a single event log - unwrapped
transparently by :func:`mate.api.ingest.compression.decompressed`, just like a
``.gz`` - or a bundle of several logs (a BPI Challenge download, say), which the
import wizard lists with :func:`inspect_archive` and splits into one staged
upload per member the user picks, via :func:`extract_member`.

Archives are hostile input, so nothing here takes one at its word:

- member names are display strings and selection keys only. Nothing is ever
  written to a path derived from one, so ``../`` names and links can't escape.
- zip: the central directory is sized up from the end-of-central-directory
  record *before* zipfile materialises it, entries that share compressed bytes
  (the trick behind non-recursive zip bombs) reject the archive, and a member
  whose declared size is implausible for its packed size is refused unread.
  zipfile itself never yields more than a member's declared size.
- tar: read by the small forward-only walker below instead of ``tarfile``,
  which buffers pax / long-name / sparse-map metadata in memory without any
  bound. The decompressed stream is metered as it's read (listing a
  ``.tar.gz`` means decompressing all of it), metadata is capped in total, and
  sparse or special entries are listed but never importable.
- every member is unpacked through :func:`bounded_copy`, against both its own
  size and the budget the caller holds for the archive as a whole.
"""

from __future__ import annotations

import io
import lzma
import struct
import zipfile
import zlib
from collections import Counter
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any

from mate.api.ingest.compression import (
    LZMA_MEMLIMIT,
    DecompressionLimitError,
    DecompressionLimits,
    bounded_copy,
    detect_compression,
    file_size,
    human_bytes,
    open_compressed,
    reading,
)

# Filename suffixes of tar archives, plain or compressed as a whole.
TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tbz", ".tar.xz", ".txz")

# Zip methods zipfile can read. Deflate64 - what Windows Explorer writes for big
# files - isn't one of them, so such members are listed with a reason instead of
# failing halfway through an import.
_ZIP_METHODS = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
)

_ZIP_LOCAL_HEADER_SIZE = 30
_EOCD = struct.Struct("<4s4H2LH")
_EOCD64_LOCATOR = struct.Struct("<4sLQL")
_EOCD64 = struct.Struct("<4sQ2H2L4Q")
_EOCD_MAX_COMMENT = 0xFFFF
# Bytes of central directory allowed per permitted entry. Real entries are
# 100-200 bytes; zipfile holds every one of them in memory at once.
_CENTRAL_DIR_BYTES_PER_ENTRY = 512

_BLOCK = 512
_ZERO_BLOCK = bytes(_BLOCK)
_TAR_REGULAR = frozenset({b"0", b"\x00", b"7"})
# Links, character / block devices, folders and FIFOs have no data blocks, even
# when their size field says otherwise (tarfile reads them the same way).
_TAR_NO_DATA = frozenset({b"1", b"2", b"3", b"4", b"5", b"6"})
# Headers that describe the *next* member: pax (x / X), pax global (g), GNU
# long name (L) and long link name (K).
_TAR_META = frozenset({b"x", b"X", b"g", b"L", b"K"})
# A member may arrive with a pax, a long-name and a long-link header of its own.
_TAR_HEADERS_PER_ENTRY = 4
# Real metadata is a few hundred bytes per member; this bounds what one archive
# can make us hold in memory.
_TAR_META_BUDGET = 4 * 1024 * 1024
_CHUNK = 1024 * 1024

# Coarse formats (`detect_format`) of a bare archive. One nested in an upload is
# listed but never unpacked: recursion is how the classic zip bombs multiply.
# A compressed *log* inside an archive (`log.csv.gz`, `log.xes.zip`) is fine -
# its name says what it holds.
_ARCHIVE_FORMATS = frozenset({"zip", "tar"})
_NESTED_ARCHIVE = "An archive inside the archive - unpack it first."


@dataclass(frozen=True)
class ArchiveEntry:
    """One file inside an uploaded archive."""

    # Name inside the archive: a display string and selection key, never a path.
    path: str
    # Unpacked bytes as the archive declares them - enforced on extraction.
    size: int
    # Packed bytes; zip only (a tar is compressed as a whole).
    packed: int | None
    # Coarse format from the file name (`detect_format`), None if it isn't a log.
    format: str | None
    # Why it can't be imported; None when it can.
    reason: str | None = None
    # Refused by a zip-bomb guard rather than for being the wrong kind of file.
    unsafe: bool = False

    @property
    def importable(self) -> bool:
        return self.reason is None

    @property
    def name(self) -> str:
        return member_basename(self.path)


@dataclass(frozen=True)
class ArchiveListing:
    kind: str  # "zip" | "tar"
    # The stream a tar is wrapped in ("gzip" / "bzip2" / "xz"), None otherwise.
    compression: str | None
    # Archive order; directories and OS junk (__MACOSX, dotfiles) left out.
    entries: list[ArchiveEntry]
    # What the whole archive may unpack to, all of its members together.
    budget: int

    @property
    def importable(self) -> list[ArchiveEntry]:
        return [e for e in self.entries if e.importable]

    @property
    def label(self) -> str:
        """``zip`` / ``tar`` / ``tar.gz`` / ``tar.bz2`` / ``tar.xz``."""
        suffix = {"gzip": "gz", "bzip2": "bz2", "xz": "xz"}.get(self.compression or "")
        return f"{self.kind}.{suffix}" if suffix else self.kind

    def get(self, path: str) -> ArchiveEntry | None:
        return next((e for e in self.entries if e.path == path), None)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchiveListing:
        return cls(
            kind=str(data["kind"]),
            compression=data.get("compression"),
            entries=[ArchiveEntry(**entry) for entry in data.get("entries", [])],
            budget=int(data["budget"]),
        )


def member_basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def is_junk_member(path: str) -> bool:
    """Archive noise that must never count as a candidate member."""
    parts = path.replace("\\", "/").split("/")
    if path.endswith("/") or "__MACOSX" in parts:
        return True
    base = parts[-1]
    return not base or base.startswith(".") or base.startswith("~$")


def member_format(path: str) -> str | None:
    """Coarse format of a member from its name; ``None`` when it isn't a log."""
    # Runtime import: detect.py imports this module.
    from mate.api.ingest.detect import detect_format

    try:
        return detect_format(member_basename(path))
    except ValueError:
        return None


def archive_kind(path: Path) -> str | None:
    """``"zip"`` / ``"tar"`` (compressed or not) by content, else ``None``."""
    sniffed = _sniff(path)
    return sniffed[0] if sniffed else None


def inspect_archive(
    path: Path, *, limits: DecompressionLimits | None = None
) -> ArchiveListing | None:
    """List ``path``'s members, or return ``None`` when it isn't an archive.

    Raises :class:`DecompressionLimitError` for an archive that trips a
    zip-bomb guard and ``ValueError`` for one that can't be read at all.
    """
    sniffed = _sniff(path)
    if sniffed is None:
        return None
    limits = limits or DecompressionLimits.from_settings()
    kind, compression = sniffed
    budget = limits.budget_for(file_size(path))
    if kind == "zip":
        entries = _list_zip(path, limits, budget)
    else:
        entries = _list_tar(path, compression, limits, budget)
    return ArchiveListing(
        kind=kind, compression=compression, entries=_flag_duplicates(entries), budget=budget
    )


def resolve_single_member(path: Path, *, limits: DecompressionLimits | None = None) -> str:
    """The one importable member of a single-log archive.

    Raises ``ValueError`` when the archive holds no importable log or several
    of them: several logs need the import wizard, which stages each one on its
    own. A lone log refused by a zip-bomb guard raises
    :class:`DecompressionLimitError`.
    """
    listing = inspect_archive(path, limits=limits)
    if listing is None:
        raise ValueError("The upload is not an archive.")
    importable = listing.importable
    if len(importable) == 1:
        return importable[0].path
    if importable:
        names = ", ".join(e.name for e in importable[:5]) + ("…" if len(importable) > 5 else "")
        raise ValueError(
            f"The archive contains {len(importable)} event logs ({names}). Upload it in the "
            "import wizard to choose which ones to import."
        )
    refused = next((e for e in listing.entries if e.format is not None), None)
    if refused is not None:
        error = DecompressionLimitError if refused.unsafe else ValueError
        raise error(f"{refused.name}: {refused.reason}")
    raise ValueError(
        "The archive contains no supported event-log file "
        "(.xes, .xes.gz, .csv, .xml, .json, or an OCEL extension)."
    )


def extract_member(
    path: Path,
    member: str,
    dest: IO[bytes],
    *,
    limit: int,
    limits: DecompressionLimits | None = None,
) -> int:
    """Copy one member's bytes into ``dest`` and return how many were written.

    ``limit`` caps this member's output; a tar's stream is also capped by the
    archive's own budget. Raises :class:`DecompressionLimitError` past either,
    ``ValueError`` when the member is missing, not a file, or unreadable.
    """
    limits = limits or DecompressionLimits.from_settings()
    sniffed = _sniff(path)
    if sniffed is None:
        raise ValueError("The upload is not an archive.")
    kind, compression = sniffed
    if kind == "zip":
        return _extract_zip(path, member, dest, limit, limits)
    return _extract_tar(path, compression, member, dest, limit, limits)


# ── detection ─────────────────────────────────────────────────────────────────


def _sniff(path: Path) -> tuple[str, str | None] | None:
    algo = detect_compression(path)
    if algo == "zip":
        return "zip", None
    try:
        if algo is None:
            with path.open("rb") as fh:
                head = fh.read(_BLOCK)
        else:
            with open_compressed(path, algo) as fh:
                head = fh.read(_BLOCK)
    except (OSError, EOFError, ValueError, zlib.error, lzma.LZMAError):
        # A damaged stream isn't recognisably a tar; the plain decompression
        # path reports the damage with a better message.
        return None
    return ("tar", algo) if _is_tar_header(head) else None


def _is_tar_header(block: bytes) -> bool:
    """A ustar / GNU / pax header block: "ustar" magic and a valid checksum."""
    return len(block) >= _BLOCK and block[257:262] == b"ustar" and _tar_checksum_ok(block)


def _tar_checksum_ok(block: bytes) -> bool:
    try:
        stored = _tar_number(block[148:156])
    except ValueError:
        return False
    # The checksum field itself counts as eight spaces; old tars summed signed bytes.
    unsigned = 256 + sum(struct.unpack_from("148B8x356B", block))
    signed = 256 + sum(struct.unpack_from("148b8x356b", block))
    return stored in (unsigned, signed)


# ── zip ───────────────────────────────────────────────────────────────────────


def _too_many_entries(limits: DecompressionLimits) -> DecompressionLimitError:
    return DecompressionLimitError(
        f"The archive holds more than {limits.max_entries:,} entries - refusing to read it."
    )


def _check_zip_directory(path: Path, limits: DecompressionLimits) -> None:
    """Size up the central directory before zipfile loads it into memory.

    Mirrors zipfile's own lookup of the end-of-central-directory record, zip64
    included, so both read the same numbers. zipfile parses the directory by
    its byte size rather than by its entry count, so both are checked.
    """
    stats = _zip_directory_stats(path)
    if stats is None:
        return  # no record at all: zipfile raises its own "not a zip" error
    entries, directory_bytes = stats
    if (
        entries > limits.max_entries
        or directory_bytes > limits.max_entries * _CENTRAL_DIR_BYTES_PER_ENTRY
    ):
        raise _too_many_entries(limits)


def _zip_directory_stats(path: Path) -> tuple[int, int] | None:
    """(entry count, central directory bytes), or None without a record."""
    size = file_size(path)
    if size < _EOCD.size:
        return None
    with path.open("rb") as fh:
        tail_len = min(size, _EOCD.size + _EOCD_MAX_COMMENT)
        fh.seek(size - tail_len)
        tail = fh.read(tail_len)
        at = tail.rfind(b"PK\x05\x06")
        if at < 0 or len(tail) - at < _EOCD.size:
            return None
        record = _EOCD.unpack_from(tail, at)
        entries, directory_bytes = int(record[4]), int(record[5])
        # A zip64 locator right before the record overrides it, as in zipfile,
        # which expects the zip64 record right before the locator.
        locator_at = size - tail_len + at - _EOCD64_LOCATOR.size
        record64_at = locator_at - _EOCD64.size
        if record64_at < 0:
            return entries, directory_bytes
        fh.seek(locator_at)
        if fh.read(4) != b"PK\x06\x07":
            return entries, directory_bytes
        fh.seek(record64_at)
        record64 = fh.read(_EOCD64.size)
        if len(record64) < _EOCD64.size or record64[:4] != b"PK\x06\x06":
            return entries, directory_bytes
        fields = _EOCD64.unpack(record64)
        return int(fields[7]), int(fields[8])


def _check_zip_layout(infos: list[zipfile.ZipInfo], archive_size: int) -> None:
    """Reject archives whose members share compressed bytes.

    Real archives store each member's data once, back to back. Overlapping
    entries - one deflate stream referenced thousands of times - are how
    non-recursive zip bombs reach expansion ratios in the millions.
    """
    previous_offset, previous_end, packed_total = -1, 0, 0
    for offset, packed in sorted((i.header_offset, i.compress_size) for i in infos):
        if offset == previous_offset or offset < previous_end:
            raise DecompressionLimitError(
                "The zip archive's entries overlap - refusing to unpack what looks like a zip bomb."
            )
        previous_offset, previous_end = offset, offset + packed
        packed_total += packed
    if packed_total > archive_size:
        raise DecompressionLimitError(
            "The zip archive claims more compressed data than it contains - refusing to unpack it."
        )


def _list_zip(path: Path, limits: DecompressionLimits, budget: int) -> list[ArchiveEntry]:
    _check_zip_directory(path, limits)
    with reading("The zip archive"), zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
    if len(infos) > limits.max_entries:
        raise _too_many_entries(limits)
    _check_zip_layout(infos, file_size(path))
    entries: list[ArchiveEntry] = []
    for info in infos:
        if info.is_dir() or is_junk_member(info.filename):
            continue
        fmt = member_format(info.filename)
        with reading("The zip archive"):
            reason, unsafe = _zip_refusal(path, info, fmt, limits, budget)
        entries.append(
            ArchiveEntry(
                path=info.filename,
                size=info.file_size,
                packed=info.compress_size,
                format=fmt,
                reason=reason,
                unsafe=unsafe,
            )
        )
    return entries


def _zip_refusal(
    path: Path,
    info: zipfile.ZipInfo,
    fmt: str | None,
    limits: DecompressionLimits,
    budget: int,
) -> tuple[str | None, bool]:
    if fmt is None:
        return "Not an event log.", False
    if fmt in _ARCHIVE_FORMATS:
        return _NESTED_ARCHIVE, False
    if info.flag_bits & 0x1:
        return "Password-protected - unpack it locally first.", False
    if info.compress_type not in _ZIP_METHODS:
        return (
            "Packed with a compression method we can't read (e.g. Deflate64) - "
            "re-create the zip with standard compression.",
            False,
        )
    if (lzma_reason := _lzma_refusal(path, info)) is not None:
        return lzma_reason, True
    if limits.implausible_ratio(info.file_size, info.compress_size):
        return "Implausibly high compression ratio - looks like a zip bomb.", True
    if info.file_size > budget:
        return _too_large(info.file_size, budget), True
    return None, False


def _lzma_refusal(path: Path, info: zipfile.ZipInfo) -> str | None:
    """Why an LZMA member can't be decoded safely, or None.

    zipfile decodes LZMA members with the dictionary size their own header asks
    for and no memory limit - the decoder's memory then grows towards it as
    output is produced. So the properties are read ahead of zipfile: a 4-byte
    header (version, properties size) and 5 bytes of LZMA1 properties right
    after the member's local file header.
    """
    if info.compress_type != zipfile.ZIP_LZMA:
        return None
    with path.open("rb") as fh:
        fh.seek(info.header_offset)
        local = fh.read(_ZIP_LOCAL_HEADER_SIZE)
        if len(local) < _ZIP_LOCAL_HEADER_SIZE or local[:4] != b"PK\x03\x04":
            return "Its LZMA header can't be read - re-create the zip with standard compression."
        name_len, extra_len = struct.unpack_from("<HH", local, 26)
        fh.seek(info.header_offset + _ZIP_LOCAL_HEADER_SIZE + name_len + extra_len)
        head = fh.read(9)
    if len(head) < 9 or struct.unpack_from("<H", head, 2)[0] < 5:
        return "Its LZMA header can't be read - re-create the zip with standard compression."
    if struct.unpack_from("<L", head, 5)[0] > LZMA_MEMLIMIT:
        return (
            f"Its LZMA dictionary needs more than {human_bytes(LZMA_MEMLIMIT)} of memory - "
            "re-create the zip with standard compression."
        )
    return None


def _extract_zip(
    path: Path, member: str, dest: IO[bytes], limit: int, limits: DecompressionLimits
) -> int:
    _check_zip_directory(path, limits)
    with reading("The zip archive"), zipfile.ZipFile(path) as zf:
        try:
            info = zf.getinfo(member)
        except KeyError as exc:
            raise ValueError(f"{member!r} is not in the archive.") from exc
        if info.is_dir():
            raise ValueError(f"{member!r} is a folder, not a file.")
        if info.flag_bits & 0x1 or info.compress_type not in _ZIP_METHODS:
            raise ValueError(f"{member!r} is encrypted or packed in a way we can't read.")
        if (lzma_reason := _lzma_refusal(path, info)) is not None:
            raise DecompressionLimitError(f"{member_basename(member)}: {lzma_reason}")
        if info.file_size > limit:
            raise limits.overflow(f"{member_basename(member)!r}", limit)
        # zipfile stops at the declared size and checks the CRC; the bounded
        # copy is the belt to those braces.
        with zf.open(info) as src:
            return bounded_copy(
                src, dest, budget=limit, limits=limits, what=f"{member_basename(member)!r}"
            )


# ── tar ───────────────────────────────────────────────────────────────────────


class _MeteredStream:
    """Forward-only read side of a (decompressed) tar stream with a byte budget.

    Sits on the *decompressed* side, so it counts what the archive really
    expands to - not what its headers claim. A plain ``.tar`` skips member
    data with a seek: nothing is produced, so there is nothing to meter.
    """

    def __init__(
        self, fp: IO[bytes], budget: int, limits: DecompressionLimits, *, seekable: bool
    ) -> None:
        self._fp = fp
        self._budget = budget
        self._left = budget
        self._limits = limits
        self._seekable = seekable

    def read(self, size: int) -> bytes:
        data = self._fp.read(min(size, self._left + 1))
        self._left -= len(data)
        if self._left < 0:
            raise self._limits.overflow("The archive", self._budget)
        return data

    def read_exact(self, size: int) -> bytes:
        parts: list[bytes] = []
        while size > 0:
            data = self.read(min(size, _CHUNK))
            if not data:
                break
            parts.append(data)
            size -= len(data)
        return b"".join(parts)

    def skip(self, size: int) -> None:
        if self._seekable:
            self._fp.seek(size, io.SEEK_CUR)
            return
        while size > 0:
            data = self.read(min(size, _CHUNK))
            if not data:
                raise ValueError("The tar archive is truncated.")
            size -= len(data)


class _TarMember:
    """A member of a tar stream; its data is readable until the walk moves on."""

    def __init__(self, name: str, size: int, regular: bool, stream: _MeteredStream) -> None:
        self.name = name
        self.size = size
        self.regular = regular
        self.unread = size
        self._stream = stream

    def copy_to(self, dest: IO[bytes], *, limit: int, limits: DecompressionLimits) -> int:
        if self.size > limit:
            raise limits.overflow(f"{member_basename(self.name)!r}", limit)
        written = 0
        while self.unread:
            chunk = self._stream.read(min(_CHUNK, self.unread))
            if not chunk:
                raise ValueError("The tar archive is truncated.")
            self.unread -= len(chunk)
            written += len(chunk)
            dest.write(chunk)
        return written


@contextmanager
def _tar_stream(
    path: Path, compression: str | None, budget: int, limits: DecompressionLimits
) -> Generator[_MeteredStream]:
    with reading("The tar archive"):
        raw = open_compressed(path, compression) if compression else path.open("rb")
        with raw:
            yield _MeteredStream(raw, budget, limits, seekable=compression is None)


def _walk_tar(stream: _MeteredStream, limits: DecompressionLimits) -> Iterator[_TarMember]:
    """Yield the members of a ustar / GNU / pax tar stream, in order.

    A consumer may read the current member's data (``copy_to``) before asking
    for the next one; whatever it leaves is skipped. Only ``path`` and
    ``size`` are taken from pax headers - everything else a member could
    carry is irrelevant to an import.
    """
    pax: dict[str, str] = {}
    long_name: str | None = None
    meta_spent = 0
    headers = 0
    while True:
        header = stream.read_exact(_BLOCK)
        if not header or header == _ZERO_BLOCK:
            return  # end-of-archive marker (or a clean end without one)
        if len(header) < _BLOCK:
            raise ValueError("The tar archive is truncated.")
        if not _tar_checksum_ok(header):
            raise ValueError("The tar archive is damaged (bad header checksum).")
        headers += 1
        if headers > limits.max_entries * _TAR_HEADERS_PER_ENTRY:
            raise _too_many_entries(limits)
        typeflag = header[156:157]
        size = _tar_number(header[124:136])

        if typeflag in _TAR_META:
            meta_spent += size
            if meta_spent > _TAR_META_BUDGET:
                raise DecompressionLimitError(
                    "The tar archive's metadata headers are implausibly large - refusing "
                    "to read it."
                )
            payload = stream.read_exact(_padded(size))[:size]
            if len(payload) < size:
                raise ValueError("The tar archive is truncated.")
            if typeflag in (b"x", b"X"):
                pax.update(_parse_pax(payload))
            elif typeflag == b"L":
                long_name = _decode(payload.split(b"\x00", 1)[0])
            continue

        name = pax.get("path") or long_name or _ustar_name(header)
        if "size" in pax:
            size = _parse_int(pax["size"])
        if typeflag in _TAR_NO_DATA:
            size = 0
        sparse = typeflag == b"S" or any(key.startswith("GNU.sparse.") for key in pax)
        if typeflag == b"S" and header[482]:
            # Old GNU sparse member: extension headers sit before its data.
            while True:
                extension = stream.read_exact(_BLOCK)
                if len(extension) < _BLOCK:
                    raise ValueError("The tar archive is truncated.")
                if not extension[504]:
                    break
        pax, long_name = {}, None

        member = _TarMember(name, size, typeflag in _TAR_REGULAR and not sparse, stream)
        yield member
        stream.skip(member.unread + _padded(size) - size)


def _list_tar(
    path: Path, compression: str | None, limits: DecompressionLimits, budget: int
) -> list[ArchiveEntry]:
    entries: list[ArchiveEntry] = []
    members = 0
    with _tar_stream(path, compression, budget, limits) as stream:
        for member in _walk_tar(stream, limits):
            members += 1
            if members > limits.max_entries:
                raise _too_many_entries(limits)
            if is_junk_member(member.name):
                continue
            fmt = member_format(member.name)
            if not member.regular:
                if fmt is None:
                    continue  # folders, links, devices: nothing anyone would import
                reason, unsafe = "Not a regular file (a link or special entry).", False
            elif fmt is None:
                reason, unsafe = "Not an event log.", False
            elif fmt in _ARCHIVE_FORMATS:
                reason, unsafe = _NESTED_ARCHIVE, False
            elif member.size > budget:
                reason, unsafe = _too_large(member.size, budget), True
            else:
                reason, unsafe = None, False
            entries.append(
                ArchiveEntry(
                    path=member.name,
                    size=member.size,
                    packed=None,
                    format=fmt,
                    reason=reason,
                    unsafe=unsafe,
                )
            )
    return entries


def _extract_tar(
    path: Path,
    compression: str | None,
    member: str,
    dest: IO[bytes],
    limit: int,
    limits: DecompressionLimits,
) -> int:
    budget = limits.budget_for(file_size(path))
    with _tar_stream(path, compression, budget, limits) as stream:
        for candidate in _walk_tar(stream, limits):
            if candidate.name != member:
                continue
            if not candidate.regular:
                raise ValueError(f"{member!r} is not a regular file.")
            return candidate.copy_to(dest, limit=limit, limits=limits)
    raise ValueError(f"{member!r} is not in the archive.")


def _tar_number(field_bytes: bytes) -> int:
    """A tar numeric field: NUL/space-terminated octal, or GNU base-256."""
    if field_bytes[:1] in (b"\x80", b"\xff"):
        value = int.from_bytes(field_bytes[1:], "big")
        if field_bytes[0] == 0xFF:
            value -= 256 ** (len(field_bytes) - 1)
    else:
        text = field_bytes.split(b"\x00", 1)[0].strip()
        try:
            value = int(text or b"0", 8)
        except ValueError as exc:
            raise ValueError("The tar archive is damaged (bad number field).") from exc
    return _plausible(value)


def _parse_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError("The tar archive is damaged (bad pax size).") from exc
    return _plausible(value)


def _plausible(value: int) -> int:
    # Nothing real comes near 2**62 bytes, and seeking past 2**63 overflows.
    if not 0 <= value < 1 << 62:
        raise ValueError("The tar archive is damaged (implausible size).")
    return value


def _parse_pax(payload: bytes) -> dict[str, str]:
    """``"<len> <key>=<value>\\n"`` records → dict."""
    records: dict[str, str] = {}
    pos = 0
    while pos < len(payload):
        space = payload.find(b" ", pos)
        if space < 0:
            break
        try:
            length = int(payload[pos:space])
        except ValueError as exc:
            raise ValueError("The tar archive is damaged (bad pax record).") from exc
        if length <= space - pos or pos + length > len(payload):
            raise ValueError("The tar archive is damaged (bad pax record).")
        key, _, value = payload[space + 1 : pos + length - 1].partition(b"=")
        records[_decode(key)] = _decode(value)
        pos += length
    return records


def _ustar_name(header: bytes) -> str:
    name = header[:100].split(b"\x00", 1)[0]
    # Only POSIX ustar has a name prefix; GNU tar keeps timestamps in that slot.
    if header[257:265] == b"ustar\x0000":
        prefix = header[345:500].split(b"\x00", 1)[0]
        if prefix:
            name = prefix + b"/" + name
    return _decode(name)


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _padded(size: int) -> int:
    return -(-size // _BLOCK) * _BLOCK


# ── shared ────────────────────────────────────────────────────────────────────


def _too_large(size: int, budget: int) -> str:
    return f"Too large: unpacks to {human_bytes(size)}, the limit is {human_bytes(budget)}."


def _flag_duplicates(entries: list[ArchiveEntry]) -> list[ArchiveEntry]:
    """Members are picked by name, so a name that appears twice is ambiguous."""
    counts = Counter(e.path for e in entries)
    return [
        e
        if counts[e.path] == 1 or not e.importable
        else ArchiveEntry(
            path=e.path,
            size=e.size,
            packed=e.packed,
            format=e.format,
            reason="Appears more than once in the archive.",
        )
        for e in entries
    ]


__all__ = [
    "TAR_SUFFIXES",
    "ArchiveEntry",
    "ArchiveListing",
    "archive_kind",
    "extract_member",
    "inspect_archive",
    "is_junk_member",
    "member_basename",
    "member_format",
    "resolve_single_member",
]
