#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok
"""Bring Easel (Pixel Visual Core) all the way up from userspace, then retrain
the PCIe link so it enumerates.

HANDOFF §41: Easel is an in-line MIPI bridge carrying both cameras' CSI lanes.
This reproduces mnh_pwr_up() (mnh-pwr.c:597) by hand so the sequence can be
validated before it is committed to a driver:

    PON (tlmm25) -> PMIC out of reset
    rails, in order: sdldo -> ioldo -> asr -> sdsr
    soc_pwr_good (bcm15602 gpio0) high
    udelay(60), then let the PCIe RC retrain

Register addresses from bcm15602-regulator.{c,h}; enable values are exactly
what bcm15602_regulator_enable() writes.

Run ON the phone, as root.
"""
import ctypes
import fcntl
import mmap
import os
import struct
import subprocess
import time

TLMM = 0x03400000
NORTH, WEST, EAST = 0x500000, 0x100000, 0x900000
TILES = [(0, 3, EAST), (4, 7, WEST), (8, 34, EAST), (35, 37, NORTH),
         (38, 39, WEST), (40, 48, EAST), (49, 52, NORTH), (53, 84, WEST),
         (85, 96, EAST), (97, 104, WEST), (105, 113, NORTH), (114, 116, WEST),
         (117, 126, EAST), (127, 129, WEST), (130, 131, NORTH), (132, 149, WEST)]
PON, RESETB = 25, 91
CFG, IN_OUT = 0x0, 0x4

# BCM15602 registers (bcm15602-regulator.h)
R_GPIO_OUT_CTRL = 0x08
R_SDSR_CTRL0 = 0x52
R_ASR_CTRL0 = 0x5c
R_IOLDO_ENCTRL = 0x72
R_SDLDO_ENCTRL = 0x77

# enable values (bcm15602_regulator_enable)
RAILS = [("sdldo", R_SDLDO_ENCTRL, 0x3),
         ("ioldo", R_IOLDO_ENCTRL, 0x3),
         ("asr",   R_ASR_CTRL0,    0xC1),
         ("sdsr",  R_SDSR_CTRL0,   0xDB)]

I2C_RDWR = 0x0707
BUS, ADDR = 4, 0x08


class Msg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("len", ctypes.c_uint16), ("buf", ctypes.POINTER(ctypes.c_uint8))]


class IData(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(Msg)), ("nmsgs", ctypes.c_uint32)]


def pin_base(p):
    for lo, hi, tile in TILES:
        if lo <= p <= hi:
            return TLMM + tile + p * 0x1000
    raise SystemExit("pin %d not tiled" % p)


class Pin:
    def __init__(self, fd, num):
        addr = pin_base(num)
        self.off = addr & 0xFFF
        self.m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                           mmap.PROT_READ | mmap.PROT_WRITE,
                           offset=addr & ~0xFFF)

    def r(self, o):
        return struct.unpack_from("<I", self.m, self.off + o)[0]

    def w(self, o, v):
        struct.pack_into("<I", self.m, self.off + o, v)

    def drive(self, val):
        cfg = self.r(CFG)
        cfg &= ~(0xF << 2)
        cfg &= ~0x3
        cfg |= (1 << 9)
        self.w(CFG, cfg)
        io = self.r(IN_OUT)
        self.w(IN_OUT, (io | 0x2) if val else (io & ~0x2))

    def level(self):
        return self.r(IN_OUT) & 1


class Pmic:
    def __init__(self):
        self.fd = os.open("/dev/i2c-%d" % BUS, os.O_RDWR)

    def rd(self, reg, n=1):
        w = (ctypes.c_uint8 * 1)(reg)
        r = (ctypes.c_uint8 * n)()
        msgs = (Msg * 2)(Msg(ADDR, 0, 1, w), Msg(ADDR, 1, n, r))
        fcntl.ioctl(self.fd, I2C_RDWR, IData(msgs, 2))
        return bytes(r)

    def wr(self, reg, val):
        b = (ctypes.c_uint8 * 2)(reg, val)
        msgs = (Msg * 1)(Msg(ADDR, 0, 2, b))
        fcntl.ioctl(self.fd, I2C_RDWR, IData(msgs, 1))


def main():
    memfd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    pon, resetb = Pin(memfd, PON), Pin(memfd, RESETB)

    print("== PON ==")
    pon.drive(0)
    time.sleep(0.05)
    pon.drive(1)
    for i in range(40):
        time.sleep(0.05)
        if resetb.level():
            print("  RESETB released after %d ms" % ((i + 1) * 50))
            break
    else:
        return print("  RESETB never released -- aborting") or 1

    p = Pmic()
    b = p.rd(0x00, 4)
    print("  PMIC part 0x%02x%02x rev %d" % (b[1], b[0], b[3]))

    print("== rails ==")
    for name, reg, val in RAILS:
        p.wr(reg, val)
        time.sleep(0.01)
        back = p.rd(reg)[0]
        print("  %-6s reg 0x%02x <- 0x%02x, reads 0x%02x %s" %
              (name, reg, val, back, "OK" if back == val else "MISMATCH"))

    print("== soc_pwr_good (pmic gpio0) ==")
    # reg_data = 0xC | (gpio1 << 1) | gpio0   (bcm15602-gpio.c:80-84)
    p.wr(R_GPIO_OUT_CTRL, 0xC | 0x1)
    time.sleep(0.01)
    print("  gpio_out_ctrl reads 0x%02x" % p.rd(R_GPIO_OUT_CTRL)[0])

    time.sleep(0.01)  # mnh-pwr does udelay(60); be generous

    print("== PCIe ==")
    print(subprocess.run(["sh", "-c",
        "ls /sys/bus/pci/devices/ 2>/dev/null | wc -l"],
        capture_output=True, text=True).stdout.strip(), "device(s) before rescan")
    # retrain: rebind the root complex so it redoes link training with the
    # endpoint now powered
    rc = "/sys/bus/platform/drivers/qcom-pcie"
    if os.path.isdir(rc):
        for d in os.listdir(rc):
            if "pci" in d:
                try:
                    open(rc + "/unbind", "w").write(d)
                    time.sleep(0.3)
                    open(rc + "/bind", "w").write(d)
                    print("  rebound %s" % d)
                except OSError as e:
                    print("  rebind %s failed: %s" % (d, e))
    time.sleep(1.0)
    out = subprocess.run(["sh", "-c", "ls /sys/bus/pci/devices/"],
                         capture_output=True, text=True).stdout.strip()
    print("  PCI devices now: %s" % (out or "(none)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
