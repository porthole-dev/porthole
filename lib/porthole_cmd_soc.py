# SPDX-License-Identifier: MIT
"""`porthole soc` -- who else runs this silicon, and what did they answer?

The single highest-leverage question when starting a port is "has someone
already done this SoC". If they have, their `deviceinfo` contains boot image
offsets that are *known to boot*, their APKBUILD names the soc-* package that
already exists, and their kernel tree has a device tree you can read.

Guessing those values costs days. pmaports has 664 devices on disk and the
answer takes 55ms.
"""
from __future__ import annotations

import pathlib

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE
import porthole_pmaports as pmap

# deviceinfo keys worth copying from a sibling, and why. A wrong value in any
# of these produces a device that does not boot and gives no diagnostic, which
# is exactly the class of question a sibling can answer for free.
INHERITABLE = [
    ("flash_offset_base", "boot image layout -- do NOT guess"),
    ("flash_offset_kernel", "boot image layout -- do NOT guess"),
    ("flash_offset_ramdisk", "boot image layout -- do NOT guess"),
    ("flash_offset_tags", "boot image layout -- do NOT guess"),
    ("flash_offset_second", "boot image layout"),
    ("flash_pagesize", "boot image layout"),
    ("header_version", "mkbootimg header version"),
    ("flash_method", "how images reach the device"),
    ("arch", "package architecture"),
    ("generate_bootimg", "does this platform take an android boot image"),
    ("flash_sparse", "sparse image support"),
    ("rootfs_image_sector_size", "storage sector size"),
    ("append_dtb", "does the kernel need the DTB appended"),
    ("bootimg_qcdt", "Qualcomm device tree table in the boot image"),
]


def _load(ctx):
    pmaports = pmap.find_pmaports(ctx.cfg)
    if not pmaports:
        raise Bail("no pmaports checkout found", EX_FAIL,
                   "set PORTHOLE_PMAPORTS, or run `pmbootstrap init` once so "
                   "it clones one")
    devices = pmap.load_devices(pmaports)
    if not devices:
        raise Bail(f"{pmaports}/device contains no device packages", EX_FAIL)
    return pmaports, devices


def _my_soc(ctx, devices) -> str:
    """This checkout's SoC: the profile first, then the pmaports entry."""
    soc = ctx.cfg.get("PORTHOLE_SOC", "")
    codename = ctx.cfg.get("PORTHOLE_CODENAME") or ctx.cfg.get("PORTHOLE_DEVICE", "")
    if soc:
        # The profile says "msm8998"; pmaports says "qcom-msm8998". Match either.
        for dev in devices:
            if dev.codename == codename and dev.soc:
                return dev.soc
        for dev in devices:
            if dev.soc.endswith("-" + soc):
                return dev.soc
        return soc
    for dev in devices:
        if dev.codename == codename:
            return dev.soc
    return ""


def cmd_soc(args, ctx) -> int:
    pmaports, devices = _load(ctx)

    soc = args.name or _my_soc(ctx, devices)
    if not soc:
        raise Bail("could not determine a SoC", EX_FAIL,
                   "name one (`porthole soc show qcom-msm8998`), set PORTHOLE_SOC "
                   "in the profile, or `porthole soc list`")

    matches = [d for d in devices if d.soc == soc]
    if not matches:
        near = sorted({d.soc for d in devices if soc.split("-")[-1] in d.soc})
        raise Bail(f"no devices on SoC {soc!r}", EX_FAIL,
                   f"did you mean: {', '.join(near[:5])}?" if near
                   else "`porthole soc list` shows every family")

    mine = ctx.cfg.get("PORTHOLE_CODENAME") or ctx.cfg.get("PORTHOLE_DEVICE", "")
    sibs = [d for d in matches if d.codename != mine]

    payload = {
        "soc": soc,
        "pmaports": str(pmaports),
        "device_count": len(matches),
        "mine": mine if any(d.codename == mine for d in matches) else "",
        "devices": [d.as_dict() for d in matches],
        "best_reference": sibs[0].as_dict() if sibs else None,
        "soc_package": f"soc-{soc}",
    }

    def render():
        o = ctx.out
        o.heading(f"{soc} — {len(matches)} device(s) in pmaports")
        o.blank()
        width = max(len(d.codename) for d in matches)
        for dev in matches:
            mark = o.mark("active", dev.codename == mine)
            cat = {"main": "green", "community": "cyan",
                   "testing": "yellow", "archived": "grey"}.get(dev.category, "grey")
            o(f" {mark} {dev.codename:<{width}}  "
              f"{o.paint(f'{dev.category:<10}', cat)} "
              f"{dev.year:<5} {dev.name}")
        o.blank()
        if sibs:
            best = sibs[0]
            o.heading("best reference")
            o(f"  {best.codename} ({best.category}) — "
              f"{len(best.info)} deviceinfo keys already answered")
            o.blank()
            o.hint(f"porthole soc inherit {best.codename}   "
                   f"# the values worth copying")
            o.hint(f"porthole soc diff {best.codename}      "
                   f"# how yours differs")
            o(f"  {o.paint(f'read: {best.path}', 'grey')}")
        else:
            o("No sibling on this SoC. You are first — "
              "`porthole dts new` scaffolds from the closest relative instead.")
        o.blank()
        o(f"  SoC package: {o.paint('soc-' + soc, 'bold')}  "
          f"(depend on this in your device APKBUILD)")

    return ctx.emit(payload, render)


