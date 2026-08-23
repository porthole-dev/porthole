#!/usr/bin/env python3
# scope: device:google-taimen
"""Program Easel's inbound iATU so BAR2 maps its peripheral window, then read
the MIPI TOP block.

HANDOFF §41: Easel bridges both cameras' MIPI to the SoC. Its MIPI mux lives at
0x04015000 in Easel address space, far outside BAR2's 8 MB, so BAR2 has to be
retargeted first -- downstream does this in mnh_check_iatu_bar2() via
mnh_pcie_config_write(), which is PCI *config* space (0x900+), not MMIO.

A raw BAR2 read with the window unprogrammed hangs the bus and watchdogs the
phone, so every step is logged to /tmp/easel-iatu.log with fsync -- if this
reboots the box, that file says how far it got.

Run ON the phone, as root.
"""
import os
import struct
import sys

DEV = "/sys/bus/pci/devices/0000:01:00.0/"
PERIPH_BASE = 0x04000000
MIPI_TOP = 0x04015000

IATU_VIEWPORT = 0x900
IATU_REGION_CTRL_1 = 0x904
IATU_REGION_CTRL_2 = 0x908
IATU_LWR_TARGET = 0x918
IATU_UPR_TARGET = 0x91C
IATU_INBOUND = 0x80000000
IATU_BAR_MODE = 0xC0000000

log = open("/tmp/easel-iatu.log", "w")


def p(s):
    print(s)
    log.write(s + "\n")
    log.flush()
    os.fsync(log.fileno())


def main():
    fd = os.open(DEV + "config", os.O_RDWR)
    cw = lambda o, v: os.pwrite(fd, struct.pack("<I", v), o)
    cr = lambda o: struct.unpack("<I", os.pread(fd, 4, o))[0]

    ids = cr(0)
    p("endpoint %04x:%04x" % (ids & 0xFFFF, ids >> 16))

    p("programming inbound iATU: BAR2 -> 0x%08x" % PERIPH_BASE)
    cw(IATU_VIEWPORT, IATU_INBOUND | 1)
    cw(IATU_LWR_TARGET, PERIPH_BASE)
    cw(IATU_UPR_TARGET, 0)
    cw(IATU_REGION_CTRL_1, 0)
    cw(IATU_REGION_CTRL_2, IATU_BAR_MODE | (2 << 8))

    p("  ctrl2   = 0x%08x (bit31 = region enabled)" % cr(IATU_REGION_CTRL_2))
    p("  target  = 0x%08x" % cr(IATU_LWR_TARGET))
    p("  viewport= 0x%08x" % cr(IATU_VIEWPORT))

    if len(sys.argv) > 1 and sys.argv[1] == "--read-bar":
        p("mapping BAR2 and reading MIPI TOP @ +0x%x ..." %
          (MIPI_TOP - PERIPH_BASE))
        import mmap
        bfd = os.open(DEV + "resource2", os.O_RDWR | os.O_SYNC)
        off = MIPI_TOP - PERIPH_BASE
        page = off & ~0xFFF
        m = mmap.mmap(bfd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ,
                      offset=page)
        base = off & 0xFFF
        vals = " ".join("%08x" % struct.unpack_from("<I", m, base + 4 * i)[0]
                        for i in range(8))
        p("  MIPI_TOP: %s" % vals)
    p("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
