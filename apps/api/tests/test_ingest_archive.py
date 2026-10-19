"""Upload archives: listing, member extraction, and the zip-bomb guards.

Each guard gets a hostile archive built by hand - real bombs are crafted to get
past naive readers, so "it refuses a big file" proves little. Small payloads
stand in for gigabytes by shrinking the limits (and, where the ratio guard is
under test, its floor).
"""

from __future__ import annotations

import bz2
import errno
import gzip
import io
import lzma
import os
import struct
import tarfile
import tempfile
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from mate.api.ingest import compression
from mate.api.ingest.archive import extract_member, inspect_archive, resolve_single_member
from mate.api.ingest.compression import (
    DecompressionLimitError,
    DecompressionLimits,
    decompressed,
    human_bytes,
    reading,
)

CSV = b"case,activity,timestamp\nc1,A,2024-01-01 10:00:00\nc1,B,2024-01-01 11:00:00\n"
XES = (Path(__file__).parent / "fixtures" / "sample.xes").read_bytes()

GENEROUS = DecompressionLimits(max_bytes=10 * 1024**3, max_ratio=1000, max_entries=10_000)


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _tar(
    path: Path,
    members: dict[str, bytes],
    mode: str = "w:gz",
    fmt: int = tarfile.PAX_FORMAT,
) -> Path:
    with tarfile.open(path, mode, format=fmt) as tf:  # pyright: ignore[reportUnknownMemberType]
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _tar_header(name: str, size: int, typeflag: bytes) -> bytes:
    info = tarfile.TarInfo(name)
    info.size = size
    info.type = typeflag
    return info.tobuf(format=tarfile.USTAR_FORMAT)


def _pad(data: bytes) -> bytes:
    return data + bytes(-len(data) % 512)


def _extract(path: Path, member: str, limits: DecompressionLimits = GENEROUS) -> bytes:
    out = io.BytesIO()
    extract_member(path, member, out, limit=limits.max_bytes, limits=limits)
    return out.getvalue()


def _patch(path: Path, offset_from: bytes, offset: int, fmt: str, value: int) -> None:
    """Overwrite one little-endian field of the first record starting with ``offset_from``."""
    data = bytearray(path.read_bytes())
    struct.pack_into(fmt, data, data.index(offset_from) + offset, value)
    path.write_bytes(bytes(data))


# ── listing + extraction ──────────────────────────────────────────────────────


def test_zip_listing_skips_junk_and_flags_what_isnt_a_log(tmp_path: Path) -> None:
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("inner.csv", CSV)
    path = _zip(
        tmp_path / "bpi.zip",
        {
            "BPI/": b"",
            "BPI/log_a.xes": XES,
            "BPI/log_b.csv.gz": gzip.compress(CSV),
            "BPI/README.txt": b"license",
            "BPI/more.zip": nested.getvalue(),
            "__MACOSX/BPI/._log_a.xes": b"junk",
            "BPI/.DS_Store": b"junk",
        },
    )

    listing = inspect_archive(path, limits=GENEROUS)

    assert listing is not None
    assert (listing.kind, listing.label) == ("zip", "zip")
    by_path = {e.path: e for e in listing.entries}
    assert set(by_path) == {"BPI/log_a.xes", "BPI/log_b.csv.gz", "BPI/README.txt", "BPI/more.zip"}
    assert [e.path for e in listing.importable] == ["BPI/log_a.xes", "BPI/log_b.csv.gz"]
    assert by_path["BPI/log_a.xes"].format == "xes"
    assert by_path["BPI/log_a.xes"].size == len(XES)
    assert by_path["BPI/log_b.csv.gz"].format == "csv"
    assert by_path["BPI/README.txt"].reason == "Not an event log."
    # Archives in archives are how recursive bombs multiply: listed, never unpacked.
    assert "archive inside the archive" in (by_path["BPI/more.zip"].reason or "")

    assert _extract(path, "BPI/log_a.xes") == XES
    assert gzip.decompress(_extract(path, "BPI/log_b.csv.gz")) == CSV


