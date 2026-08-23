#!/usr/bin/env python3
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok
"""Compare a live WCD934X register dump against downstream's init tables.

The vendor kernel applies three {reg, mask, val} tables at codec init
(tavil_codec_reg_defaults, tavil_codec_reg_init_common_val,
tavil_codec_reg_init_1_1_val).  Mainline's wcd934x has no equivalent, so any
entry whose bits do not match on the running device is an init write mainline
never performs.

Usage: tk-cmp-vendor-regs.py <regdump.txt> [table ...]
  regdump comes from /sys/kernel/debug/regmap/<slim-dev>/registers ("addr: val")
"""
import re
import sys

DOWN = "ref/downstream-wahoo"
HDR = f"{DOWN}/include/linux/mfd/wcd934x/registers.h"
SRC = f"{DOWN}/sound/soc/codecs/wcd934x/wcd934x.c"
DEFAULT_TABLES = [
    "tavil_codec_reg_defaults",
    "tavil_codec_reg_init_common_val",
    "tavil_codec_reg_init_1_1_val",
]


def regmap():
    h = open(HDR).read()
    return {n: int(v, 16) for n, v in
            re.findall(r"#define\s+(WCD934X_\w+)\s+(0x[0-9a-fA-F]+)", h)}


def table(src, name):
    m = re.search(rf"{name}\[\]\s*=\s*\{{(.*?)\n\}};", src, re.S)
    if not m:
        return []
    return re.findall(r"\{\s*(WCD934X_\w+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\}",
                      m.group(1))


def live(path):
    out = {}
    for line in open(path):
        m = re.match(r"([0-9a-f]{4}):\s*([0-9a-f]{2})", line)
        if m:
            out[int(m.group(1), 16)] = int(m.group(2), 16)
    return out


def main(dump, names):
    regs, src, dev = regmap(), open(SRC).read(), live(dump)
    bad = 0
    for name in names:
        entries = table(src, name)
        print(f"=== {name}: {len(entries)} entries ===")
        for rname, mask, val in entries:
            addr = regs.get(rname)
            if addr is None or addr not in dev:
                print(f"  ?? {rname} not resolvable/absent from dump")
                continue
            mask, val, got = int(mask, 16), int(val, 16), dev[addr]
            if got & mask != val & mask:
                bad += 1
                print(f"  MISMATCH {rname} (0x{addr:04x}) live=0x{got:02x} "
                      f"mask=0x{mask:02x} want=0x{val:02x} "
                      f"-> bits 0x{(got ^ val) & mask:02x} wrong")
    print(f"\n{bad} register(s) differ from the vendor's init state")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:] or DEFAULT_TABLES))
