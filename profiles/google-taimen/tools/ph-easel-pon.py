#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok
"""Power on Easel's BCM15602 PMIC by hand and see if it answers on i2c.

HANDOFF §41: Easel is an in-line MIPI bridge and both cameras terminate on it.
Nothing can work until it is powered. Its PMIC only responds on i2c once PON
(tlmm 25) is driven high and the chip releases RESETB (tlmm 91, an input we
read back) -- see bcm15602-regulator.c:1327-1366.

TLMM is APPS-owned and tiled; the tile table is copied from ph-pins.py, which
established that plain base+pin*0x1000 addressing reads a hole and lies.

Run ON the phone, as root.
"""
import ctypes
import fcntl
import mmap
import os
import struct
import time

TLMM = 0x03400000
NORTH, WEST, EAST = 0x500000, 0x100000, 0x900000
TILES = [(0, 3, EAST), (4, 7, WEST), (8, 34, EAST), (35, 37, NORTH),
         (38, 39, WEST), (40, 48, EAST), (49, 52, NORTH), (53, 84, WEST),
         (85, 96, EAST), (97, 104, WEST), (105, 113, NORTH), (114, 116, WEST),
         (117, 126, EAST), (127, 129, WEST), (130, 131, NORTH), (132, 149, WEST)]

PON, RESETB, INTB = 25, 91, 26
CFG, IN_OUT = 0x0, 0x4


def pin_base(p):
    for lo, hi, tile in TILES:
        if lo <= p <= hi:
            return TLMM + tile + p * 0x1000
    raise SystemExit("pin %d not in any tile" % p)


class Pin:
    def __init__(self, fd, num):
        self.addr = pin_base(num)
        page = self.addr & ~0xFFF
        self.off = self.addr & 0xFFF
        self.m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                           mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        self.num = num

    def r(self, o):
        return struct.unpack_from("<I", self.m, self.off + o)[0]

    def w(self, o, v):
        struct.pack_into("<I", self.m, self.off + o, v)

    def as_output(self, val):
        cfg = self.r(CFG)
        cfg &= ~(0xF << 2)          # func select 0 = plain gpio
        cfg &= ~0x3                 # no pull
        cfg |= (1 << 9)             # output enable
        self.w(CFG, cfg)
        io = self.r(IN_OUT)
        io = (io | 0x2) if val else (io & ~0x2)
        self.w(IN_OUT, io)

    def level(self):
        return self.r(IN_OUT) & 1


I2C_RDWR = 0x0707


class Msg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("len", ctypes.c_uint16), ("buf", ctypes.POINTER(ctypes.c_uint8))]


class IData(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(Msg)), ("nmsgs", ctypes.c_uint32)]


def pmic_id(bus=4, addr=0x08):
    fd = os.open("/dev/i2c-%d" % bus, os.O_RDWR)
    w = (ctypes.c_uint8 * 1)(0x00)
    r = (ctypes.c_uint8 * 4)()
    msgs = (Msg * 2)(Msg(addr, 0, 1, w), Msg(addr, 1, 4, r))
    try:
        fcntl.ioctl(fd, I2C_RDWR, IData(msgs, 2))
        return bytes(r)
    except OSError as e:
        return None
    finally:
        os.close(fd)


def main():
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    pon = Pin(fd, PON)
    resetb = Pin(fd, RESETB)

    print("before: PON cfg=%08x io=%08x | RESETB cfg=%08x level=%d" %
          (pon.r(CFG), pon.r(IN_OUT), resetb.r(CFG), resetb.level()))
    print("i2c before PON: %s" %
          (pmic_id() and pmic_id().hex() or "no answer"))

    print("driving PON low, then high...")
    pon.as_output(0)
    time.sleep(0.05)
    pon.as_output(1)

    for i in range(40):
        time.sleep(0.05)
        if resetb.level():
            print("  RESETB released after %d ms" % ((i + 1) * 50))
            break
    else:
        print("  RESETB never released (still 0) after 2 s")

    print("after:  PON io=%08x | RESETB level=%d" %
          (pon.r(IN_OUT), resetb.level()))

    b = pmic_id()
    if b:
        print("PMIC ANSWERS: Part 0x%02x%02x  Rev %d  VendorRev 0x%02x" %
              (b[1], b[0], b[3], b[2]))
    else:
        print("PMIC still silent on i2c-4 @0x08")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
