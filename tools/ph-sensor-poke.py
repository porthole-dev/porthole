#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
"""Live 16-bit-register I2C access to the IMX179, for use WHILE camss holds the
pipeline open.

The point is to decouple "what the driver does" from "what the sensor needs".
Every sensor experiment so far has cost a module rebuild and a 25-second capture;
this makes it a sub-second write, so the mode table, the PLL and the streaming
bit can be swept live against the CSIPHY status without touching the kernel.

Run ON the phone, as root.

  ph-sensor-poke.py r 0x0100            read one register
  ph-sensor-poke.py w 0x0100 0x01       write one register
  ph-sensor-poke.py dump 0x0300 0x10    dump a range
"""
import fcntl, struct, sys, ctypes

I2C_RDWR = 0x0707
BUS, ADDR = 5, 0x10


class Msg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("len", ctypes.c_uint16), ("buf", ctypes.POINTER(ctypes.c_uint8))]


class Data(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(Msg)), ("nmsgs", ctypes.c_uint32)]


def _xfer(fd, msgs):
    arr = (Msg * len(msgs))(*msgs)
    fcntl.ioctl(fd, I2C_RDWR, Data(arr, len(msgs)))


def rd(fd, reg):
    wbuf = (ctypes.c_uint8 * 2)(reg >> 8, reg & 0xFF)
    rbuf = (ctypes.c_uint8 * 1)()
    _xfer(fd, [Msg(ADDR, 0, 2, wbuf), Msg(ADDR, 1, 1, rbuf)])  # 1 = I2C_M_RD
    return rbuf[0]


def wr(fd, reg, val):
    b = (ctypes.c_uint8 * 3)(reg >> 8, reg & 0xFF, val & 0xFF)
    _xfer(fd, [Msg(ADDR, 0, 3, b)])


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    fd = open("/dev/i2c-%d" % BUS, "r+b", buffering=0)
    try:
        if a[0] == "r":
            print("%04x = %02x" % (int(a[1], 0), rd(fd, int(a[1], 0))))
        elif a[0] == "w":
            reg, val = int(a[1], 0), int(a[2], 0)
            wr(fd, reg, val)
            print("%04x <- %02x, reads %02x" % (reg, val, rd(fd, reg)))
        elif a[0] == "dump":
            base, n = int(a[1], 0), int(a[2], 0)
            for i in range(0, n, 8):
                row = ["%02x" % rd(fd, base + i + j) for j in range(min(8, n - i))]
                print("  %04x: %s" % (base + i, " ".join(row)))
        else:
            print(__doc__)
            return 1
    finally:
        fd.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