@pytest.mark.parametrize("mode", ["w", "w:gz", "w:bz2", "w:xz"])
@pytest.mark.parametrize("fmt", [tarfile.USTAR_FORMAT, tarfile.GNU_FORMAT, tarfile.PAX_FORMAT])
def test_tar_listing_and_extraction(tmp_path: Path, mode: str, fmt: int) -> None:
    # Past 100 characters: a ustar prefix, a GNU long name, or a pax path.
    deep = "logs/" + "a-rather-long-folder/" * 6 + "log.csv"
    members = {deep: CSV, "logs/notes.md": b"# hi", "logs/b.xes": XES}
    if fmt == tarfile.PAX_FORMAT:
        members["logs/Ereignisprotokoll-ä.csv"] = CSV
    path = _tar(tmp_path / "logs.tar", members, mode, fmt)

    listing = inspect_archive(path, limits=GENEROUS)

    assert listing is not None and listing.kind == "tar"
    expected_compression = {"w": None, "w:gz": "gzip", "w:bz2": "bzip2", "w:xz": "xz"}[mode]
    assert listing.compression == expected_compression
    expected = [p for p in members if p != "logs/notes.md"]
    assert [e.path for e in listing.importable] == expected
    for member in expected:
        assert _extract(path, member) == members[member]


def test_plain_and_compressed_logs_are_not_archives(tmp_path: Path) -> None:
    for name, data in {
        "a.csv": CSV,
        "a.csv.gz": gzip.compress(CSV),
        "a.xes.bz2": bz2.compress(XES),
        "a.json.xz": lzma.compress(b"[]"),
    }.items():
        path = tmp_path / name
        path.write_bytes(data)
        assert inspect_archive(path, limits=GENEROUS) is None


def test_decompressed_unwraps_a_single_log_tar(tmp_path: Path) -> None:
    path = _tar(tmp_path / "one.tar.gz", {"x/log.csv": CSV, "x/notes.txt": b"n"})
    with decompressed(path, limits=GENEROUS) as plain:
        assert plain.read_bytes() == CSV
        assert plain.suffix == ".csv"


def test_single_member_resolution_names_the_problem(tmp_path: Path) -> None:
    several = _tar(tmp_path / "two.tar", {"a.csv": CSV, "b.xes": XES}, mode="w")
    with pytest.raises(ValueError, match=r"2 event logs \(a.csv, b.xes\)"):
        resolve_single_member(several, limits=GENEROUS)


def test_tar_links_and_special_files_are_never_importable(tmp_path: Path) -> None:
    path = tmp_path / "links.tar"
    with tarfile.open(path, "w") as tf:
        real = tarfile.TarInfo("real.csv")
        real.size = len(CSV)
        tf.addfile(real, io.BytesIO(CSV))
        for name, kind in (("link.csv", tarfile.SYMTYPE), ("hard.csv", tarfile.LNKTYPE)):
            link = tarfile.TarInfo(name)
            link.type = kind
            link.linkname = "/etc/passwd"
            tf.addfile(link)
        fifo = tarfile.TarInfo("pipe")
        fifo.type = tarfile.FIFOTYPE
        tf.addfile(fifo)

    listing = inspect_archive(path, limits=GENEROUS)

    assert listing is not None
    by_path = {e.path: e for e in listing.entries}
    assert by_path["real.csv"].importable
    for name in ("link.csv", "hard.csv"):
        assert not by_path[name].importable
        assert "regular file" in (by_path[name].reason or "")
    assert "pipe" not in by_path  # specials without a log-like name aren't even listed
    with pytest.raises(ValueError, match="not a regular file"):
        _extract(path, "link.csv")


