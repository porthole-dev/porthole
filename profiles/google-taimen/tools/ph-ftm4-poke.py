#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok · 2 usage
"""Interrogate the FTM4 touch controller behind the back of a bound ftm4 driver.

Run ON THE DEVICE, as root. Stdlib only.

The point of this tool is to answer one question when touch has gone quiet:
is the CONTROLLER silent, or is the INTERRUPT silent? The driver only ever
reads the event FIFO from its irq handler, so if the irq line never asserts the
driver never looks -- and "no events in evtest" is consistent with both a chip
that stopped sensing and a chip that is sensing fine into a FIFO nobody drains.

I2C_RDWR does not go through i2cdev_check_addr(), so this works while ftm4 is
still bound to 0-0049. That is racy by construction; it is safe here precisely
because the failure mode under investigation is "the driver is not touching the
bus at all". Do not run it against a healthy controller and expect the driver
to keep working -- reading an event POPS it off the FIFO.

  ph-ftm4-poke.py id              read the chip id (non-destructive)
  ph-ftm4-poke.py gpio            read the irq / reset / switch line levels
  ph-ftm4-poke.py drain [N]       pop up to N events (default 32)
  ph-ftm4-poke.py watch [SECS]    poll events + irq level for SECS (default 15)
"""
import fcntl
import os
import struct
import sys
import time

I2C_DEV = "/dev/i2c-0"
TOUCH_ADDR = 0x49

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001

# struct i2c_msg { __u16 addr; __u16 flags; __u16 len; __u8 *buf; } -- the
# pointer is 8-byte aligned, so there are 2 bytes of padding after len.
I2C_MSG_FMT = "<HHH2xQ"
I2C_MSG_SIZE = struct.calcsize(I2C_MSG_FMT)
# struct i2c_rdwr_ioctl_data { struct i2c_msg *msgs; __u32 nmsgs; }
RDWR_FMT = "<QI4x"

GPIO_MAGIC = 0xB4
GPIOCHIP = "/dev/gpiochip0"
HANDLE_REQ_FMT = "<64II64B32sIi"
GPIOHANDLE_REQUEST_INPUT = 1 << 0
GPIO_GET_LINEHANDLE = (3 << 30) | (364 << 16) | (GPIO_MAGIC << 8) | 0x03
GPIOHANDLE_GET_LINE_VALUES = (3 << 30) | (64 << 16) | (GPIO_MAGIC << 8) | 0x08

IRQ_GPIO = 125
RESET_GPIO = 89
SWITCH_GPIO = 75

EVENT_SIZE = 8
CMD_READ_ONE_EVENT = 0x85
REG_WRITE = 0xB6
REG_CHIP_ID = 0x04
FTM4_REG_SYSTEM_RESET = 0x28

def ctypes_addr(buf):
    """Address of a bytearray's storage, for embedding in an ioctl argument."""
    import ctypes
    return ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))


def i2c_rdwr(fd, msgs):
    """msgs: list of (flags, bytearray). Returns the list of buffers."""
    packed = b""
    bufs = []
    for flags, buf in msgs:
        bufs.append(buf)
        packed += struct.pack(I2C_MSG_FMT, TOUCH_ADDR, flags, len(buf),
                              ctypes_addr(buf))
    msg_array = bytearray(packed)
    arg = struct.pack(RDWR_FMT, ctypes_addr(msg_array), len(msgs))
    fcntl.ioctl(fd, I2C_RDWR, bytearray(arg))
    return bufs


def read_reg(fd, offset, length):
    cmd = bytearray([REG_WRITE, 0x00, offset])
    val = bytearray(length)
    i2c_rdwr(fd, [(0, cmd), (I2C_M_RD, val)])
    return bytes(val)


def read_event(fd):
    cmd = bytearray([CMD_READ_ONE_EVENT])
    val = bytearray(EVENT_SIZE)
    i2c_rdwr(fd, [(0, cmd), (I2C_M_RD, val)])
    return bytes(val)


def get_line(gpio):
    """Read a line level, or -1 if something else holds the line.

    ph-ftm4-log.py keeps the irq line handle open for its whole run, and the
    driver holds reset and switch, so EBUSY here is expected and must not be
    fatal -- the FIFO reads are the point of this tool, not the gpio.
    """
    offsets = [0] * 64
    offsets[0] = gpio
    req = struct.pack(HANDLE_REQ_FMT, *offsets, GPIOHANDLE_REQUEST_INPUT,
                      *([0] * 64), b"tk-ftm4-poke", 1, 0)
    buf = bytearray(req)
    try:
        with open(GPIOCHIP, "rb") as chip:
            fcntl.ioctl(chip, GPIO_GET_LINEHANDLE, buf)
    except OSError:
        return -1
    fd = struct.unpack(HANDLE_REQ_FMT, bytes(buf))[-1]
    try:
        data = bytearray(64)
        fcntl.ioctl(fd, GPIOHANDLE_GET_LINE_VALUES, data)
        return data[0]
    finally:
        os.close(fd)


