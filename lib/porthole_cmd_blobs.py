# SPDX-License-Identifier: MIT
"""`porthole blobs` -- get at vendor firmware during bring-up.

postmarketOS packages firmware by fetching it in the aport. That is the right
answer for shipping, and it is not the question this verb answers. Before you
can write that aport you have to know what is *in* the vendor image: which
blobs exist, what names they have, which mainline driver consumes each one, and
where it expects to find them. That is a development problem and it comes
first.

Everything here works without root and without mounting anything:

- Android sparse images are decoded directly, so no `simg2img` needed.
- ext4 images are read through `debugfs`, which does not mount and does not
  need privileges. A loop mount would need root, and root is what this project
  spends its time trying not to require.

The rescue note: the sparse decoder began life as a 14-line untracked script in
one person's `blobs/` directory. It survived by luck. Anything worth using
twice belongs in the toolkit.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import zipfile

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE

SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4

# Vendor blob -> what consumes it in mainline, and where it must land.
#
# This mapping is the genuinely transferable part of a blob extraction: the
# file names repeat across Qualcomm devices, and knowing that `wlanmdsp.mbn`
# is what ath10k_snoc blocks on saves the same afternoon on every one of them.
FIRMWARE_MAP = [
    (r"^wlanmdsp\.mbn$", "wifi", "ath10k_snoc",
     "ath10k/WCN3990/hw1.0/", "the file ath10k_snoc blocks on"),
    (r"^bdwlan\.(bin|b\d+)$", "wifi", "ath10k_snoc",
     "ath10k/WCN3990/hw1.0/",
     "board data -- board-2.bin is GENERATED from these, not shipped"),
    (r"^board-2\.bin$|^firmware-5\.bin$", "wifi", "ath10k",
     "ath10k/WCN3990/hw1.0/",
     "usually generated with qca-swiss-army-knife, not present in vendor.img"),
    (r"^a\d+_zap\.(mdt|b\d+)$", "gpu", "msm/adreno", "qcom/",
     "zap shader -- the GPU will not come out of secure mode without it"),
    (r"^a\d+_gpmu\.fw2$", "gpu", "msm/adreno", "qcom/",
     "GPU power-management microcode; the GPU will not clock without it"),
    (r"^a\d+_sqe\.fw$|^a\d+_pfp\.fw$|^a\d+_pm4\.fw$", "gpu", "msm/adreno",
     "qcom/", "GPU command-processor microcode"),
    (r"^adsp\.(mdt|b\d+|mbn)$", "audio", "q6v5_pas (adsp)", "qcom/",
     "audio DSP -- no sound without it"),
    (r"^slpi(_v\d+)?\.(mdt|b\d+|mbn)$", "sensors", "q6v5_pas (slpi)", "qcom/",
     "sensor DSP -- accelerometer, gyro and proximity route through it"),
    (r"^cdsp\.(mdt|b\d+|mbn)$", "compute", "q6v5_pas (cdsp)", "qcom/",
     "compute DSP -- camera and ML offload on newer SoCs"),
    (r"^venus\.(mdt|b\d+|mbn)$", "video", "venus", "qcom/venus-*/",
     "hardware video encode/decode; software codecs work without it"),
    (r"^modem\.(mdt|b\d+|mbn)$|^mba\.mbn$", "modem", "q6v5_mss", "qcom/",
     "NOT in vendor.img -- lives on the modem partition"),
    (r"^cpe_\d+\.(mdt|b\d+)$", "audio", "WCD codec", "qcom/",
     "codec DSP -- needed for always-on voice paths, not for basic audio"),
    (r"\.jsn$", "protection domains", "pd-mapper", "next to the firmware",
     "pd-mapper needs these; modemuw.jsn is the one wifi waits on"),
    (r"^tz\w*\.(mbn|mdt)$", "trustzone", "(not used by mainline)", "-",
     "stock TZ image; mainline runs the one already flashed"),
    (r"^km\d*\.(mbn|mdt)$|^keymaster", "keymaster", "(not used by mainline)",
     "-", "Android keystore; no mainline consumer"),
]


# ---------------------------------------------------------------- sparse ----

def sparse_to_raw(src: pathlib.Path, dst: pathlib.Path,
                  progress=None) -> dict:
    """Decode an Android sparse image to a raw one.

    The format is a 28-byte header then a chunk table. Four chunk types:
    RAW copies bytes through, FILL repeats a 4-byte pattern, DONT_CARE is a
    hole, CRC carries a checksum we skip.

    Written out rather than shelling to `simg2img` because a bring-up host
    often does not have android-tools, and "install a package first" is a poor
    answer when the image is right there.
    """
    with open(src, "rb") as fh:
        header = fh.read(28)
        if len(header) < 28:
            raise Bail(f"{src} is too short to be a sparse image", EX_FAIL)
        (magic, major, minor, file_hdr_sz, chunk_hdr_sz, block_sz,
         total_blocks, total_chunks, _checksum) = struct.unpack("<IHHHHIIII",
                                                                header)
        if magic != SPARSE_MAGIC:
            raise Bail(f"{src} is not an Android sparse image "
                       f"(magic {magic:#x}, expected {SPARSE_MAGIC:#x})",
                       EX_FAIL,
                       "if it is already raw, use it directly; "
                       "`file` will tell you which it is")
        if major != 1:
            raise Bail(f"unsupported sparse version {major}.{minor}", EX_FAIL)

        fh.seek(file_hdr_sz)
        written = 0
        counts = {"raw": 0, "fill": 0, "dont_care": 0, "crc": 0}
        with open(dst, "wb") as out:
            for index in range(total_chunks):
                chunk = fh.read(chunk_hdr_sz)
                if len(chunk) < 12:
                    break
                ctype, _res, chunk_blocks, _total_sz = struct.unpack(
                    "<HHII", chunk[:12])
                span = chunk_blocks * block_sz
                if ctype == CHUNK_RAW:
                    counts["raw"] += 1
                    # Stream it: a vendor image is gigabytes and reading a
                    # chunk whole is how this runs a machine out of memory.
                    remaining = span
                    while remaining:
                        piece = fh.read(min(remaining, 4 << 20))
                        if not piece:
                            break
                        out.write(piece)
                        remaining -= len(piece)
                    written += span - remaining
                elif ctype == CHUNK_FILL:
                    counts["fill"] += 1
                    pattern = fh.read(4)
                    out.write(pattern * (span // 4))
                    written += span
                elif ctype == CHUNK_DONT_CARE:
                    counts["dont_care"] += 1
                    # A hole. Seek rather than write zeros so the output stays
                    # sparse on disk -- these are frequently most of the image.
                    out.seek(span, os.SEEK_CUR)
                    written += span
                elif ctype == CHUNK_CRC:
                    counts["crc"] += 1
                    fh.read(4)
                else:
                    raise Bail(f"unknown sparse chunk type {ctype:#x} at "
                               f"chunk {index}", EX_FAIL)
                if progress:
                    progress(index + 1, total_chunks)
            # DONT_CARE at the end leaves the file short unless we extend it.
            out.truncate(total_blocks * block_sz)

    return {"blocks": total_blocks, "block_size": block_sz,
            "chunks": total_chunks, "bytes": total_blocks * block_sz,
            "chunk_types": counts}


def cmd_unsparse(args, ctx) -> int:
    src = pathlib.Path(args.path or "")
    if not src.is_file():
        raise Bail(f"no such file: {src}", EX_USAGE,
                   "porthole blobs unsparse <sparse.img> [--out raw.img]")
    dst = pathlib.Path(args.out) if args.out else src.with_suffix(".raw.img")
    info = sparse_to_raw(src, dst)

    def render():
        ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} {dst}")
        ctx.out.kv("size", f"{info['bytes'] / 2**20:.1f} MiB", 12)
        ctx.out.kv("chunks", str(info["chunks"]), 12)
        ctx.out.blank()
        ctx.out.hint(f"porthole blobs ls {dst}", "without mounting it")

    return ctx.emit({"source": str(src), "output": str(dst), **info}, render)


# ------------------------------------------------------------------ unpack --

def cmd_unpack(args, ctx) -> int:
    """Walk a factory zip down to the images inside it.

    Vendor factory images nest: the outer zip holds an `image-*.zip`, which
    holds boot.img, dtbo.img, vendor.img and the rest. People lose time to that
    structure alone.
    """
    src = pathlib.Path(args.path or "")
    if not src.is_file():
        raise Bail(f"no such file: {src}", EX_USAGE,
                   "porthole blobs unpack <factory.zip>")
    outdir = pathlib.Path(args.out or src.parent / (src.stem + "-unpacked"))

    found, nested = [], []
    try:
        with zipfile.ZipFile(src) as zf:
            for name in zf.namelist():
                leaf = pathlib.PurePosixPath(name).name
                if leaf.endswith(".zip"):
                    nested.append(name)
                elif leaf.endswith((".img", ".bin", ".mbn", ".elf")):
                    found.append(name)
    except zipfile.BadZipFile:
        raise Bail(f"{src} is not a zip", EX_FAIL) from None

    if args.dry_run:
        payload = {"archive": str(src), "images": found, "nested_zips": nested}

        def render_list():
            o = ctx.out
            if nested:
                o.heading("nested archives")
                for name in nested:
                    o(f"  {name}")
                o.blank()
                o(o.paint("  A factory image nests: the outer zip holds an\n"
                          "  image-*.zip which holds the partitions. Unpack "
                          "both.", "grey"))
                o.blank()
            if found:
                o.heading("images")
                for name in found:
                    o(f"  {name}")
        return ctx.emit(payload, render_list)

    outdir.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(src) as zf:
        for name in nested + found:
            zf.extract(name, outdir)
            extracted.append(str(outdir / name))
    # One level of nesting is the convention; going deeper is not.
    for name in nested:
        inner = outdir / name
        try:
            with zipfile.ZipFile(inner) as zf:
                zf.extractall(inner.parent)
                extracted += [str(inner.parent / n) for n in zf.namelist()]
        except zipfile.BadZipFile:
            continue

    def render():
        ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} "
                f"{len(extracted)} file(s) into {outdir}")
        ctx.out.blank()
        ctx.out.hint(f"porthole blobs ls {outdir}/vendor.img")

    return ctx.emit({"archive": str(src), "outdir": str(outdir),
                     "files": extracted}, render)


# ------------------------------------------------------- read without root --

def _debugfs(image: pathlib.Path, command: str, timeout=120):
    if not shutil.which("debugfs"):
        raise Bail("debugfs is not installed", EX_FAIL,
                   "it is in e2fsprogs -- and it reads ext4 WITHOUT mounting, "
                   "which is why this uses it instead of a loop mount that "
                   "would need root")
    proc = subprocess.run(["debugfs", "-R", command, str(image)],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise Bail(f"debugfs failed on {image.name}: "
                   f"{proc.stderr.strip().splitlines()[-1:] or '?'}", EX_FAIL,
                   "is it a raw ext4 image? A sparse one needs "
                   "`porthole blobs unsparse` first")
    return proc.stdout


LS_RE = re.compile(r"^\s*(\d+)\s+\(\d+\)\s+\d+\s+\d+\s+"
                   r"(\d+)\s+\S+\s+\S+\s+\S+\s+(.*)$")


def cmd_ls(args, ctx) -> int:
    image = pathlib.Path(args.path or "")
    if not image.is_file():
        raise Bail(f"no such file: {image}", EX_USAGE,
                   "porthole blobs ls <image> [--dir /firmware]")
    directory = args.dir or "/firmware"
    out = _debugfs(image, f"ls -l {directory}")

    entries = []
    for line in out.splitlines():
        m = LS_RE.match(line)
        if not m:
            continue
        name = m.group(3).strip()
        if name in (".", ".."):
            continue
        entries.append({"name": name, "size": int(m.group(2)),
                        "inode": int(m.group(1))})
    entries.sort(key=lambda e: e["name"])

    def render():
        o = ctx.out
        if not entries:
            o(f"nothing under {directory} in {image.name}")
            o.hint("try --dir /", "to see what is at the root")
            return
        o.heading(f"{image.name}:{directory} — {len(entries)} entries")
        o.blank()
        width = max(len(e["name"]) for e in entries)
        for e in entries:
            size = (f"{e['size'] / 2**20:.1f}M" if e["size"] >= 2**20
                    else f"{e['size'] / 1024:.0f}K" if e["size"] >= 1024
                    else str(e["size"]))
            o(f"  {e['name']:<{width}}  {o.paint(size.rjust(7), 'grey')}")
        o.blank()
        o(o.paint("  Read without mounting -- debugfs needs no root and no "
                  "loop device.", "grey"))
        o.hint("porthole blobs inventory " + str(image))

    return ctx.emit({"image": str(image), "dir": directory,
                     "entries": entries}, render)


def cmd_extract(args, ctx) -> int:
    image = pathlib.Path(args.path or "")
    if not image.is_file():
        raise Bail(f"no such file: {image}", EX_USAGE,
                   "porthole blobs extract <image> --dir /firmware --out DIR")
    directory = args.dir or "/firmware"
    outdir = pathlib.Path(args.out or f"./{image.stem}-{directory.strip('/')}")
    outdir.mkdir(parents=True, exist_ok=True)

    out = _debugfs(image, f"ls -l {directory}")
    names = []
    for line in out.splitlines():
        m = LS_RE.match(line)
        if m and m.group(3).strip() not in (".", ".."):
            names.append(m.group(3).strip())

    if args.match:
        pattern = re.compile(args.match)
        names = [n for n in names if pattern.search(n)]

    got = []
    for name in names:
        target = outdir / name
        try:
            _debugfs(image, f"dump {directory}/{name} {target}")
            if target.is_file():
                got.append(str(target))
        except Bail:
            continue

    def render():
        ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} "
                f"{len(got)} file(s) into {outdir}")
        ctx.out.blank()
        ctx.out.hint(f"porthole blobs inventory --dir-of {outdir}")

    return ctx.emit({"image": str(image), "outdir": str(outdir),
                     "files": got}, render)


# --------------------------------------------------------------- inventory --

def classify(name: str):
    for pattern, subsystem, consumer, dest, note in FIRMWARE_MAP:
        if re.match(pattern, name) or re.search(pattern, name):
            return {"subsystem": subsystem, "consumer": consumer,
                    "destination": dest, "note": note}
    return None


def cmd_inventory(args, ctx) -> int:
    """Map what you found onto mainline consumers and destinations."""
    names: list[tuple[str, int]] = []

    if args.dir_of:
        base = pathlib.Path(args.dir_of)
        if not base.is_dir():
            raise Bail(f"not a directory: {base}", EX_USAGE)
        names = [(p.name, p.stat().st_size) for p in sorted(base.iterdir())
                 if p.is_file()]
    else:
        image = pathlib.Path(args.path or "")
        if not image.is_file():
            raise Bail("give an image or --dir-of DIR", EX_USAGE,
                       "porthole blobs inventory vendor.img")
        out = _debugfs(image, f"ls -l {args.dir or '/firmware'}")
        for line in out.splitlines():
            m = LS_RE.match(line)
            if m and m.group(3).strip() not in (".", ".."):
                names.append((m.group(3).strip(), int(m.group(2))))

    known, unknown = [], []
    for name, size in names:
        hit = classify(name)
        if hit:
            known.append({"file": name, "size": size, **hit})
        else:
            unknown.append({"file": name, "size": size})

    by_subsystem: dict[str, list] = {}
    for row in known:
        by_subsystem.setdefault(row["subsystem"], []).append(row)

    def render():
        o = ctx.out
        o.heading(f"{len(names)} file(s), {len(known)} recognised")
        o.blank()
        for subsystem in sorted(by_subsystem):
            rows = by_subsystem[subsystem]
            o.heading(subsystem)
            o(f"  consumed by {o.paint(rows[0]['consumer'], 'bold')}"
              f"  ->  {rows[0]['destination']}")
            o(f"  {o.paint(rows[0]['note'], 'grey')}")
            for row in rows:
                size = (f"{row['size'] / 2**20:.1f}M" if row["size"] >= 2**20
                        else f"{row['size'] / 1024:.0f}K")
                o(f"    {row['file']:<28} {o.paint(size.rjust(7), 'grey')}")
            o.blank()
        if unknown:
            o.heading(f"unrecognised ({len(unknown)})")
            o(o.paint("  Not necessarily useless -- the map only knows what "
                      "previous ports\n  needed. If one of these turns out to "
                      "matter, that is a brain note.", "grey"))
            for row in unknown[:20]:
                o(f"    {row['file']}")
            if len(unknown) > 20:
                o(f"    ... and {len(unknown) - 20} more")
            o.blank()
        o(o.paint("  For shipping, package these in a firmware-<device> aport "
                  "fetched\n  from a source pmaports accepts. This inventory "
                  "is for development,\n  when you need to know what exists "
                  "before you can write that aport.", "grey"))

    return ctx.emit({"total": len(names), "known": known,
                     "unknown": unknown}, render)


ACTIONS = {"unsparse": cmd_unsparse, "unpack": cmd_unpack, "ls": cmd_ls,
           "extract": cmd_extract, "inventory": cmd_inventory}


def dispatch(args, ctx) -> int:
    action = args.action or "inventory"
    fn = ACTIONS.get(action)
    if not fn:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: {', '.join(ACTIONS)}")
    return fn(args, ctx)


SPEC = {
    "verb": "blobs",
    "order": 30,
    "group": "device",
    "help": "get at vendor firmware during bring-up, without root",
    "description": (
        "pmOS packages firmware by fetching it in the aport. Before you can\n"
        "write that aport you have to know what is IN the vendor image -- which\n"
        "blobs exist, which mainline driver consumes each one, where it expects\n"
        "them. That is a development problem and it comes first.\n\n"
        "Sparse images are decoded directly (no simg2img) and ext4 images are\n"
        "read through debugfs, which does not mount and needs no privileges."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "choices": list(ACTIONS),
                      "help": "unsparse | unpack | ls | extract | inventory"}),
        (["path"], {"nargs": "?", "help": "the image or archive"}),
        (["--out"], {"metavar": "PATH", "help": "output file or directory"}),
        (["--dir"], {"metavar": "PATH",
                     "help": "directory inside the image (default /firmware)"}),
        (["--dir-of"], {"metavar": "DIR",
                        "help": "inventory: classify an already-extracted dir"}),
        (["--match"], {"metavar": "REGEX", "help": "extract: only these names"}),
        (["--dry-run"], {"action": "store_true", "dest": "dry_run",
                         "help": "unpack: list what is inside, extract nothing"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": dispatch,
    "examples": [
        "porthole blobs unpack factory.zip --dry-run",
        "porthole blobs unsparse vendor.img --out vendor.raw.img",
        "porthole blobs ls vendor.raw.img --dir /firmware",
        "porthole blobs extract vendor.raw.img --match 'wlan|bdwlan'",
        "porthole blobs inventory vendor.raw.img",
    ],
}