def test_member_names_are_labels_never_paths(tmp_path: Path) -> None:
    work = tmp_path / "a" / "b"
    work.mkdir(parents=True)
    for path in (
        _zip(work / "evil.zip", {"../../escape.csv": CSV, "/abs.csv": CSV}),
        _tar(work / "evil.tar", {"../../escape.csv": CSV, "/abs.csv": CSV}, mode="w"),
    ):
        listing = inspect_archive(path, limits=GENEROUS)
        assert listing is not None
        assert {e.path for e in listing.importable} == {"../../escape.csv", "/abs.csv"}
        assert _extract(path, "../../escape.csv") == CSV
    assert not (tmp_path / "escape.csv").exists()


def test_duplicate_names_are_ambiguous_and_not_importable(tmp_path: Path) -> None:
    path = tmp_path / "dup.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns about the duplicate it's told to write
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("log.csv", CSV)
            zf.writestr("log.csv", CSV + b"c9,Z,2024-01-01 12:00:00\n")
            zf.writestr("other.csv", CSV)

    listing = inspect_archive(path, limits=GENEROUS)

    assert listing is not None
    assert [e.importable for e in listing.entries if e.path == "log.csv"] == [False, False]
    assert [e.path for e in listing.importable] == ["other.csv"]


def test_encrypted_and_unreadable_methods_are_explained(tmp_path: Path) -> None:
    encrypted = _zip(tmp_path / "secret.zip", {"log.csv": CSV, "open.csv": CSV})
    _patch(encrypted, b"PK\x01\x02", 8, "<H", 0x1)  # general-purpose flag: encrypted
    entry = (inspect_archive(encrypted, limits=GENEROUS) or pytest.fail()).get("log.csv")
    assert entry is not None and "Password-protected" in (entry.reason or "")
    with pytest.raises(ValueError, match="encrypted"):
        _extract(encrypted, "log.csv")

    deflate64 = _zip(tmp_path / "d64.zip", {"log.csv": CSV, "open.csv": CSV})
    _patch(deflate64, b"PK\x01\x02", 10, "<H", 9)  # method: Deflate64
    entry = (inspect_archive(deflate64, limits=GENEROUS) or pytest.fail()).get("log.csv")
    assert entry is not None and "compression method" in (entry.reason or "")


# ── zip-bomb guards ───────────────────────────────────────────────────────────


def test_overlapping_zip_entries_reject_the_archive(tmp_path: Path) -> None:
    """Entries sharing one deflate stream are the core of non-recursive zip
    bombs (thousands of them reach expansion ratios in the millions)."""
    path = _zip(tmp_path / "overlap.zip", {"a.csv": b"x" * 100_000, "b.csv": b"y"})
    data = bytearray(path.read_bytes())
    second = data.index(b"PK\x01\x02", data.index(b"PK\x01\x02") + 4)
    struct.pack_into("<L", data, second + 42, 0)  # b.csv's data → a.csv's
    path.write_bytes(bytes(data))

    with pytest.raises(DecompressionLimitError, match="overlap"):
        inspect_archive(path, limits=GENEROUS)
    with pytest.raises(DecompressionLimitError), decompressed(path, limits=GENEROUS):
        pass


def test_zip_claiming_more_data_than_it_holds_is_rejected(tmp_path: Path) -> None:
    path = _zip(tmp_path / "liar.zip", {"a.csv": CSV})
    _patch(path, b"PK\x01\x02", 20, "<L", 10**9)  # compressed size
    with pytest.raises(DecompressionLimitError, match="more compressed data"):
        inspect_archive(path, limits=GENEROUS)


def test_zip_member_lying_about_its_size_never_yields_more(tmp_path: Path) -> None:
    path = _zip(tmp_path / "liar.zip", {"log.csv": b"a" * 100_000})
    _patch(path, b"PK\x01\x02", 24, "<L", 1000)  # central directory: uncompressed size
    _patch(path, b"PK\x03\x04", 22, "<L", 1000)  # local header: uncompressed size

    out = io.BytesIO()
    with pytest.raises(ValueError, match="damaged"):
        extract_member(path, "log.csv", out, limit=10**9, limits=GENEROUS)
    assert len(out.getvalue()) <= 1000


