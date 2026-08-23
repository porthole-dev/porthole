# SPDX-License-Identifier: MIT
"""`porthole doctor` -- will any of this work, and if not, what do I do?

This is the verb that makes porthole usable on a host that has nothing
installed. Its contract: every failure names a fix, and the fix is a command
you can paste. A check that reports a problem without a remedy is worse than
no check, because it converts "the tools do nothing" into "the tools do
nothing and something red happened".
"""
from __future__ import annotations

import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import time

from porthole_cli import EX_FAIL, EX_OK

# Package names per distro family, so the hint is copy-pasteable rather than
# "install fastboot somehow". Keys are matched against /etc/os-release ID and
# ID_LIKE, so derivatives (Mint, Pop, Manjaro, postmarketOS itself) resolve to
# their parent.
PACKAGES = {
    "fastboot": {
        "debian": "sudo apt install android-sdk-platform-tools",
        "arch": "sudo pacman -S android-tools",
        "fedora": "sudo dnf install android-tools",
        "alpine": "sudo apk add android-tools",
        "suse": "sudo zypper install android-tools",
        "macos": "brew install android-platform-tools",
    },
    "adb": {
        "debian": "sudo apt install android-sdk-platform-tools",
        "arch": "sudo pacman -S android-tools",
        "fedora": "sudo dnf install android-tools",
        "alpine": "sudo apk add android-tools",
        "suse": "sudo zypper install android-tools",
        "macos": "brew install android-platform-tools",
    },
    "ssh": {
        "debian": "sudo apt install openssh-client",
        "arch": "sudo pacman -S openssh",
        "fedora": "sudo dnf install openssh-clients",
        "alpine": "sudo apk add openssh-client",
        "suse": "sudo zypper install openssh-clients",
        "macos": "(preinstalled)",
    },
    "pmbootstrap": {
        "*": "pipx install pmbootstrap    # or: pip install --user pmbootstrap",
    },
    "shellcheck": {
        "debian": "sudo apt install shellcheck",
        "arch": "sudo pacman -S shellcheck",
        "fedora": "sudo dnf install ShellCheck",
        "alpine": "sudo apk add shellcheck",
        "macos": "brew install shellcheck",
    },
}


def distro_family() -> str:
    """Best-effort distro family for install hints. 'unknown' is fine."""
    if sys.platform == "darwin":
        return "macos"
    try:
        text = pathlib.Path("/etc/os-release").read_text()
    except OSError:
        return "unknown"
    fields = dict(re.findall(r'^(\w+)=(.*)$', text, re.M))
    ids = [fields.get("ID", "").strip('"')]
    ids += fields.get("ID_LIKE", "").strip('"').split()
    for ident in ids:
        if ident in ("debian", "ubuntu"):
            return "debian"
        if ident in ("arch", "archlinux", "manjaro"):
            return "arch"
        if ident in ("fedora", "rhel", "centos"):
            return "fedora"
        if ident in ("alpine", "postmarketos"):
            return "alpine"
        if ident in ("suse", "opensuse", "opensuse-leap", "opensuse-tumbleweed"):
            return "suse"
    return "unknown"


def install_hint(tool: str, family: str) -> str:
    table = PACKAGES.get(tool, {})
    return (table.get(family) or table.get("*")
            or f"install {tool} with your package manager")


class Checks:
    """Collects verdicts.

    A failing check with no fix raises: the contract is enforced in code, not
    left to reviewer diligence.
    """

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, name, status, detail="", fix="", doc=""):
        if status == "fail" and not fix:
            raise AssertionError(f"failing check {name!r} offers no fix")
        self.rows.append({"name": name, "status": status, "detail": detail,
                          "fix": fix, "doc": doc})

    def worst(self) -> str:
        for level in ("fail", "warn"):
            if any(r["status"] == level for r in self.rows):
                return level
        return "ok"

    def counts(self) -> dict:
        out = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
        for row in self.rows:
            out[row["status"]] += 1
        return out


def _resolve(cfg, key, default):
    value = cfg.get(key) or default
    return shutil.which(value) or (value if os.path.isfile(value) else None)


