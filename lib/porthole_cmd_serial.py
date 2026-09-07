# SPDX-License-Identifier: MIT
"""`porthole serial` -- the console that works when nothing else does.

Every other channel porthole uses needs software to be running: ssh needs
userspace, the USB gadget needs the kernel to have reached driver probe, the
debug shell needs an initramfs. A UART needs none of that. It is the only thing
that talks during early boot, and the only thing that says anything at all when
a kernel dies before console handover.

If you are bringing up a device that resets silently, a UART is not a
convenience -- it is the difference between debugging and guessing.

The terminal here is built in rather than shelling out to picocom or minicom,
because a bring-up host frequently has none of them and "install a terminal
emulator first" is a poor answer to "my device is printing something and I
cannot see it". termios is in the standard library.
"""
from __future__ import annotations

import os
import pathlib
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE

BAUDS = {
    50: termios.B50, 75: termios.B75, 110: termios.B110, 300: termios.B300,
    600: termios.B600, 1200: termios.B1200, 2400: termios.B2400,
    4800: termios.B4800, 9600: termios.B9600, 19200: termios.B19200,
    38400: termios.B38400, 57600: termios.B57600, 115200: termios.B115200,
    230400: termios.B230400, 460800: getattr(termios, "B460800", 0),
    921600: getattr(termios, "B921600", 0),
    1500000: getattr(termios, "B1500000", 0),
    3000000: getattr(termios, "B3000000", 0),
}

# USB-serial bridges you actually meet on a bench, by vendor:product.
KNOWN_BRIDGES = {
    "0403:6001": "FTDI FT232R", "0403:6010": "FTDI FT2232",
    "0403:6014": "FTDI FT232H", "0403:6015": "FTDI FT-X",
    "10c4:ea60": "Silicon Labs CP2102", "10c4:ea70": "Silicon Labs CP2105",
    "067b:2303": "Prolific PL2303", "1a86:7523": "CH340",
    "1a86:55d4": "CH9102", "2341:0043": "Arduino (CDC-ACM)",
}


def find_ports() -> list[dict]:
    """Every serial port on the host, with whatever identity we can recover.

    /dev/serial/by-id is the stable name and the one worth using: ttyUSB0 is
    whichever adapter enumerated first, which changes when you unplug things
    mid-session and is exactly the sort of thing that makes you flash the wrong
    device.
    """
    ports: dict[str, dict] = {}

    by_id = pathlib.Path("/dev/serial/by-id")
    if by_id.is_dir():
        for link in sorted(by_id.iterdir()):
            try:
                target = link.resolve()
            except OSError:
                continue
            ports[str(target)] = {
                "device": str(target), "stable": str(link),
                "id": link.name, "kind": "usb",
            }

    for pattern in ("ttyUSB*", "ttyACM*", "ttyAMA*", "ttyS*"):
        for dev in sorted(pathlib.Path("/dev").glob(pattern)):
            path = str(dev)
            # Built-in ttyS* are almost always absent hardware; only report one
            # if it has a real driver behind it.
            if dev.name.startswith("ttyS") and not _is_real_uart(dev.name):
                continue
            ports.setdefault(path, {
                "device": path, "stable": "", "id": dev.name,
                "kind": "usb" if "USB" in dev.name or "ACM" in dev.name else "builtin",
            })

    for info in ports.values():
        info["usb_id"] = _usb_id(info["device"])
        info["chip"] = KNOWN_BRIDGES.get(info["usb_id"], "")
        info["writable"] = os.access(info["device"], os.R_OK | os.W_OK)
    return sorted(ports.values(), key=lambda p: p["device"])


def _is_real_uart(name: str) -> bool:
    """Is there hardware behind this ttyS*, or is it an 8250 placeholder?

    Linux registers a fixed set of ttyS nodes whether or not the ports exist,
    so a plain glob reports 32 serial ports on a laptop that has none. The
    discriminator is `type`: PORT_UNKNOWN (0) means the probe found nothing.
    """
    base = pathlib.Path("/sys/class/tty") / name
    try:
        if not (base / "device").exists():
            return False
        kind = (base / "type").read_text().strip()
        return kind not in ("", "0")
    except OSError:
        return False


def _usb_id(device: str) -> str:
    """vendor:product for a USB serial device, by walking sysfs upward."""
    name = pathlib.Path(device).name
    sysfs = pathlib.Path("/sys/class/tty") / name / "device"
    try:
        node = sysfs.resolve()
    except OSError:
        return ""
    for _ in range(6):
        vid, pid = node / "idVendor", node / "idProduct"
        if vid.is_file() and pid.is_file():
            try:
                return f"{vid.read_text().strip()}:{pid.read_text().strip()}"
            except OSError:
                return ""
        if node.parent == node:
            break
        node = node.parent
    return ""


# -------------------------------------------------------------------- list --