def test_implausible_ratio_refuses_the_member_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compression, "RATIO_FLOOR_BYTES", 4096)
    path = _zip(tmp_path / "bomb.zip", {"log.csv": bytes(2 * 1024 * 1024), "ok.csv": CSV})
    limits = DecompressionLimits(max_bytes=10**9, max_ratio=100, max_entries=100)

    listing = inspect_archive(path, limits=limits)

    assert listing is not None
    bomb = listing.get("log.csv")
    assert bomb is not None and bomb.unsafe and "zip bomb" in (bomb.reason or "")
    # One bad member doesn't poison the rest of the archive.
    assert [e.path for e in listing.importable] == ["ok.csv"]


def test_lone_refused_log_raises_the_limit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compression, "RATIO_FLOOR_BYTES", 4096)
    path = _zip(tmp_path / "one.zip", {"log.csv": bytes(1024 * 1024), "readme.txt": b"hi"})
    limits = DecompressionLimits(max_bytes=10**9, max_ratio=100, max_entries=10)
    with pytest.raises(DecompressionLimitError, match=r"log\.csv"):
        resolve_single_member(path, limits=limits)


def test_member_over_the_budget_is_refused(tmp_path: Path) -> None:
    path = _zip(tmp_path / "big.zip", {"big.csv": os.urandom(8192), "small.csv": CSV})
    limits = DecompressionLimits(max_bytes=4096, max_ratio=1000, max_entries=100)

    listing = inspect_archive(path, limits=limits)

    assert listing is not None
    big = listing.get("big.csv")
    assert big is not None and big.unsafe and (big.reason or "").startswith("Too large")
    with pytest.raises(DecompressionLimitError):
        _extract(path, "big.csv", limits)


def test_entry_count_is_checked_before_the_directory_is_loaded(tmp_path: Path) -> None:
    path = _zip(tmp_path / "many.zip", {f"f{i}.csv": b"" for i in range(50)})
    limits = DecompressionLimits(max_bytes=10**9, max_ratio=1000, max_entries=10)
    with pytest.raises(DecompressionLimitError, match="more than 10 entries"):
        inspect_archive(path, limits=limits)

    # A record that understates the count still can't sneak 50 entries past.
    data = bytearray(path.read_bytes())
    struct.pack_into("<HH", data, data.rindex(b"PK\x05\x06") + 8, 1, 1)
    path.write_bytes(bytes(data))
    with pytest.raises(DecompressionLimitError):
        inspect_archive(path, limits=limits)


def test_gzip_bomb_stops_at_the_cap_and_cleans_up(tmp_path: Path) -> None:
    path = tmp_path / "log.csv.gz"
    path.write_bytes(gzip.compress(bytes(4 * 1024 * 1024)))
    limits = DecompressionLimits(max_bytes=1024 * 1024, max_ratio=0, max_entries=10)
    temp_dir = Path(tempfile.gettempdir())
    before = set(temp_dir.glob("mate-decompress-*"))

    with (
        pytest.raises(DecompressionLimitError, match="more than 1 MiB"),
        decompressed(path, limits=limits),
    ):
        pass
    assert set(temp_dir.glob("mate-decompress-*")) <= before


def test_ratio_guard_trips_long_before_the_size_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compression, "RATIO_FLOOR_BYTES", 4096)
    path = tmp_path / "log.csv.bz2"
    path.write_bytes(bz2.compress(bytes(4 * 1024 * 1024)))  # ~50 bytes of bzip2
    with (
        pytest.raises(DecompressionLimitError, match="zip bomb"),
        decompressed(path, limits=GENEROUS),
    ):
        pass


def test_tar_stream_is_metered_while_listing(tmp_path: Path) -> None:
    path = _tar(tmp_path / "bomb.tar.gz", {"zeros.bin": bytes(4 * 1024 * 1024), "log.csv": CSV})
    limits = DecompressionLimits(max_bytes=1024 * 1024, max_ratio=0, max_entries=100)
    with pytest.raises(DecompressionLimitError):
        inspect_archive(path, limits=limits)


