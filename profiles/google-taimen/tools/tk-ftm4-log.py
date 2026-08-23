#!/usr/bin/env python3
# scope: device:google-taimen
"""Passively log the whole touch chain so a failure can be read off afterwards.

Run ON THE DEVICE, as root. Stdlib only. Writes one line per state change to
stdout; redirect it to a file and leave it running.

The chain has three boundaries and the bug could be at any of them, so this
samples all three and never perturbs any of them:

  gpio 125   the irq line itself, level-low. 0 = controller is asserting.
  irq count  /proc/interrupts, i.e. how many times the CPU took that irq.
  evdev      /dev/input/event1, i.e. how many packets reached userspace.

Reading the FIFO is deliberately NOT done here -- that would steal events from
the driver and turn a passive observation into an experiment. tk-ftm4-poke.py
is the tool for poking; this one only watches. The two together separate
"controller stopped scanning" (gpio stays high) from "irq not delivered" (gpio
goes low, count flat) from "driver drops it" (count rises, evdev flat).

  tk-ftm4-log.py [SECONDS]      default 3600
"""
import fcntl
import os
import struct
import sys
import time

GPIO_MAGIC = 0xB4
GPIOCHIP = "/dev/gpiochip0"
HANDLE_REQ_FMT = "<64II64B32sIi"
GPIOHANDLE_REQUEST_INPUT = 1 << 0
GPIO_GET_LINEHANDLE = (3 << 30) | (364 << 16) | (GPIO_MAGIC << 8) | 0x03
GPIOHANDLE_GET_LINE_VALUES = (3 << 30) | (64 << 16) | (GPIO_MAGIC << 8) | 0x08

IRQ_GPIO = 125
EVDEV = "/dev/input/event1"

SAMPLE_S = 0.05
HEARTBEAT_S = 30.0

# struct input_event on 64-bit: __kernel_ulong_t sec, usec; __u16 type, code;
# __s32 value
INPUT_EVENT_FMT = "<QQHHi"
INPUT_EVENT_SIZE = struct.calcsize(INPUT_EVENT_FMT)
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03


def open_gpio_line(gpio):
    """Hold the line handle open -- re-requesting it every sample is slow."""
    offsets = [0] * 64
    offsets[0] = gpio
    req = struct.pack(HANDLE_REQ_FMT, *offsets, GPIOHANDLE_REQUEST_INPUT,
                      *([0] * 64), b"tk-ftm4-log", 1, 0)
    buf = bytearray(req)
    with open(GPIOCHIP, "rb") as chip:
        fcntl.ioctl(chip, GPIO_GET_LINEHANDLE, buf)
    return struct.unpack(HANDLE_REQ_FMT, bytes(buf))[-1]


def read_line(fd, _data=bytearray(64)):
    fcntl.ioctl(fd, GPIOHANDLE_GET_LINE_VALUES, _data)
    return _data[0]


def irq_count():
    try:
        with open("/proc/interrupts") as f:
            for line in f:
                if "ftm4" in line:
                    return sum(int(x) for x in line.split()[1:9])
    except (OSError, ValueError):
        pass
    return -1


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 3600.0

    gpio_fd = open_gpio_line(IRQ_GPIO)
    try:
        ev_fd = os.open(EVDEV, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        print("cannot open %s: %s" % (EVDEV, e))
        ev_fd = -1

    boot = time.clock_gettime(time.CLOCK_MONOTONIC)
    syns = 0
    last_irq = irq_count()
    last_gpio = read_line(gpio_fd)
    last_syns = 0
    last_report = 0.0

    def stamp():
        return time.clock_gettime(time.CLOCK_MONOTONIC) - boot

    print("# t=uptime-relative seconds, gpio125 0=asserted")
    print("%9.2f  START      gpio=%d irq=%d evdev_syn=%d"
          % (stamp(), last_gpio, last_irq, syns))
    sys.stdout.flush()

    end = time.time() + secs
    while time.time() < end:
        if ev_fd >= 0:
            while True:
                try:
                    raw = os.read(ev_fd, INPUT_EVENT_SIZE * 64)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if not raw:
                    break
                for off in range(0, len(raw) - INPUT_EVENT_SIZE + 1,
                                 INPUT_EVENT_SIZE):
                    _, _, etype, _, _ = struct.unpack_from(
                        INPUT_EVENT_FMT, raw, off)
                    if etype == EV_SYN:
                        syns += 1

        gpio = read_line(gpio_fd)
        irq = irq_count()
        now = stamp()

        changed = (gpio != last_gpio or irq != last_irq or syns != last_syns)
        if changed or now - last_report >= HEARTBEAT_S:
            print("%9.2f  %-9s gpio=%d irq=%d(+%d) evdev_syn=%d(+%d)"
                  % (now, "CHANGE" if changed else "idle", gpio,
                     irq, irq - last_irq, syns, syns - last_syns))
            sys.stdout.flush()
            last_report = now
            last_gpio, last_irq, last_syns = gpio, irq, syns

        time.sleep(SAMPLE_S)

    print("%9.2f  END        gpio=%d irq=%d evdev_syn=%d"
          % (stamp(), read_line(gpio_fd), irq_count(), syns))
    os.close(gpio_fd)
    if ev_fd >= 0:
        os.close(ev_fd)


if __name__ == "__main__":
    main()