def check_host(ch: Checks, cfg, family: str) -> None:
    ch.add("host: python", "ok" if sys.version_info >= (3, 8) else "fail",
           f"{platform.python_version()} at {sys.executable}",
           "" if sys.version_info >= (3, 8) else
           "porthole needs python 3.8+; install a newer python3")

    for tool, key, required, why in (
        ("ssh", None, True, "every device command goes over ssh"),
        ("fastboot", "FASTBOOT", True, "the only reliable way to reach the bootloader"),
        ("adb", "ADB", False, "only for talking to a stock or recovery system"),
        ("pmbootstrap", None, False, "needed to build and flash, not to probe"),
        ("shellcheck", None, False, "only for `make lint` when contributing"),
    ):
        found = _resolve(cfg, key, tool) if key else shutil.which(tool)
        if found:
            ch.add(f"host: {tool}", "ok", found)
        elif required:
            ch.add(f"host: {tool}", "fail", f"not found -- {why}",
                   install_hint(tool, family))
        else:
            ch.add(f"host: {tool}", "warn", f"not found -- {why}",
                   doc=install_hint(tool, family))

    # flock backs the device mutex. Without it parallel workers corrupt each
    # other's sessions, which is a subtle failure rather than a loud one.
    if shutil.which("flock"):
        ch.add("host: flock", "ok", shutil.which("flock"))
    else:
        ch.add("host: flock", "warn",
               "not found -- the device mutex cannot serialise parallel workers",
               doc="part of util-linux; on macOS: brew install flock")


def check_profile(ch: Checks, cfg, root: pathlib.Path) -> None:
    device = cfg.get("PORTHOLE_DEVICE", "")
    if not device:
        ch.add("profile", "fail", "no device selected",
               "porthole init --device <codename>",
               "`porthole devices` lists what exists")
        return
    ch.add("profile", "ok", device)

    required = ("PORTHOLE_SOC", "PORTHOLE_CODENAME", "PORTHOLE_ARCH")
    blank = [k for k in required if not cfg.get(k)]
    if blank:
        ch.add("profile: required keys", "fail", f"unset: {', '.join(blank)}",
               f"fill them in profiles/{device}/device.env")
    else:
        ch.add("profile: required keys", "ok", ", ".join(required))

    if cfg.get("PORTHOLE_CODENAME") and cfg["PORTHOLE_CODENAME"] != device:
        ch.add("profile: codename", "warn",
               f"PORTHOLE_CODENAME={cfg['PORTHOLE_CODENAME']!r} but the "
               f"directory is {device!r} -- one of them is wrong")

    if cfg.get("PORTHOLE_HAS_AB_SLOTS") == "1":
        if not cfg.get("PORTHOLE_ACTIVE_SLOT"):
            ch.add("profile: A/B slots", "warn",
                   "HAS_AB_SLOTS=1 but ACTIVE_SLOT is unset; recovery from the "
                   "bootloader cannot re-arm a slot")
        elif not cfg.get("PORTHOLE_SLOT_FORBIDDEN"):
            ch.add("profile: A/B slots", "warn",
                   "no SLOT_FORBIDDEN set -- porthole cannot stop you arming a "
                   "slot with no good image",
                   doc="leave empty only if both slots are genuinely safe")
        else:
            ch.add("profile: A/B slots", "ok",
                   f"active={cfg['PORTHOLE_ACTIVE_SLOT']} "
                   f"forbidden={cfg['PORTHOLE_SLOT_FORBIDDEN']}")

    # Measured, not inherited. Until it exists every boot verdict is a guess.
    if not cfg.get("PORTHOLE_REBOOT_BUDGET_S"):
        ch.add("profile: timing", "warn",
               "PORTHOLE_REBOOT_BUDGET_S unset -- measure cold boot to first ssh",
               doc="brain/traps/wait-long-enough-before-calling-a-boot-failed.md")