def test_plain_tar_lists_past_a_member_over_the_budget(tmp_path: Path) -> None:
    path = _tar(tmp_path / "plain.tar", {"huge.csv": os.urandom(64 * 1024), "log.csv": CSV}, "w")
    limits = DecompressionLimits(max_bytes=16 * 1024, max_ratio=0, max_entries=10)

    listing = inspect_archive(path, limits=limits)

    assert listing is not None
    huge = listing.get("huge.csv")
    assert huge is not None and huge.unsafe
    assert [e.path for e in listing.importable] == ["log.csv"]
    assert _extract(path, "log.csv", limits) == CSV
    with pytest.raises(DecompressionLimitError):
        _extract(path, "huge.csv", limits)


def test_oversized_pax_header_is_refused_before_it_is_buffered(tmp_path: Path) -> None:
    """tarfile would read this header into memory whole - a few KB of gzip
    standing in for gigabytes of RAM."""
    payload = b"99999 comment=" + b"a" * (5 * 1024 * 1024)
    raw = _tar_header("././@PaxHeader", len(payload), tarfile.XHDTYPE) + _pad(payload)
    raw += _tar_header("log.csv", len(CSV), tarfile.REGTYPE) + _pad(CSV) + bytes(1024)
    path = tmp_path / "pax.tar.gz"
    path.write_bytes(gzip.compress(raw))

    with pytest.raises(DecompressionLimitError, match="metadata"):
        inspect_archive(path, limits=GENEROUS)


def test_gnu_sparse_member_is_listed_but_not_importable(tmp_path: Path) -> None:
    raw = _tar_header("sparse.csv", len(CSV), tarfile.GNUTYPE_SPARSE) + _pad(CSV)
    raw += _tar_header("log.csv", len(CSV), tarfile.REGTYPE) + _pad(CSV) + bytes(1024)
    path = tmp_path / "sparse.tar"
    path.write_bytes(raw)

    listing = inspect_archive(path, limits=GENEROUS)

    assert listing is not None
    by_path = {e.path: e for e in listing.entries}
    assert not by_path["sparse.csv"].importable
    assert by_path["log.csv"].importable
    assert _extract(path, "log.csv") == CSV


