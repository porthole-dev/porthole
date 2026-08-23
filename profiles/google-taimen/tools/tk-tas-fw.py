#!/usr/bin/env python3
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Parse a TAS2557 uCDSP firmware image and report what is inside it.

The amp driver cannot make sound without this blob: tas2557_enable() refuses
to run unless a firmware with at least one program and one configuration has
been parsed, because the PLL block and the DSP program exist only in here --
there is no register-init shortcut. So before porting the driver it is worth
knowing the blob is intact and which parts it carries.

Layout, from downstream fw_parse() in tas2557-core.c: magic '5552', then
big-endian u32 fields, a 64-byte DDC name and a NUL-terminated description,
then PLL / program / configuration sections, each a u16 count followed by
items of (64-byte name, NUL description, payload).

    tk-tas-fw.py blobs/work/tas2557/tas2557s_PG21_uCDSP.bin
"""
import struct
import sys

MAGIC = b"\x35\x35\x35\x32"


class R:
    def __init__(self, d):
        self.d, self.p = d, 0

    def u32(self):
        v = struct.unpack_from(">I", self.d, self.p)[0]
        self.p += 4
        return v

    def u16(self):
        v = struct.unpack_from(">H", self.d, self.p)[0]
        self.p += 2
        return v

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def fixed(self, n):
        v = self.d[self.p:self.p + n].split(b"\0")[0].decode("latin1")
        self.p += n
        return v

    def cstr(self):
        e = self.d.index(b"\0", self.p)
        v = self.d[self.p:e].decode("latin1")
        self.p = e + 1
        return v


def block(r, crc):
    r.u32()                       # type
    if crc:
        r.u8(); r.u8(); r.u8(); r.u8()
    n = r.u32()                   # command count
    r.p += n * 4
    return n


def data(r, crc):
    r.fixed(64)
    r.cstr()
    nb = r.u16()
    return [block(r, crc) for _ in range(nb)]


def main(path):
    d = open(path, "rb").read()
    r = R(d)
    assert d[:4] == MAGIC, "bad magic -- not a TAS firmware image"
    r.p = 4
    size, checksum, ppc, fwver, drvver, ts = (r.u32() for _ in range(6))
    ddc = r.fixed(64)
    desc = r.cstr()
    family, device = r.u32(), r.u32()
    crc = drvver >= 0x200  # PPC_DRIVER_CRCCHK: blocks carry checksums at/above this

    print(f"{path}")
    print(f"  size={size} (file {len(d)})  checksum=0x{checksum:08x}")
    print(f"  PPC={ppc:#x} fw={fwver:#x} driver={drvver:#x} crc_fields={crc}")
    print(f"  DDC={ddc!r}")
    print(f"  desc={desc!r}")
    print(f"  family={family} device={device} "
          f"({'TAS2557 Dual Mono' if device == 3 else 'UNEXPECTED'})")

    npll = r.u16()
    plls = []
    for _ in range(npll):
        name = r.fixed(64); r.cstr(); n = block(r, crc)
        plls.append((name, n))
    print(f"  PLLs: {npll}")
    for name, n in plls:
        print(f"    - {name!r}: {n} commands")

    nprog = r.u16()
    progs = []
    for _ in range(nprog):
        name = r.fixed(64); r.cstr(); r.u8(); r.u16()
        progs.append((name, data(r, crc)))
    print(f"  programs: {nprog}")
    for name, blocks in progs:
        print(f"    - {name!r}: {len(blocks)} blocks, {sum(blocks)} commands")

    ncfg = r.u16()
    print(f"  configurations: {ncfg}")
    # Which configuration the driver picks is decided by name+rate, so listing
    # them is the only way to see what else was on offer at 48 kHz.
    for _ in range(ncfg):
        name = r.fixed(64); r.cstr()
        devs = r.u16() if drvver >= 0x300 else 3
        prog, pll = r.u8(), r.u8()
        rate = r.u32()
        blocks = data(r, crc)
        print(f"    - {name!r:52s} devices={devs} program={prog} pll={pll} "
              f"rate={rate} blocks={len(blocks)}")

    ok = npll >= 1 and nprog >= 1 and ncfg >= 1
    print(f"  => {'USABLE' if ok else 'NOT USABLE'} "
          f"(driver needs >=1 program and >=1 configuration)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