def cmd_list(args, ctx) -> int:
    ports = find_ports()
    hidden = 0
    if not args.all:
        usb = [p for p in ports if p["kind"] == "usb"]
        hidden = len(ports) - len(usb)
        ports = usb
    payload = {"ports": ports, "count": len(ports)}

    def render():
        o = ctx.out
        if not ports:
            o.heading("no serial ports found")
            o.blank()
            o("Nothing is plugged in, or the adapter has no driver.")
            o.blank()
            o.hint("porthole serial hardware   what to buy and how to wire it")
            return
        o.heading(f"{len(ports)} serial port(s)")
        o.blank()
        width = max(len(p["device"]) for p in ports)
        for p in ports:
            chip = f"  {p['chip']}" if p["chip"] else ""
            warn = "" if p["writable"] else o.paint("  [no permission]", "red")
            o(f"  {p['device']:<{width}}{o.paint(chip, 'grey')}{warn}")
            if p["stable"]:
                o(f"  {'':<{width}}  {o.paint(p['stable'], 'grey')}")
        o.blank()
        if any(not p["writable"] for p in ports):
            o(o.paint("  A port you cannot open usually means you are not in "
                      "the dialout\n  group:", "yellow"))
            o(o.paint(f"    sudo usermod -aG dialout $USER   "
                      f"# then log out and back in", "cyan"))
            o.blank()
        if hidden:
            o(o.paint(f"  {hidden} built-in port(s) hidden — `--all` to see them",
                      "grey"))
        o.hint("porthole serial console   attach to one")

    return ctx.emit(payload, render)


# ---------------------------------------------------------------- hardware --

def cmd_hardware(args, ctx) -> int:
    """What to buy, how to wire it, and how to make the kernel talk to it."""
    cfg = ctx.cfg
    soc = cfg.get("PORTHOLE_SOC", "your SoC")
    earlycon = cfg.get("PORTHOLE_EARLYCON", "")
    baud = cfg.get("PORTHOLE_SERIAL_BAUD", "115200")

    def render():
        o = ctx.out
        o.heading("getting a UART onto a phone")
        o.blank()
        o(o.paint("  Why bother: ssh needs userspace, the USB gadget needs "
                  "driver probe,\n  the debug shell needs an initramfs. A UART "
                  "needs none of them. It is\n  the only channel that exists "
                  "before the kernel has finished booting,\n  and the only one "
                  "that says anything when it dies early.", "grey"))
        o.blank()

        o.heading("1. the adapter")
        o("  Any 3.3V USB-TTL bridge. CP2102, FT232R and CH340 are all fine and")
        o("  all cost very little.")
        o.blank()
        o(o.paint("  It MUST be 3.3V logic. A 5V adapter will damage the SoC's "
                  "UART pins.\n  Many adapters have a jumper -- check it before "
                  "the first connection,\n  not after.", "red"))
        o.blank()

        o.heading("2. finding the pins")
        o("  Phones expose a UART in one of three places, in rough order of luck:")
        o.blank()
        o(f"  {o.sym('•', '-')} {o.paint('the headphone jack', 'bold')} — many "
          f"Qualcomm phones multiplex a UART")
        o("    onto the 3.5mm jack, switched by a resistance on the mic pin")
        o("    (often ~619kΩ). This is the easiest by far: no disassembly.")
        o(f"  {o.sym('•', '-')} {o.paint('test pads on the board', 'bold')} — "
          f"labelled TX/RX/GND if you are")
        o("    fortunate. Needs the back off and usually a magnifier.")
        o(f"  {o.sym('•', '-')} {o.paint('the USB connector', 'bold')} — some "
          f"devices switch D+/D- to UART")
        o("    when a specific resistance sits on the ID pin.")
        o.blank()
        o("  Search terms that work: \"<device> uart pinout\", \"<device> serial")
        o("  console\", plus the xda-developers thread for the device.")
        o.blank()

        o.heading("3. wiring")
        o("  adapter TX  ->  device RX")
        o("  adapter RX  ->  device TX")
        o("  adapter GND ->  device GND        <- never skip this one")
        o.blank()
        o(o.paint("  TX to TX gives you silence and no error. If you see "
                  "nothing at all,\n  swap them before assuming the UART is "
                  "not exposed.", "grey"))
        o.blank()

        o.heading("4. making the kernel use it")
        if earlycon:
            o(f"  This profile records: {o.paint(earlycon, 'bold')}")
        else:
            o("  Add earlycon to the kernel cmdline. The address is the UART's")
            o(f"  register base, which you read out of {soc}'s dtsi.")
        o.blank()
        o(o.paint("    earlycon=<driver>,<address> console=ttyMSM0,"
                  f"{baud}n8", "cyan"))
        o.blank()
        o("  earlycon prints from very early in boot, before the real console")
        o("  driver is up. It is what shows you a kernel that dies before it")
        o("  would otherwise have said anything.")
        o.blank()
        o("  Record it in the profile so nobody has to find it twice:")
        o(o.paint(f'    PORTHOLE_EARLYCON="earlycon=msm_serial_dm,0xc1b0000"',
                  "cyan"))
        o(o.paint(f'    PORTHOLE_SERIAL_BAUD="115200"', "cyan"))
        o.blank()
        o.hint("porthole serial list      is the adapter visible")
        o.hint("porthole serial console   attach to it")

    return ctx.emit({"soc": soc, "earlycon": earlycon, "baud": baud}, render)


