#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Vendor blob tooling: the sparse decoder and the firmware classifier.

The sparse decoder is checked byte-for-byte against images this test builds
itself, covering every chunk type including the combinations that are easy to
get subtly wrong -- a DONT_CARE hole at the end of the file, and a FILL pattern
that is not zero.

That matters because the original was fourteen untracked lines that nothing
verified. It produced correct output on the one image it was ever pointed at,
which is not the same as being correct.

Needs no device, no root and no vendor image.
"""
import pathlib
import struct
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

from porthole_cmd_blobs import (  # noqa: E402
    SPARSE_MAGIC, classify, sparse_to_raw, FIRMWARE_MAP)
from porthole_cli import Bail  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="porthole-blobs-test-"))
BLOCK = 4096


def build_sparse(path, chunks, block_size=BLOCK):
    """Write a sparse image. chunks is a list of (type, payload-or-blocks)."""
    total_blocks = 0
    body = b""
    for kind, value in chunks:
        if kind == "raw":
            blocks = len(value) // block_size
            body += struct.pack("<HHII", 0xCAC1, 0, blocks,
                                12 + len(value)) + value
            total_blocks += blocks
        elif kind == "fill":
            pattern, blocks = value
            body += struct.pack("<HHII", 0xCAC2, 0, blocks, 12 + 4) + pattern
            total_blocks += blocks
        elif kind == "dont_care":
            body += struct.pack("<HHII", 0xCAC3, 0, value, 12)
            total_blocks += value
        elif kind == "crc":
            body += struct.pack("<HHII", 0xCAC4, 0, 0, 12 + 4) + b"\x00" * 4
    header = struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12,
                         block_size, total_blocks, len(chunks), 0)
    path.write_bytes(header + body)
    return total_blocks * block_size


# ------------------------------------------------------------------ sparse --

def test_raw_chunks_round_trip_exactly():
    src, dst = TMP / "raw.simg", TMP / "raw.img"
    payload = bytes(range(256)) * (BLOCK * 2 // 256)
    build_sparse(src, [("raw", payload)])
    sparse_to_raw(src, dst)
    assert dst.read_bytes() == payload, "RAW chunk must copy through verbatim"


def test_fill_chunks_repeat_the_pattern():
    src, dst = TMP / "fill.simg", TMP / "fill.img"
    build_sparse(src, [("fill", (b"\xde\xad\xbe\xef", 1))])
    sparse_to_raw(src, dst)
    assert dst.read_bytes() == b"\xde\xad\xbe\xef" * (BLOCK // 4)


def test_dont_care_becomes_a_hole_of_zeros():
    src, dst = TMP / "dc.simg", TMP / "dc.img"
    build_sparse(src, [("raw", b"A" * BLOCK), ("dont_care", 2)])
    sparse_to_raw(src, dst)
    data = dst.read_bytes()
    assert len(data) == BLOCK * 3, f"got {len(data)}"
    assert data[:BLOCK] == b"A" * BLOCK
    assert data[BLOCK:] == b"\x00" * (BLOCK * 2)


def test_a_trailing_dont_care_still_sizes_the_file():
    """The bug this guards: DONT_CARE is written by seeking, and seeking past
    the end of a file does not extend it. An image ending in a hole comes out
    short, and a short filesystem image fails to mount for reasons that look
    nothing like the cause."""
    src, dst = TMP / "trail.simg", TMP / "trail.img"
    expected = build_sparse(src, [("raw", b"B" * BLOCK), ("dont_care", 5)])
    sparse_to_raw(src, dst)
    assert dst.stat().st_size == expected, (
        f"file is {dst.stat().st_size}, header says {expected}")


def test_crc_chunks_are_skipped_without_corrupting_the_stream():
    src, dst = TMP / "crc.simg", TMP / "crc.img"
    build_sparse(src, [("raw", b"C" * BLOCK), ("crc", None),
                       ("raw", b"D" * BLOCK)])
    sparse_to_raw(src, dst)
    assert dst.read_bytes() == b"C" * BLOCK + b"D" * BLOCK


def test_mixed_image_matches_byte_for_byte():
    src, dst = TMP / "mixed.simg", TMP / "mixed.img"
    build_sparse(src, [
        ("raw", b"H" * BLOCK),
        ("fill", (b"\x01\x02\x03\x04", 2)),
        ("dont_care", 1),
        ("raw", b"T" * BLOCK),
    ])
    sparse_to_raw(src, dst)
    expected = (b"H" * BLOCK
                + b"\x01\x02\x03\x04" * (BLOCK // 4) * 2
                + b"\x00" * BLOCK
                + b"T" * BLOCK)
    assert dst.read_bytes() == expected


def test_reports_metadata_the_caller_can_check():
    src, dst = TMP / "meta.simg", TMP / "meta.img"
    build_sparse(src, [("raw", b"X" * BLOCK), ("dont_care", 3)])
    info = sparse_to_raw(src, dst)
    assert info["blocks"] == 4 and info["block_size"] == BLOCK
    assert info["bytes"] == 4 * BLOCK
    assert info["chunk_types"]["raw"] == 1
    assert info["chunk_types"]["dont_care"] == 1


def test_a_raw_image_is_refused_with_an_explanation():
    """Pointing this at an already-raw image is the most likely mistake, so the
    refusal has to say which it is rather than produce garbage."""
    src, dst = TMP / "notsparse.img", TMP / "out.img"
    src.write_bytes(b"\x00" * 1024)
    try:
        sparse_to_raw(src, dst)
    except Bail as exc:
        assert "not an Android sparse image" in exc.message
        assert "already raw" in exc.hint
    else:
        assert False, "a non-sparse image must be refused"


def test_a_truncated_header_is_refused():
    src, dst = TMP / "short.img", TMP / "out2.img"
    src.write_bytes(b"\x3a\xff\x26\xed")
    try:
        sparse_to_raw(src, dst)
    except Bail as exc:
        assert "too short" in exc.message
    else:
        assert False, "a truncated file must be refused"


def test_an_unknown_chunk_type_is_refused_not_ignored():
    """Silently skipping an unrecognised chunk produces a plausible image with
    a hole in it, which is worse than failing."""
    src, dst = TMP / "bad.simg", TMP / "out3.img"
    body = struct.pack("<HHII", 0xCAFE, 0, 1, 12)
    src.write_bytes(struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12,
                                BLOCK, 1, 1, 0) + body)
    try:
        sparse_to_raw(src, dst)
    except Bail as exc:
        assert "unknown sparse chunk type" in exc.message
    else:
        assert False, "an unknown chunk type must be refused"


# -------------------------------------------------------------- classifier --

def test_classifies_the_blobs_that_matter():
    cases = [
        ("wlanmdsp.mbn", "wifi", "ath10k_snoc"),
        ("bdwlan.bin", "wifi", "ath10k_snoc"),
        ("bdwlan.b09", "wifi", "ath10k_snoc"),
        ("a540_zap.mdt", "gpu", "msm/adreno"),
        ("a540_gpmu.fw2", "gpu", "msm/adreno"),
        ("adsp.mdt", "audio", "q6v5_pas (adsp)"),
        ("slpi_v2.b14", "sensors", "q6v5_pas (slpi)"),
        ("venus.mbn", "video", "venus"),
        ("modem.b27", "modem", "q6v5_mss"),
        ("mba.mbn", "modem", "q6v5_mss"),
        ("modemuw.jsn", "protection domains", "pd-mapper"),
    ]
    for name, subsystem, consumer in cases:
        hit = classify(name)
        assert hit, f"{name} should be recognised"
        assert hit["subsystem"] == subsystem, f"{name}: {hit['subsystem']}"
        assert hit["consumer"] == consumer, f"{name}: {hit['consumer']}"


def test_unknown_files_are_not_forced_into_a_category():
    """A wrong classification is worse than none: it sends someone to package a
    file no mainline driver will ever open."""
    for name in ("README.txt", "some-vendor-thing.so", "libfoo.so"):
        assert classify(name) is None, f"{name} should not be classified"


def test_every_map_entry_has_a_usable_destination():
    for pattern, subsystem, consumer, dest, note in FIRMWARE_MAP:
        assert subsystem and consumer and dest and note, pattern
        # A note that does not say why the file matters is decoration.
        assert len(note) > 10, f"{pattern}: note too thin"


def test_board_files_are_flagged_as_generated():
    """board-2.bin is built from bdwlan.bin with qca-swiss-army-knife, not
    shipped in vendor.img. Someone hunting for it in the image loses an hour."""
    hit = classify("board-2.bin")
    assert hit and "generated" in hit["note"].lower()
    hit = classify("bdwlan.bin")
    assert hit and "GENERATED" in hit["note"]


def test_modem_is_flagged_as_living_elsewhere():
    """Modem firmware is not in vendor.img -- it is on its own partition."""
    hit = classify("modem.mdt")
    assert hit and "NOT in vendor.img" in hit["note"]


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