def check_identity(ch: Checks, cfg) -> None:
    # Judge the resolved target, not one input to it: somebody who exports
    # PHONE has a good identity even with PORTHOLE_USER unset.
    if cfg.source("PHONE") == "derived" and cfg.source("PORTHOLE_USER") == "default":
        ch.add("identity", "warn",
               f"nothing set a username; ssh will use {cfg['PORTHOLE_USER']!r}",
               doc="porthole init")
    else:
        ch.add("identity", "ok", f"{cfg['PHONE']} (via {cfg.source('PHONE')})")


def check_device(ch: Checks, ctx, cfg) -> None:
    dev = ctx.device()
    start = time.monotonic()
    state = dev.state()
    elapsed = (time.monotonic() - start) * 1000
    detail = f"{state} ({elapsed:.0f}ms)"

    if state == "BOOTED":
        ch.add("device: state", "ok", detail)
        rc, _, _ = dev.run_full("sudo -n true", timeout=8)
        if rc == 0:
            ch.add("device: sudo -n", "ok", "passwordless sudo works")
        else:
            user = cfg["PHONE"].partition("@")[0]
            ch.add("device: sudo -n", "fail",
                   "sudo -n fails; nearly every tool calls it, and -n does not "
                   "prompt -- it fails. They will all silently do nothing.",
                   f"on the DEVICE, once per install:\n"
                   f"          echo '{user} ALL=(ALL) NOPASSWD: ALL' | "
                   f"sudo tee /etc/sudoers.d/99-porthole-dev\n"
                   f"          sudo chmod 0440 /etc/sudoers.d/99-porthole-dev",
                   "must NOT ship in a device package others install")
        _, ver, _ = dev.run_full("cat /proc/version", timeout=8)
        if ver:
            ch.add("device: kernel", "ok", " ".join(ver.split()[:3]))
        _, py, _ = dev.run_full("command -v python3", timeout=8)
        if py:
            ch.add("device: python3", "ok", py.strip())
        elif cfg.get("PORTHOLE_REBOOT_MODE_VIA_SYSCALL") == "1":
            ch.add("device: python3", "warn",
                   "absent, so reaching the bootloader falls back to burning "
                   "boot retries (minutes instead of ~9s)",
                   doc="apk add python3 on the device")
    elif state == "FASTBOOT":
        ch.add("device: state", "warn",
               f"{detail} -- in the bootloader; nothing over ssh will work",
               doc="tools/tk-reboot.sh, or fastboot set_active + reboot")
    elif state == "FROZEN":
        ch.add("device: state", "warn",
               f"{detail} -- kernel alive, userspace gone",
               doc="brain/traps/frozen-is-not-hung.md; tools/tk-recover.sh")
    else:
        ch.add("device: state", "warn",
               f"{detail} -- not reachable. Suspended, powered off, or "
               f"unplugged. Not a failure if you are working offline.")


HEADER_FIELDS = ("scope", "needs", "env", "exits")


def check_tools(ch: Checks, root: pathlib.Path) -> None:
    """Every tool must be self-describing in its first 30 lines."""
    tools = [p for p in sorted((root / "tools").iterdir())
             if p.is_file() and not p.is_symlink()
             and p.suffix in (".sh", ".py") or (p.is_file() and os.access(p, os.X_OK)
                                                and not p.is_symlink())]
    tools = [p for p in tools if p.name not in ("__pycache__",)]
    missing = {}
    for tool in tools:
        try:
            head = "".join(tool.read_text(errors="replace").splitlines(True)[:30])
        except OSError:
            continue
        gaps = [f for f in HEADER_FIELDS if f"{f}:" not in head]
        if gaps:
            missing[tool.name] = gaps
    if not tools:
        ch.add("tools: headers", "skip", "no tools found")
    elif missing:
        sample = ", ".join(list(missing)[:5])
        ch.add("tools: headers", "fail",
               f"{len(missing)} of {len(tools)} incomplete: {sample}"
               + (" ..." if len(missing) > 5 else ""),
               "add the standard header block; see docs/CONTRIBUTING.md",
               "`porthole tools --lint` lists every gap")
    else:
        ch.add("tools: headers", "ok", f"all {len(tools)} self-describing")


