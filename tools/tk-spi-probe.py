#!/usr/bin/env python3
# scope: soc:msm8998
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Poke the taimen touch controller (LG SW49408) over spidev.

Run ON THE DEVICE, as root. Drives the reset line, then does full-duplex
transfers and prints what comes back. Nothing here is device-specific beyond
the GPIO numbers and the reset timing, both taken from factory dtbo entry 12.

Stdlib only: there is no py3-spidev, no libgpiod and no /sys/class/gpio on this
device (CONFIG_GPIO_SYSFS is off), so both SPI and GPIO go through raw ioctls.

  tk-spi-probe.py [DEV] [SPEED_HZ]
  tk-spi-probe.py poll [DEV] [SPEED_HZ] [LEN] [COUNT]
"""
import ctypes
import fcntl
import os
import struct
import sys
import time

SPI_IOC_MAGIC = ord('k')
GPIO_MAGIC = 0xB4

# struct spi_ioc_transfer is exactly 32 bytes on 6.0:
#   u64 tx_buf, u64 rx_buf, u32 len, u32 speed_hz, u16 delay_usecs,
#   u8 bits_per_word, cs_change, tx_nbits, rx_nbits, word_delay_usecs, pad
XFER_FMT = "<QQIIHBBBBBB"
XFER_SIZE = struct.calcsize(XFER_FMT)
assert XFER_SIZE == 32, XFER_SIZE


def _iow(nr, size):
    return (1 << 30) | (size << 16) | (SPI_IOC_MAGIC << 8) | nr


SPI_IOC_WR_MODE = _iow(1, 1)
SPI_IOC_WR_BITS_PER_WORD = _iow(3, 1)
SPI_IOC_WR_MAX_SPEED_HZ = _iow(4, 4)
SPI_IOC_MESSAGE_1 = _iow(0, XFER_SIZE)

# TLMM is /dev/gpiochip0 -- label "3400000.pinctrl", 150 lines. Verified on
# device. CONFIG_GPIO_SYSFS is off and there is no libgpiod, so the character
# device is the only way in.
GPIOCHIP = "/dev/gpiochip0"
RESET_GPIO = 89       # tlmm 89, from the vendor node's reset-gpio
IRQ_GPIO = 125        # tlmm 125, from the vendor node's irq-gpio
HW_RESET_MS = 90      # vendor hw_reset_delay

# struct gpiohandle_request: u32 lineoffsets[64], u32 flags,
#   u8 default_values[64], char consumer_label[32], u32 lines, int fd == 364
HANDLE_REQ_FMT = "<64II64B32sIi"
GPIOHANDLE_REQUEST_OUTPUT = 1 << 1
GPIOHANDLE_REQUEST_INPUT = 1 << 0
GPIO_GET_LINEHANDLE = (3 << 30) | (364 << 16) | (GPIO_MAGIC << 8) | 0x03
GPIOHANDLE_GET_LINE_VALUES = (3 << 30) | (64 << 16) | (GPIO_MAGIC << 8) | 0x08
GPIOHANDLE_SET_LINE_VALUES = (3 << 30) | (64 << 16) | (GPIO_MAGIC << 8) | 0x09


def _request_line(gpio, flags, label=b"tk-spi-probe"):
    offsets = [0] * 64
    offsets[0] = gpio
    defaults = [0] * 64
    req = struct.pack(HANDLE_REQ_FMT, *offsets, flags, *defaults, label, 1, 0)
    buf = bytearray(req)
    with open(GPIOCHIP, "rb") as chip:
        fcntl.ioctl(chip, GPIO_GET_LINEHANDLE, buf)
    return struct.unpack(HANDLE_REQ_FMT, bytes(buf))[-1]


def _set_line(handle_fd, value):
    data = bytearray(64)
    data[0] = value
    fcntl.ioctl(handle_fd, GPIOHANDLE_SET_LINE_VALUES, data)


def read_irq_line():
    """Current level of the touch IRQ line (active low per the vendor DT)."""
    fd = _request_line(IRQ_GPIO, GPIOHANDLE_REQUEST_INPUT)
    try:
        data = bytearray(64)
        fcntl.ioctl(fd, GPIOHANDLE_GET_LINE_VALUES, data)
        return data[0]
    finally:
        os.close(fd)


def gpio_reset_hold(gpio=RESET_GPIO, hold_ms=10, settle_ms=HW_RESET_MS):
    """Pulse reset low then high, and RETURN THE STILL-OPEN handle.

    The caller must keep the returned fd open for as long as it intends to
    talk to the chip. Closing it releases the line back to gpiolib, the pin
    goes high-impedance, and with no bias configured on tlmm 89 it floats --
    which drops the chip back into reset. An earlier version of this script
    closed the handle before doing any transfer and read back all zeroes.
    """
    fd = _request_line(gpio, GPIOHANDLE_REQUEST_OUTPUT)
    _set_line(fd, 0)
    time.sleep(hold_ms / 1000.0)
    _set_line(fd, 1)
    time.sleep(settle_ms / 1000.0)
    return fd


def spi_open(dev, speed_hz, mode=0):
    fd = open(dev, "rb+", buffering=0)
    fcntl.ioctl(fd, SPI_IOC_WR_MODE, struct.pack("B", mode))
    fcntl.ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", 8))
    fcntl.ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("<I", speed_hz))
    return fd


def spi_xfer(fd, tx, speed_hz=1000000):
    """Full-duplex transfer. Returns the same number of bytes it sent."""
    n = len(tx)
    tx_buf = ctypes.create_string_buffer(bytes(tx), n)
    rx_buf = ctypes.create_string_buffer(n)
    msg = struct.pack(XFER_FMT,
                      ctypes.addressof(tx_buf), ctypes.addressof(rx_buf),
                      n, speed_hz, 0, 8, 0, 0, 0, 0, 0)
    fcntl.ioctl(fd, SPI_IOC_MESSAGE_1, msg)
    return bytes(rx_buf.raw[:n])


def probe(dev, speed):
    print(f"irq line (gpio{IRQ_GPIO}) before reset: {read_irq_line()}")
    print(f"resetting via gpio{RESET_GPIO}, holding it deasserted ...")
    rst = gpio_reset_hold()
    try:
        print(f"irq line after reset (reset still held high): {read_irq_line()}")
        fd = spi_open(dev, speed)
        try:
            for mode in (0, 3):
                fcntl.ioctl(fd, SPI_IOC_WR_MODE, struct.pack("B", mode))
                print(f"-- spi mode {mode} @ {speed} Hz")
                for length in (4, 8, 16):
                    rx = spi_xfer(fd, bytes(length), speed)
                    print(f"   tx {length:2d} zero bytes -> rx {rx.hex(' ')}")
                rx = spi_xfer(fd, b"\xff" * 8, speed)
                print(f"   tx  8 x 0xff      -> rx {rx.hex(' ')}")
        finally:
            fd.close()
    finally:
        os.close(rst)


# --- SW49408 register protocol, from LGE's downstream touch_sw49408.c ---
# Read:  tx[0] = (size > 4 ? 0x20 : 0x00) | ((addr >> 8) & 0x0f)
#        tx[1] = addr & 0xff, then zero padding to R_HEADER_SIZE.
#        The transfer is R_HEADER_SIZE + size bytes; payload starts at
#        rx[R_HEADER_SIZE]. Write uses 0x40 in the top nibble instead.
R_HEADER_SIZE = 6
W_HEADER_SIZE = 2

# Fixed (non-bootstrapped) registers -- the dynamic chip-info addresses live
# in a register map the IC itself reports, so these are what we can hit cold.
FIXED_REGS = [
    (0x011, "spr_boot_st"),
    (0x022, "spr_subdisp_st"),
    (0x006, "spr_rst_ctl"),
    (0xFE0, "SPI_RST_CTL"),
    (0xFE1, "SPI_CLK_CTL"),
    (0xFE2, "SPI_OSC_CTL"),
    (0x000, "tc_version(base)"),
]


def reg_read(fd, addr, size=4, speed=1000000):
    """One SW49408 register read. Returns the payload bytes."""
    tx = bytearray(R_HEADER_SIZE + size)
    tx[0] = (0x20 if size > 4 else 0x00) | ((addr >> 8) & 0x0F)
    tx[1] = addr & 0xFF
    rx = spi_xfer(fd, bytes(tx), speed)
    return rx[R_HEADER_SIZE:], rx[:R_HEADER_SIZE]


def regs(dev, speed):
    """Read the fixed registers using the real framing, reset held."""
    rst = gpio_reset_hold()
    try:
        print(f"irq={read_irq_line()} after reset")
        for mode in (0, 3):
            fd = spi_open(dev, speed, mode=mode)
            try:
                print(f"-- spi mode {mode} @ {speed} Hz")
                for addr, name in FIXED_REGS:
                    payload, hdr = reg_read(fd, addr, 4, speed)
                    print(f"   {name:16s} 0x{addr:03x}  hdr {hdr.hex(' ')}"
                          f"  data {payload.hex(' ')}")
            finally:
                fd.close()
    finally:
        os.close(rst)


def reg_write(fd, addr, value, speed=1000000):
    """One SW49408 32-bit register write: 0x40 | addr_hi, addr_lo, then data."""
    tx = bytearray(W_HEADER_SIZE + 4)
    tx[0] = 0x40 | ((addr >> 8) & 0x0F)
    tx[1] = addr & 0xFF
    tx[2:6] = value.to_bytes(4, "little")
    spi_xfer(fd, bytes(tx), speed)


def wrtest(dev, speed):
    """Write-then-readback. THE test for 'is anything on this bus'.

    A blank SW49408 legitimately reads 0 from status registers, so all-zero
    reads alone do not prove the link is dead. But a register we write and
    read back must return what we wrote -- if it does, the part is present
    and only needs firmware; if it does not, we are talking to nothing.
    """
    SPR_SRAM_CTL = 0x010
    for mode in (0, 1, 2, 3):
        rst = gpio_reset_hold()
        try:
            fd = spi_open(dev, speed, mode=mode)
            try:
                before, _ = reg_read(fd, SPR_SRAM_CTL, 4, speed)
                for val in (3, 0):
                    reg_write(fd, SPR_SRAM_CTL, val, speed)
                    back, _ = reg_read(fd, SPR_SRAM_CTL, 4, speed)
                    got = int.from_bytes(back, "little")
                    flag = "MATCH" if got == val else "no"
                    print(f"mode {mode}: wrote 0x{val:x} -> read 0x{got:08x}"
                          f"  [{flag}]  (pre-write {before.hex()})")
            finally:
                fd.close()
        finally:
            os.close(rst)


def irqwatch(seconds=20):
    """Watch the touch IRQ line, reset held deasserted. Touch the screen.

    This is independent of SPI entirely: if the IC's firmware is running it
    asserts IRQ (active low) on a touch, whether or not we can talk to it.
    """
    rst = gpio_reset_hold()
    try:
        fd = _request_line(IRQ_GPIO, GPIOHANDLE_REQUEST_INPUT)
        try:
            data = bytearray(64)
            fcntl.ioctl(fd, GPIOHANDLE_GET_LINE_VALUES, data)
            last = data[0]
            print(f"idle irq level = {last}  (active low: 0 means asserted)")
            print(f"watching gpio{IRQ_GPIO} for {seconds}s -- TOUCH THE SCREEN")
            end = time.time() + seconds
            changes = 0
            while time.time() < end:
                fcntl.ioctl(fd, GPIOHANDLE_GET_LINE_VALUES, data)
                if data[0] != last:
                    changes += 1
                    print(f"  {time.time() % 1000:8.3f}  irq -> {data[0]}")
                    last = data[0]
                time.sleep(0.002)
            print(f"done: {changes} transitions")
        finally:
            os.close(fd)
    finally:
        os.close(rst)


def poll(dev, speed, length, count):
    """Repeatedly read `length` bytes and print only when the data changes."""
    fd = spi_open(dev, speed)
    last = None
    try:
        for _ in range(count):
            rx = spi_xfer(fd, bytes(length), speed)
            if rx != last:
                print(f"irq={read_irq_line()}  {rx.hex(' ')}")
                last = rx
            time.sleep(0.02)
    finally:
        fd.close()


def main():
    args = sys.argv[1:]
    if args and args[0] == "wrtest":
        args = args[1:]
        wrtest(args[0] if args else "/dev/spidev0.0",
               int(args[1]) if len(args) > 1 else 1000000)
        return
    if args and args[0] == "irqwatch":
        irqwatch(int(args[1]) if len(args) > 1 else 20)
        return
    if args and args[0] == "regs":
        args = args[1:]
        dev = args[0] if args else "/dev/spidev0.0"
        speed = int(args[1]) if len(args) > 1 else 1000000
        regs(dev, speed)
        return
    if args and args[0] == "poll":
        args = args[1:]
        dev = args[0] if args else "/dev/spidev0.0"
        speed = int(args[1]) if len(args) > 1 else 1000000
        length = int(args[2]) if len(args) > 2 else 64
        count = int(args[3]) if len(args) > 3 else 200
        poll(dev, speed, length, count)
        return
    dev = args[0] if args else "/dev/spidev0.0"
    speed = int(args[1]) if len(args) > 1 else 1000000
    probe(dev, speed)


if __name__ == "__main__":
    main()
