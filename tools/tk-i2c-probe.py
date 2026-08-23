#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Scan the taimen touch i2c bus (QUP5) for the STM FTM4 at 0x49.

Run ON THE DEVICE, as root. Stdlib only -- there is no i2c-tools and no
libgpiod on this device, and CONFIG_GPIO_SYSFS is off, so GPIO goes through
the character device and i2c through /dev/i2c-N ioctls.

Two GPIOs must be right before the AP can see anything on this bus:

  tlmm 75  -- the vendor's stm,switch_gpio. The bus is SHARED with the SLPI
              sensor DSP. 0 = AP owns it, 1 = SLPI owns it. Observed live on
              the stock kernel: "switch i2c to SLPI (set to 1)" on suspend,
              "switch i2c to AP (set to 0)" on resume.
  tlmm 89  -- reset, active low. Must be released (high) and HELD; releasing
              the line lets it float, since the pin has no bias configured.

  tk-i2c-probe.py [BUS]     e.g. tk-i2c-probe.py 5   (default: scan all)
"""
import fcntl
import os
import struct
import sys
import time
import glob

GPIO_MAGIC = 0xB4
GPIOCHIP = "/dev/gpiochip0"          # tlmm: label "3400000.pinctrl", 150 lines
SWITCH_GPIO = 75                     # stm,switch_gpio: 0 = AP, 1 = SLPI
RESET_GPIO = 89                      # active low
IRQ_GPIO = 125
HW_RESET_MS = 90

HANDLE_REQ_FMT = "<64II64B32sIi"
GPIOHANDLE_REQUEST_OUTPUT = 1 << 1
GPIOHANDLE_REQUEST_INPUT = 1 << 0
GPIO_GET_LINEHANDLE = (3 << 30) | (364 << 16) | (GPIO_MAGIC << 8) | 0x03
GPIOHANDLE_GET_LINE_VALUES = (3 << 30) | (64 << 16) | (GPIO_MAGIC << 8) | 0x08
GPIOHANDLE_SET_LINE_VALUES = (3 << 30) | (64 << 16) | (GPIO_MAGIC << 8) | 0x09

I2C_SLAVE_FORCE = 0x0706
TOUCH_ADDR = 0x49


def request_line(gpio, flags, label=b"tk-i2c-probe"):
    offsets = [0] * 64
    offsets[0] = gpio
    req = struct.pack(HANDLE_REQ_FMT, *offsets, flags, *([0] * 64), label, 1, 0)
    buf = bytearray(req)
    with open(GPIOCHIP, "rb") as chip:
        fcntl.ioctl(chip, GPIO_GET_LINEHANDLE, buf)
    return struct.unpack(HANDLE_REQ_FMT, bytes(buf))[-1]


def set_line(fd, value):
    data = bytearray(64)
    data[0] = value
    fcntl.ioctl(fd, GPIOHANDLE_SET_LINE_VALUES, data)


def get_line(gpio):
    fd = request_line(gpio, GPIOHANDLE_REQUEST_INPUT)
    try:
        data = bytearray(64)
        fcntl.ioctl(fd, GPIOHANDLE_GET_LINE_VALUES, data)
        return data[0]
    finally:
        os.close(fd)


def scan_bus(path):
    """Return the list of addresses that ACK on this bus."""
    found = []
    for addr in range(0x08, 0x78):
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            return found
        try:
            fcntl.ioctl(fd, I2C_SLAVE_FORCE, addr)
            try:
                os.read(fd, 1)
                found.append(addr)
            except OSError:
                pass
        finally:
            os.close(fd)
    return found


# --- FTM4 register read, from downstream stm/ftm4_ts.c:fts_read_reg() ---
# Two i2c messages with a repeated start: write the register bytes, then read.
#   chip id: write {0xB6, 0x00, 0x04}, read 7 -> val[1]=0x36 val[2]=0x70
#   val[5]==0 && val[6]==0 means the IC reports no firmware.
import ctypes

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001
FTS_ID0, FTS_ID1 = 0x36, 0x70

# struct i2c_msg { u16 addr; u16 flags; u16 len; u8 *buf; }  (8-byte aligned)
MSG_FMT = "<HHH2xQ"
# struct i2c_rdwr_ioctl_data { struct i2c_msg *msgs; u32 nmsgs; }
RDWR_FMT = "<QI4x"


def i2c_read_reg(path, addr, reg_bytes, rlen):
    """Combined write-then-read, exactly like fts_read_reg()."""
    wbuf = ctypes.create_string_buffer(bytes(reg_bytes), len(reg_bytes))
    rbuf = ctypes.create_string_buffer(rlen)
    msgs = (struct.pack(MSG_FMT, addr, 0, len(reg_bytes),
                        ctypes.addressof(wbuf))
            + struct.pack(MSG_FMT, addr, I2C_M_RD, rlen,
                          ctypes.addressof(rbuf)))
    msgs_buf = ctypes.create_string_buffer(msgs, len(msgs))
    data = struct.pack(RDWR_FMT, ctypes.addressof(msgs_buf), 2)
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.ioctl(fd, I2C_RDWR, data)
    finally:
        os.close(fd)
    return bytes(rbuf.raw[:rlen])


def chipid(path=f"/dev/i2c-0"):
    sw = request_line(SWITCH_GPIO, GPIOHANDLE_REQUEST_OUTPUT)
    rst = request_line(RESET_GPIO, GPIOHANDLE_REQUEST_OUTPUT)
    try:
        set_line(sw, 0)
        set_line(rst, 0)
        time.sleep(0.010)
        set_line(rst, 1)
        time.sleep(HW_RESET_MS / 1000.0)
        val = i2c_read_reg(path, TOUCH_ADDR, [0xB6, 0x00, 0x04], 7)
        print(f"raw   = {val.hex(' ')}")
        print(f"id    = {val[1]:02X} {val[2]:02X}   "
              f"(expect {FTS_ID0:02X} {FTS_ID1:02X})")
        if val[1] == FTS_ID0 and val[2] == FTS_ID1:
            print("=> STM FTM4 CONFIRMED")
            if val[5] == 0 and val[6] == 0:
                print("=> but IC reports NO FIRMWARE (val[5],val[6] == 0)")
            else:
                print(f"=> firmware present: {val[5]:02X} {val[6]:02X}")
        else:
            print("=> id mismatch")
    finally:
        os.close(rst)
        os.close(sw)


# --- FTM4 init + event protocol, from downstream stm/ftm4_ts.c ---
# Registers are written as raw byte strings; 0xB6 xx yy vv is "write vv to yy".
WBCRC_ON     = [0xB6, 0x00, 0x1E, 0x20]   # fts_systemreset(), step 1
SYSTEM_RESET = [0xB6, 0x00, 0x28, 0x80]   # fts_systemreset(), step 2
INT_SET      = [0xB6, 0x00, 0x2C, 0x00]   # fts_interrupt_set(); [3] = 0x48/0x08
INT_ENABLE, INT_DISABLE = 0x48, 0x08
SENSEON, SENSEOFF = 0x93, 0x92
FLUSHBUFFER = 0xA1
READ_ONE_EVENT = 0x85
FTS_EVENT_SIZE = 8
EVENTID_ENTER, EVENTID_LEAVE, EVENTID_MOTION = 0x03, 0x04, 0x05
EVENTID_ERROR, EVENTID_CONTROLLER_READY = 0x0F, 0x10


def i2c_write(path, addr, data):
    """Single-message i2c write, like fts_write_reg()."""
    wbuf = ctypes.create_string_buffer(bytes(data), len(data))
    msgs = struct.pack(MSG_FMT, addr, 0, len(data), ctypes.addressof(wbuf))
    msgs_buf = ctypes.create_string_buffer(msgs, len(msgs))
    payload = struct.pack(RDWR_FMT, ctypes.addressof(msgs_buf), 1)
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.ioctl(fd, I2C_RDWR, payload)
    finally:
        os.close(fd)


def read_event(path):
    return i2c_read_reg(path, TOUCH_ADDR, [READ_ONE_EVENT], FTS_EVENT_SIZE)


def ftm4_init(path, verbose=True):
    """The vendor init: reset, drain to CONTROLLER_READY, sense on, irq on."""
    def say(*a):
        if verbose:
            print(*a)

    i2c_write(path, TOUCH_ADDR, INT_SET[:3] + [INT_DISABLE])
    say("int disabled")

    i2c_write(path, TOUCH_ADDR, WBCRC_ON)
    time.sleep(0.010)
    i2c_write(path, TOUCH_ADDR, SYSTEM_RESET)
    time.sleep(0.010)
    say("wbcrc enabled, system reset issued")

    ready = False
    for _ in range(100):
        ev = read_event(path)
        if ev[0] == EVENTID_CONTROLLER_READY:
            say(f"CONTROLLER_READY: {ev.hex(' ')}")
            ready = True
            break
        if ev[0] == EVENTID_ERROR:
            say(f"error event: {ev.hex(' ')}")
        time.sleep(0.005)
    if not ready:
        say("WARNING: never saw CONTROLLER_READY (0x10)")

    # Order below is fts_reinit(): SENSEON, 50 ms, FLUSHBUFFER, then irq on.
    # FLUSHBUFFER matters -- it drops whatever is already queued so the FIFO
    # starts clean; without it the stale init status events are all you see.
    i2c_write(path, TOUCH_ADDR, [SENSEON])
    time.sleep(0.050)
    say("SENSEON sent")

    i2c_write(path, TOUCH_ADDR, [FLUSHBUFFER])
    time.sleep(0.010)
    say("FLUSHBUFFER sent")

    i2c_write(path, TOUCH_ADDR, INT_SET[:3] + [INT_ENABLE])
    say("int enabled")
    return ready


#. Status events are matched on the WHOLE first byte, while pointer events pack
#  TouchID into the high nibble and the event id into the low one. The two
#  spaces overlap, so only codes that cannot BE a pointer event may be matched
#  whole -- as pointer events these four decode to event ids 0xF, 0x0, 0x1 and
#  0x6, none of which the part emits.
#
#  The vendor header also names 0x12..0x15 (RESULT_READ_REGISTER,
#  STATUS_REQUEST_COMP, INTERNAL_/EXTERNAL_RELEASE_INFO), and they are
#  deliberately NOT listed here: those are exactly ENTER/LEAVE/MOTION for touch
#  id 1, so matching them whole hides the second finger. They only ever arrive
#  in answer to commands this script does not send.
STATUS_EVENTS = {
    0x0F: "ERROR", 0x10: "CONTROLLER_READY",
    0x11: "SLEEPOUT_READY", 0x16: "STATUS_EVENT",
}


def decode(ev):
    whole = ev[0]
    if whole in STATUS_EVENTS:
        return f"{STATUS_EVENTS[whole]:20s} raw {ev.hex(' ')}"
    eid = whole & 0x0F
    tid = (whole >> 4) & 0x0F
    x = ((ev[1] & 0xFF) << 4) + ((ev[3] & 0xF0) >> 4)
    y = ((ev[2] & 0xFF) << 4) + (ev[3] & 0x0F)
    z = ev[4]
    name = {EVENTID_ENTER: "ENTER", EVENTID_LEAVE: "LEAVE",
            EVENTID_MOTION: "MOTION"}.get(eid)
    if name:
        return f"{name:6s} id={tid} x={x:4d} y={y:4d} z={z:3d}"
    return f"unknown 0x{whole:02x}        raw {ev.hex(' ')}"


def touchtest(path, seconds=25):
    """Init the chip, then report touch events. TOUCH THE SCREEN."""
    sw = request_line(SWITCH_GPIO, GPIOHANDLE_REQUEST_OUTPUT)
    rst = request_line(RESET_GPIO, GPIOHANDLE_REQUEST_OUTPUT)
    try:
        set_line(sw, 0)
        set_line(rst, 0)
        time.sleep(0.010)
        set_line(rst, 1)
        time.sleep(HW_RESET_MS / 1000.0)

        ftm4_init(path)
        print(f"--- TOUCH THE SCREEN for {seconds}s ---")
        # Poll the event FIFO directly rather than gating on the irq line --
        # a 2 ms sampling loop can miss a pulsed interrupt entirely, and a
        # read of an empty FIFO is harmless (it returns EVENTID_NO_EVENT).
        end = time.time() + seconds
        n = 0
        while time.time() < end:
            ev = read_event(path)
            if ev[0] != 0x00:
                print("  " + decode(ev))
                n += 1
            else:
                time.sleep(0.002)
        print(f"done: {n} events")
    finally:
        os.close(rst)
        os.close(sw)


def main():
    if sys.argv[1:2] == ["touchtest"]:
        touchtest(sys.argv[2] if len(sys.argv) > 2 else "/dev/i2c-0",
                  int(sys.argv[3]) if len(sys.argv) > 3 else 25)
        return
    if sys.argv[1:2] == ["chipid"]:
        chipid(sys.argv[2] if len(sys.argv) > 2 else "/dev/i2c-0")
        return
    buses = sys.argv[1:] or None
    paths = ([f"/dev/i2c-{b}" for b in buses] if buses
             else sorted(glob.glob("/dev/i2c-*")))
    if not paths:
        sys.exit("no /dev/i2c-* -- is blsp1_i2c5 enabled and i2c_qup loaded?")

    print(f"switch gpio{SWITCH_GPIO} before: {get_line(SWITCH_GPIO)}  "
          f"irq gpio{IRQ_GPIO}: {get_line(IRQ_GPIO)}")

    sw = request_line(SWITCH_GPIO, GPIOHANDLE_REQUEST_OUTPUT)
    rst = request_line(RESET_GPIO, GPIOHANDLE_REQUEST_OUTPUT)
    try:
        set_line(sw, 0)                    # hand the bus to the AP
        set_line(rst, 0)                   # pulse reset
        time.sleep(0.010)
        set_line(rst, 1)
        time.sleep(HW_RESET_MS / 1000.0)
        print(f"switch driven LOW (AP), reset released and held; "
              f"irq now {get_line(IRQ_GPIO)}")

        for p in paths:
            hits = scan_bus(p)
            pretty = " ".join(f"0x{a:02x}" for a in hits) or "(nothing)"
            mark = "  <-- FTM4 TOUCH" if TOUCH_ADDR in hits else ""
            print(f"{p}: {pretty}{mark}")
    finally:
        os.close(rst)
        os.close(sw)


if __name__ == "__main__":
    main()