def bench(ch: Checks, ctx) -> None:
    """Measure the budgets in docs/PERFORMANCE.md rather than claiming them."""
    dev = ctx.device()
    if dev.state() != "BOOTED":
        ch.add("bench", "skip", "device is not booted")
        return

    def timed(label, fn, budget_ms):
        start = time.monotonic()
        fn()
        ms = (time.monotonic() - start) * 1000
        ch.add(f"bench: {label}", "ok" if ms <= budget_ms else "warn",
               f"{ms:.0f}ms (budget {budget_ms}ms)",
               doc="" if ms <= budget_ms else "docs/PERFORMANCE.md")

    dev.run("true")                                    # warm the master
    timed("ssh round trip (warm)", lambda: dev.run("true"), 30)
    timed("fastboot devices", dev.in_fastboot, 250)
    timed("device state", dev.state, 100)


def cmd_doctor(args, ctx) -> int:
    family = distro_family()
    ch = Checks()
    cfg = ctx.cfg

    check_host(ch, cfg, family)
    check_profile(ch, cfg, ctx.root)
    check_identity(ch, cfg)
    if args.no_device:
        ch.add("device: state", "skip", "--no-device")
    else:
        check_device(ch, ctx, cfg)
    if args.tools or args.all:
        check_tools(ch, ctx.root)
    if args.bench and not args.no_device:
        bench(ch, ctx)

    payload = {"verdict": ch.worst(), "counts": ch.counts(),
               "distro_family": family, "checks": ch.rows}

    def render():
        mark = {"ok": ("ok", "green"), "warn": ("warn", "yellow"),
                "fail": ("FAIL", "red"), "skip": ("skip", "grey")}
        width = max(len(r["name"]) for r in ch.rows)
        for row in ch.rows:
            label, colour = mark[row["status"]]
            ctx.out(f"  {ctx.out.paint(f'{label:>4}', colour)}  "
                    f"{row['name']:<{width}}  {row['detail']}")
            if row["fix"]:
                ctx.out(ctx.out.paint(f"        fix: {row['fix']}", "cyan"))
            elif row["doc"]:
                ctx.out(ctx.out.paint(f"        see: {row['doc']}", "grey"))
        counts = ch.counts()
        ctx.out.blank()
        summary = (f"{counts['ok']} ok, {counts['warn']} warn, "
                   f"{counts['fail']} fail, {counts['skip']} skipped")
        ctx.out({"ok": ctx.out.paint("everything checks out", "green"),
                 "warn": ctx.out.paint("usable, with warnings", "yellow"),
                 "fail": ctx.out.paint("something needs fixing", "red"),
                 }[ch.worst()] + ctx.out.paint(f"  ({summary})", "grey"))
        if ch.worst() == "fail":
            ctx.out.blank()
            ctx.out("Nothing above is fatal to the repo -- fix the FAIL lines "
                    "and re-run `porthole doctor`.")

    ctx.emit(payload, render)
    return EX_FAIL if ch.worst() == "fail" else EX_OK


SPEC = {
    "verb": "doctor",
    "order": 20,
    "help": "check the host, the profile and the device; name every fix",
    "description": (
        "The first thing to run on a new host, and the first thing to run when\n"
        "'all the tools are broken'. Every failure names a command that fixes\n"
        "it, chosen for your distribution where that matters.\n\n"
        "Exits non-zero only on FAIL. Warnings are things you can work without."),
    "args": [
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
        (["--tools"], {"action": "store_true",
                       "help": "also check every tool is self-describing"}),
        (["--bench"], {"action": "store_true",
                       "help": "also measure the performance budgets"}),
        (["--all"], {"action": "store_true", "help": "every check"}),
        (["--no-device"], {"action": "store_true",
                           "help": "skip anything that touches the device"}),
    ],
    "run": cmd_doctor,
    "examples": [
        "porthole doctor",
        "porthole doctor --no-device        # host only, device unplugged",
        "porthole doctor --all --json       # everything, for an agent",
        "porthole doctor --bench            # measure, do not trust, the budgets",
    ],
}