def decode(ev):
    """Human-readable one-liner for an 8-byte event, using the driver's split."""
    b0 = ev[0]
    if b0 == 0x00:
        return "NO_EVENT"
    if b0 in (0x0F, 0x10, 0x11, 0x16):
        names = {0x0F: "ERROR", 0x10: "CONTROLLER_READY",
                 0x11: "SLEEPOUT_READY", 0x16: "STATUS"}
        return "%-16s %s" % (names[b0], ev.hex(" "))
    eid = b0 & 0x0F
    tid = (b0 >> 4) & 0x0F
    names = {0x03: "ENTER", 0x04: "LEAVE", 0x05: "MOTION"}
    if eid not in names:
        return "UNKNOWN         %s" % ev.hex(" ")
    x = (ev[1] << 4) | (ev[3] >> 4)
    y = (ev[2] << 4) | (ev[3] & 0x0F)
    return "%-6s id=%-2d x=%4d y=%4d z=%3d  %s" % (
        names[eid], tid, x, y, ev[4], ev.hex(" "))


def irq_count():
    try:
        with open("/proc/interrupts") as f:
            for line in f:
                if "ftm4" in line:
                    return sum(int(x) for x in line.split()[1:9])
    except (OSError, ValueError):
        pass
    return -1


def cmd_gpio():
    print("gpio %3d (irq)    = %d   (level-low: 0 = asserted)"
          % (IRQ_GPIO, get_line(IRQ_GPIO)))
    print("gpio %3d (reset)  = %d   (active-low: 1 = released)"
          % (RESET_GPIO, get_line(RESET_GPIO)))
    print("gpio %3d (switch) = %d   (0 = AP owns bus, 1 = SLPI)"
          % (SWITCH_GPIO, get_line(SWITCH_GPIO)))
    print("ftm4 irq count    = %d" % irq_count())


def write_reg(fd, offset, value):
    i2c_rdwr(fd, [(0, bytearray([REG_WRITE, 0x00, offset, value]))])


def run_induce(fd):
    """Reset the controller behind the driver's back and see what breaks.

    The failure being chased looks exactly like a controller that rebooted on
    its own: alive on i2c, chip id readable, but no interrupts and nothing in
    the FIFO. The vendor driver reacts to such a reboot (STATUS_EVENT_REBOOT_BY_
    ESD, 0x16 0xed) by re-initialising the part; ftm4.c currently only logs it.
    So issue the same system reset the driver's own init uses, WITHOUT the
    SENSE_ON / FLUSHBUFFER / INT_ENABLE that normally follow it, and check
    whether the resulting state is indistinguishable from the reported bug.
    """
    print("baseline irq = %d" % irq_count())
    print("issuing SYSTEM RESET (0xb6 00 28 80) -- no SENSE_ON afterwards")
    sys.stdout.flush()
    write_reg(fd, FTM4_REG_SYSTEM_RESET, 0x80)
    time.sleep(0.2)

    after = irq_count()
    print("irq right after reset = %d" % after)

    print("\nwatching 10s with NO touching and NO i2c ...")
    sys.stdout.flush()
    time.sleep(10)
    idle_irq = irq_count()
    print("  irq -> %d (delta %d)" % (idle_irq, idle_irq - after))

    print("\nNOW TOUCH AND SWIPE CONTINUOUSLY FOR 15s ...")
    sys.stdout.flush()
    time.sleep(15)
    touch_irq = irq_count()
    print("  irq -> %d (delta %d)" % (touch_irq, touch_irq - idle_irq))

    val = read_reg(fd, REG_CHIP_ID, 7)
    print("\nchip id after reset: %s (%s)"
          % (val.hex(" "),
             "responds" if val[1] == 0x36 and val[2] == 0x70 else "DEAD"))

    seen = []
    for _ in range(8):
        ev = read_event(fd)
        if ev[0] == 0x00:
            break
        seen.append(decode(ev))
    print("fifo after reset: %s"
          % ("empty" if not seen else "%d event(s)" % len(seen)))
    for line in seen:
        print("  %s" % line)

    print("\n=== VERDICT ===")
    if touch_irq == idle_irq:
        print("REPRODUCED: after an unserviced reset the controller is alive "
              "on i2c but reports nothing, even while touched.")
        print("An unhandled spontaneous reset is therefore sufficient to "
              "cause the reported 'touch goes quiet' failure.")
    else:
        print("NOT reproduced: the part resumed reporting on its own after "
              "reset (%d interrupts while touched)." % (touch_irq - idle_irq))
    print("\nRecover with:")
    print("  sudo sh -c 'echo 0-0049 > /sys/bus/i2c/drivers/ftm4/unbind; "
          "echo 0-0049 > /sys/bus/i2c/drivers/ftm4/bind'")
    return 0