def test_damaged_tars_fail_cleanly(tmp_path: Path) -> None:
    path = _tar(tmp_path / "x.tar", {"a.csv": CSV, "b.csv": CSV}, mode="w")
    data = bytearray(path.read_bytes())
    data[1024 + 10] ^= 0xFF  # second header: breaks its checksum
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="checksum"):
        inspect_archive(path, limits=GENEROUS)

    full = _tar(tmp_path / "y.tar.gz", {"a.csv": os.urandom(64 * 1024), "b.csv": CSV}).read_bytes()
    cut = tmp_path / "cut.tar.gz"
    cut.write_bytes(full[: len(full) // 2])
    with pytest.raises(ValueError, match=r"truncated|damaged"):
        inspect_archive(cut, limits=GENEROUS)


def test_tar_number_fields_are_validated(tmp_path: Path) -> None:
    header = bytearray(_tar_header("log.csv", len(CSV), tarfile.REGTYPE))
    header[124:136] = b"zz\x00" + bytes(9)  # size: not octal
    header[148:156] = b"        "
    header[148:155] = f"{sum(header):06o}\x00".encode()
    path = tmp_path / "bad-number.tar"
    path.write_bytes(bytes(header) + _pad(CSV) + bytes(1024))
    with pytest.raises(ValueError, match="bad number field"):
        inspect_archive(path, limits=GENEROUS)

    # GNU base-256: a size no seek() could even express.
    header[124:136] = b"\x80" + b"\xff" * 11
    header[148:156] = b"        "
    header[148:155] = f"{sum(header):06o}\x00".encode()
    path.write_bytes(bytes(header) + _pad(CSV) + bytes(1024))
    with pytest.raises(ValueError, match="implausible size"):
        inspect_archive(path, limits=GENEROUS)


def _zip_lzma(path: Path, dictionary: int | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_LZMA) as zf:
        zf.writestr("log.csv", CSV * 100)
        zf.writestr("other.csv", CSV)
    if dictionary is not None:
        data = bytearray(path.read_bytes())
        name_len, extra_len = struct.unpack_from("<HH", data, 26)
        # LZMA properties follow a 4-byte header: lc/lp/pb, then the dictionary size.
        struct.pack_into("<L", data, 30 + name_len + extra_len + 5, dictionary)
        path.write_bytes(bytes(data))
    return path


def test_zip_lzma_members_decode_within_the_memory_cap(tmp_path: Path) -> None:
    path = _zip_lzma(tmp_path / "lzma.zip")
    listing = inspect_archive(path, limits=GENEROUS)
    assert listing is not None and [e.path for e in listing.importable] == ["log.csv", "other.csv"]
    assert _extract(path, "log.csv") == CSV * 100


def test_zip_lzma_member_with_a_huge_dictionary_is_refused_unread(tmp_path: Path) -> None:
    """zipfile would hand that dictionary size straight to the decoder, with no
    memory limit - a few hundred KB of upload growing into gigabytes of RAM."""
    path = _zip_lzma(tmp_path / "lzma-bomb.zip", dictionary=1024**3)

    listing = inspect_archive(path, limits=GENEROUS)

    assert listing is not None
    entry = listing.get("log.csv")
    assert entry is not None and entry.unsafe and "LZMA dictionary" in (entry.reason or "")
    assert [e.path for e in listing.importable] == ["other.csv"]
    with pytest.raises(DecompressionLimitError, match="LZMA dictionary"):
        _extract(path, "log.csv")


@pytest.mark.parametrize(
    ("name", "compress"),
    [("log.xml.gz", gzip.compress), ("log.json.bz2", bz2.compress), ("log.xml.xz", lzma.compress)],
)
def test_damaged_compressed_uploads_are_a_clean_value_error(
    tmp_path: Path, name: str, compress: Callable[[bytes], bytes]
) -> None:
    from mate.api.ingest.detect import detect_format, sniff_format

    whole = compress(b'<log><trace><event name="x"/></trace></log>' * 2000)
    truncated = tmp_path / f"cut-{name}"
    truncated.write_bytes(whole[: len(whole) // 2])
    corrupt = tmp_path / f"bad-{name}"
    corrupt.write_bytes(whole[:16] + bytes(len(whole) - 16))

    for path in (truncated, corrupt):
        with pytest.raises(ValueError, match="damaged or truncated"):
            sniff_format(path, detect_format(name), filename=name)


def test_disk_errors_are_not_mistaken_for_damaged_uploads() -> None:
    with pytest.raises(OSError) as caught, reading("The upload"):
        raise OSError(errno.ENOSPC, "No space left on device")
    assert not isinstance(caught.value, ValueError)


def test_xz_decoder_memory_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Below the 8 MiB dictionary of the default preset.
    monkeypatch.setattr(compression, "LZMA_MEMLIMIT", 1024 * 1024)
    path = tmp_path / "log.csv.xz"
    path.write_bytes(lzma.compress(CSV))
    with (
        pytest.raises(DecompressionLimitError, match="memory"),
        decompressed(path, limits=GENEROUS),
    ):
        pass


def test_xz_reader_matches_the_stdlib_on_concatenated_streams(tmp_path: Path) -> None:
    tail = b"c2,C,2024-01-02 10:00:00\n"
    path = tmp_path / "log.csv.xz"
    path.write_bytes(lzma.compress(CSV) + lzma.compress(tail) + b"trailing garbage")
    with decompressed(path, limits=GENEROUS) as plain:
        assert plain.read_bytes() == CSV + tail


@pytest.mark.parametrize(
    ("size", "text"),
    [(512, "512 B"), (1536, "1.5 KiB"), (64 * 1024**2, "64 MiB"), (10 * 1024**3, "10 GiB")],
)
def test_human_bytes(size: int, text: str) -> None:
    assert human_bytes(size) == text