# ----------------------------------------------------------------- console --

def _open_port(device: str, baud: int):
    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    speed = BAUDS.get(baud)
    if not speed:
        os.close(fd)
        raise Bail(f"unsupported baud rate {baud}", EX_USAGE,
                   f"supported: {', '.join(str(b) for b in sorted(BAUDS) if BAUDS[b])}")
    attrs = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
    # 8N1, no flow control, receiver on, ignore modem control lines. The last
    # one matters: without CLOCAL, open() blocks forever on an adapter that is
    # not asserting carrier detect, which most TTL bridges never do.
    cflag = termios.CS8 | termios.CREAD | termios.CLOCAL
    iflag = termios.IGNPAR
    oflag = 0
    lflag = 0
    cc = list(cc)
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW,
                      [iflag, oflag, cflag, lflag, speed, speed, cc])
    termios.tcflush(fd, termios.TCIFLUSH)
    return fd


def cmd_console(args, ctx) -> int:
    ports = find_ports()
    device = args.port
    if not device:
        if not ports:
            raise Bail("no serial port found", EX_FAIL,
                       "porthole serial hardware — what to buy and how to wire it")
        # Prefer a USB bridge over a built-in port: nobody attaches a phone to
        # a motherboard COM header.
        usb = [p for p in ports if p["kind"] == "usb"]
        chosen = (usb or ports)[0]
        device = chosen["stable"] or chosen["device"]
    if not os.path.exists(device):
        raise Bail(f"{device} does not exist", EX_FAIL, "porthole serial list")
    if not os.access(device, os.R_OK | os.W_OK):
        raise Bail(f"cannot open {device}", EX_FAIL,
                   "sudo usermod -aG dialout $USER, then log out and back in")

    baud = int(args.baud or ctx.cfg.get("PORTHOLE_SERIAL_BAUD") or 115200)
    logfile = None
    if args.log:
        logfile = open(args.log, "ab", buffering=0)

    fd = _open_port(os.path.realpath(device), baud)
    stdin_fd = sys.stdin.fileno()
    interactive = sys.stdin.isatty()
    saved = termios.tcgetattr(stdin_fd) if interactive else None

    ctx.out(ctx.out.paint(
        f"  {device} @ {baud} 8N1"
        + (f"  logging to {args.log}" if logfile else ""), "grey"))
    if interactive:
        ctx.out(ctx.out.paint("  Ctrl-] to detach", "grey"))
    ctx.out.blank()

    deadline = time.monotonic() + args.timeout if args.timeout else None
    try:
        if interactive:
            tty.setraw(stdin_fd)
        while True:
            if deadline and time.monotonic() > deadline:
                break
            watch = [fd] + ([stdin_fd] if interactive else [])
            ready, _, _ = select.select(watch, [], [], 0.2)
            if fd in ready:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if data:
                    os.write(sys.stdout.fileno(), data)
                    if logfile:
                        logfile.write(data)
            if interactive and stdin_fd in ready:
                data = os.read(stdin_fd, 1024)
                if b"\x1d" in data:          # Ctrl-]
                    break
                os.write(fd, data)
    except KeyboardInterrupt:
        pass
    finally:
        if interactive and saved:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved)
        os.close(fd)
        if logfile:
            logfile.close()
        print()
    return EX_OK


ACTIONS = {"list": cmd_list, "console": cmd_console, "hardware": cmd_hardware}


def dispatch(args, ctx) -> int:
    action = args.action or "list"
    fn = ACTIONS.get(action)
    if not fn:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: {', '.join(ACTIONS)}")
    return fn(args, ctx)


SPEC = {
    "verb": "serial",
    "order": 32,
    "group": "device",
    "help": "UART console: the channel that works before anything else does",
    "description": (
        "ssh needs userspace, the USB gadget needs driver probe, the debug\n"
        "shell needs an initramfs. A UART needs none of them -- it is the only\n"
        "thing that talks during early boot, and the only thing that says\n"
        "anything when a kernel dies before console handover.\n\n"
        "The terminal is built in (termios, stdlib) rather than shelling out to\n"
        "picocom, because 'install a terminal emulator first' is a poor answer\n"
        "to 'my device is printing something and I cannot see it'."),
    # It drives termios directly and expects to own the terminal. The console
    # hands the real tty over rather than streaming it into a pane.
    "interactive": True,
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "choices": list(ACTIONS),
                      "help": "list | console | hardware"}),
        (["--port"], {"metavar": "PATH", "help": "console: which serial port"}),
        (["--baud"], {"type": int, "help": "console: baud rate (default 115200)"}),
        (["--log"], {"metavar": "FILE", "help": "console: also append to a file"}),
        (["--timeout"], {"type": float, "metavar": "SEC",
                         "help": "console: detach after this long (for scripts)"}),
        (["--all"], {"action": "store_true",
                     "help": "list: include built-in (non-USB) ports"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": dispatch,
    "examples": [
        "porthole serial hardware",
        "porthole serial list",
        "porthole serial console",
        "porthole serial console --log boot.log --timeout 120",
    ],
}