def _list_families(args, ctx, devices) -> int:
    families = pmap.soc_families(devices)
    rows = [{"soc": soc,
             "devices": len(devs),
             "best_category": min(d.category for d in devs),
             "codenames": [d.codename for d in devs]}
            for soc, devs in families.items()]
    rows.sort(key=lambda r: (-r["devices"], r["soc"]))

    def render():
        width = max(len(r["soc"]) for r in rows)
        for row in rows:
            ctx.out(f"  {row['soc']:<{width}}  {row['devices']:>3} devices  "
                    f"{ctx.out.paint(', '.join(row['codenames'][:4]), 'grey')}"
                    f"{ctx.out.paint(' ...', 'grey') if row['devices'] > 4 else ''}")
        ctx.out.blank()
        ctx.out(f"{len(rows)} SoC families, "
                f"{sum(r['devices'] for r in rows)} devices.")

    return ctx.emit(rows, render)


def cmd_inherit(args, ctx, devices, reference: str) -> int:
    """Print the values worth copying from a sibling, as porthole profile keys."""
    ref = next((d for d in devices if d.codename == reference), None)
    if ref is None:
        raise Bail(f"no device {reference!r} in pmaports", EX_FAIL,
                   "`porthole soc` lists the siblings")

    # Map pmOS deviceinfo keys onto porthole profile keys, so the output can be
    # pasted straight into a profile rather than translated by hand.
    PROFILE_KEY = {
        "flash_offset_base": "PORTHOLE_BOOTIMG_BASE",
        "flash_offset_kernel": "PORTHOLE_BOOTIMG_KERNEL_OFFSET",
        "flash_offset_ramdisk": "PORTHOLE_BOOTIMG_RAMDISK_OFFSET",
        "flash_offset_tags": "PORTHOLE_BOOTIMG_TAGS_OFFSET",
        "flash_pagesize": "PORTHOLE_BOOTIMG_PAGESIZE",
        "header_version": "PORTHOLE_BOOTIMG_HEADER_VERSION",
        "arch": "PORTHOLE_ARCH",
    }

    found = [(k, ref.info[k], why) for k, why in INHERITABLE if k in ref.info]
    payload = {"reference": ref.as_dict(),
               "values": [{"deviceinfo_key": k, "value": v,
                           "profile_key": PROFILE_KEY.get(k, ""), "why": why}
                          for k, v, why in found]}

    def render():
        o = ctx.out
        o.heading(f"inheritable from {ref.codename} ({ref.category})")
        o.blank()
        o(o.paint("  These are known-good on a device that boots. Copying them "
                  "is not\n  cutting corners -- guessing a boot image offset is "
                  "how you get a\n  device that fails silently with no "
                  "diagnostic at all.", "grey"))
        o.blank()
        width = max((len(k) for k, _, _ in found), default=10)
        for key, value, why in found:
            o(f"  deviceinfo_{key:<{width}} = {o.paint(value, 'bold')}")
            o(f"  {'':<{width + 12}}   {o.paint(why, 'grey')}")
        o.blank()
        o.heading("as porthole profile keys")
        for key, value, _ in found:
            pk = PROFILE_KEY.get(key)
            if pk:
                o(o.paint(f'  {pk}="{value}"', "cyan"))
        o.blank()
        o(o.paint("  STILL VERIFY these against your own device's downstream "
                  "mkbootimg\n  args or an unpacked stock boot.img. Same SoC "
                  "does not guarantee same\n  board layout.", "yellow"))

    return ctx.emit(payload, render)