def run_protocol(fd, idle_s=40):
    """Three timed phases that pin down a 'touch went quiet' report.

    The whole point is that the operator only has to follow a fixed clock --
    no live coordination, so a mistimed instruction cannot masquerade as a
    hardware fault. Phase 2 deliberately issues NO i2c at all, so if the irq
    stays flat there it is an uncontaminated observation of the failure; only
    then does phase 3 touch the bus and ask what the controller was doing.
    """
    quiet_s, poll_s = 15, 15

    print("PHASE 1 (%ds): DO NOT TOUCH THE SCREEN." % idle_s)
    sys.stdout.flush()
    start_irq = irq_count()
    # Sample the irq line through the idle so a mid-idle glitch is not missed;
    # the controller going quiet and coming back would otherwise look flat.
    deadline = time.time() + idle_s
    while time.time() < deadline:
        time.sleep(min(10.0, max(0.0, deadline - time.time())))
    p1_irq = irq_count()
    quiet = p1_irq == start_irq
    print("  irq %d -> %d  => %s"
          % (start_irq, p1_irq,
             "QUIET (failed state entered)" if quiet else "still reporting"))

    print("\nPHASE 2 (%ds): TOUCH AND SWIPE CONTINUOUSLY, STARTING NOW."
          % quiet_s)
    print("  (no i2c is issued in this phase -- pure observation)")
    sys.stdout.flush()
    time.sleep(quiet_s)
    p2_irq = irq_count()
    print("  irq %d -> %d (delta %d)" % (p1_irq, p2_irq, p2_irq - p1_irq))

    print("\nPHASE 3 (%ds): KEEP TOUCHING. Reading the FIFO directly."
          % poll_s)
    sys.stdout.flush()
    end = time.time() + poll_s
    seen = 0
    first = []
    while time.time() < end:
        ev = read_event(fd)
        if ev[0] != 0x00:
            seen += 1
            if len(first) < 12:
                first.append(decode(ev))
        else:
            time.sleep(0.002)
    p3_irq = irq_count()
    for line in first:
        print("  %s" % line)
    print("  fifo events = %d ; irq %d -> %d (delta %d)"
          % (seen, p2_irq, p3_irq, p3_irq - p2_irq))

    print("\n=== VERDICT ===")
    if not quiet:
        print("Never entered the failed state -- controller kept reporting "
              "through phase 1. Inconclusive; retry after a longer idle.")
    elif p2_irq != p1_irq:
        print("Controller reported normally as soon as it was touched.")
        print("The 'quiet window' was just an untouched screen, NOT a fault.")
    elif seen:
        print("FAULT CONFIRMED, and the controller WAS still scanning:")
        print("  touching produced no interrupt, but the FIFO held %d events."
              % seen)
        print("  => the chip stops ASSERTING THE IRQ LINE while still sensing.")
    else:
        print("FAULT CONFIRMED, and the controller had STOPPED SCANNING:")
        print("  touching produced neither an interrupt nor a single FIFO "
              "event.")
        print("  => the chip itself stops sensing; the irq line is innocent.")
    if p3_irq != p2_irq:
        print("NOTE: interrupts resumed during phase 3, i.e. bus activity "
              "revived the controller.")
    return 0


def main():
    args = sys.argv[1:] or ["id"]
    what = args[0]

    if what == "gpio":
        cmd_gpio()
        return

    fd = os.open(I2C_DEV, os.O_RDWR)
    try:
        if what == "id":
            val = read_reg(fd, REG_CHIP_ID, 7)
            print("chip id read: %s" % val.hex(" "))
            ok = val[1] == 0x36 and val[2] == 0x70
            print("  id0=%02x id1=%02x -> %s" % (val[1], val[2],
                                                 "MATCH" if ok else "MISMATCH"))
            print("  firmware=%02x %02x -> %s" % (
                val[5], val[6],
                "absent" if not val[5] and not val[6] else "present"))

        elif what == "drain":
            n = int(args[1]) if len(args) > 1 else 32
            empty = 0
            for i in range(n):
                ev = read_event(fd)
                print("%3d  %s" % (i, decode(ev)))
                if ev[0] == 0x00:
                    empty += 1
                    if empty >= 3:
                        print("  (FIFO empty)")
                        break

        elif what == "watch":
            secs = float(args[1]) if len(args) > 1 else 15.0
            print("polling for %.0fs -- TOUCH THE SCREEN NOW" % secs)
            print("irq gpio starts at %d, irq count %d"
                  % (get_line(IRQ_GPIO), irq_count()))
            end = time.time() + secs
            seen = 0
            low = 0
            while time.time() < end:
                if get_line(IRQ_GPIO) == 0:
                    low += 1
                ev = read_event(fd)
                if ev[0] != 0x00:
                    seen += 1
                    print("%8.2f  %s" % (time.time() % 1000, decode(ev)))
                else:
                    time.sleep(0.002)
            print("---")
            print("events seen  = %d" % seen)
            print("irq line low = %d samples (0 means never asserted)" % low)
            print("irq count    = %d" % irq_count())

        elif what == "reg":
            off = int(args[1], 0)
            n = int(args[2]) if len(args) > 2 else 4
            print("reg %#04x: %s" % (off, read_reg(fd, off, n).hex(" ")))

        elif what == "induce":
            return run_induce(fd)

        elif what == "protocol":
            return run_protocol(fd,
                                int(args[1]) if len(args) > 1 else 40)

        else:
            print(__doc__)
            return 2
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