def cmd_diff(args, ctx, devices, reference: str) -> int:
    """Diff my deviceinfo against a sibling's: what have I not answered yet?"""
    ref = next((d for d in devices if d.codename == reference), None)
    if ref is None:
        raise Bail(f"no device {reference!r} in pmaports", EX_FAIL)
    mine_name = ctx.cfg.get("PORTHOLE_CODENAME") or ctx.cfg.get("PORTHOLE_DEVICE", "")
    mine = next((d for d in devices if d.codename == mine_name), None)
    if mine is None:
        raise Bail(f"no pmaports device package for {mine_name!r}", EX_FAIL,
                   "the diff compares two pmaports entries; create yours first")

    keys = sorted(set(mine.info) | set(ref.info))
    rows = []
    for key in keys:
        a, b = mine.info.get(key), ref.info.get(key)
        if a == b:
            continue
        rows.append({"key": key, "mine": a, "theirs": b,
                     "status": "missing" if a is None else
                               "extra" if b is None else "differs"})

    payload = {"mine": mine.codename, "reference": ref.codename, "rows": rows}

    def render():
        o = ctx.out
        o.heading(f"{mine.codename}  vs  {ref.codename} ({ref.category})")
        o.blank()
        if not rows:
            o("  identical deviceinfo.")
            return
        width = max(len(r["key"]) for r in rows)
        for r in rows:
            colour = {"missing": "yellow", "extra": "cyan",
                      "differs": "magenta"}[r["status"]]
            status = r["status"]
            o(f"  {o.paint(f'{status:<8}', colour)} "
              f"deviceinfo_{r['key']:<{width}}")
            if r["mine"] is not None:
                o(f"           mine:   {r['mine']}")
            if r["theirs"] is not None:
                o(f"           theirs: {r['theirs']}")
        o.blank()
        missing = sum(1 for r in rows if r["status"] == "missing")
        o(f"  {missing} key(s) they answer and you do not.")

    return ctx.emit(payload, render)


ACTIONS = ("show", "list", "inherit", "diff")


def dispatch(args, ctx) -> int:
    action = args.action or "show"
    if action in ("inherit", "diff"):
        if not args.name:
            raise Bail(f"{action} needs a codename", EX_USAGE,
                       f"porthole soc {action} <codename>   "
                       f"(`porthole soc list` to find one)")
        _, devices = _load(ctx)
        fn = cmd_inherit if action == "inherit" else cmd_diff
        return fn(args, ctx, devices, args.name)
    if action == "list":
        _, devices = _load(ctx)
        return _list_families(args, ctx, devices)
    return cmd_soc(args, ctx)


SPEC = {
    "verb": "soc",
    "order": 26,
    "help": "find devices sharing your SoC and inherit their working values",
    "description": (
        "The highest-leverage question when starting a port: has someone\n"
        "already done this silicon? If so their deviceinfo holds boot image\n"
        "offsets that are known to boot, and their kernel tree holds a device\n"
        "tree you can read. Guessing those costs days; this costs 55ms."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "choices": list(ACTIONS),
                      "help": "show | list | inherit | diff  (default show)"}),
        (["name"], {"nargs": "?", "metavar": "SOC|CODENAME",
                    "help": "show: a SoC (default yours). "
                            "inherit/diff: a sibling codename"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": dispatch,
    "examples": [
        "porthole soc",
        "porthole soc show qcom-sdm845",
        "porthole soc list",
        "porthole soc inherit oneplus-cheeseburger",
        "porthole soc diff xiaomi-sagit",
    ],
}
