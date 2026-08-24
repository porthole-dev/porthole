# SPDX-License-Identifier: MIT
"""`porthole init` -- write your identity, once.

Interactive when a human is at a terminal, flag-driven otherwise, so an agent
can bootstrap headless. It never applies the sudoers change itself: see the
long comment in emit_sudo_snippet.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

import porthole
from porthole_cli import Bail, EX_FAIL, EX_OK

TEMPLATE = """\
# porthole identity -- written by `porthole init`.
#
# This file is yours: your username, your device's address, your tool paths.
# It is never committed. Device facts live in profiles/<codename>/device.env,
# which IS committed and shared.
#
# Anything here is overridden by the process environment, so a one-off
# `PHONE=other@host tools/tk-fps.py` works without editing this.

PORTHOLE_DEVICE={device}
PORTHOLE_USER={user}
PORTHOLE_HOST={host}
PORTHOLE_SSH_PORT={port}
PORTHOLE_AGENT={agent}
{extra}"""


def ask(prompt: str, default: str) -> str:
    """Prompt only when a human is present; an agent silently gets the default."""
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    try:
        got = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return got or default


def emit_sudo_snippet(ctx, user: str) -> None:
    """Print the passwordless-sudo setup. Deliberately do not apply it.

    Three reasons, all learned the hard way:
      - it must NOT ship in a device package other people install;
      - it belongs on the DEVICE, not this host, and we may not be able to
        reach the device yet;
      - an agent whose harness refuses to type a sudo password cannot install
        it, and retrying just burns turns. Hand it to the human.

    Without it, nearly every tool calls `sudo -n`, `-n` does not prompt, and
    the whole toolbox silently does nothing.
    """
    ctx.out.blank()
    ctx.out.heading("One more step, on the DEVICE")
    ctx.out("Nearly every tool calls `sudo -n`. The -n means it never prompts --")
    ctx.out("it just fails. Without passwordless sudo the toolbox silently does")
    ctx.out("nothing, and the symptom is 'every tool is broken'.")
    ctx.out.blank()
    ctx.out(ctx.out.paint(
        f"    echo '{user} ALL=(ALL) NOPASSWD: ALL' | "
        f"sudo tee /etc/sudoers.d/99-porthole-dev", "cyan"))
    ctx.out(ctx.out.paint(
        "    sudo chmod 0440 /etc/sudoers.d/99-porthole-dev", "cyan"))
    ctx.out.blank()
    ctx.out(ctx.out.paint(
        "This must NOT go into a device package other people install.", "grey"))


def cmd_init(args, ctx) -> int:
    root = ctx.root
    devices = porthole.list_profiles(root)
    interactive = sys.stdin.isatty() and not args.non_interactive

    if interactive:
        ctx.out.heading("porthole setup")
        ctx.out(f"  profiles available: {', '.join(devices) or '(none yet)'}")
        ctx.out.blank()

    device = args.codename
    if not device:
        device = ask("device codename", devices[0] if devices else "")
    if device and device not in devices:
        raise Bail(
            f"no profile for {device!r}. Known: {', '.join(devices) or '(none)'}",
            EX_FAIL, f"porthole new-device {device}   to create one")

    user = args.user or ask("ssh username on the device", "user")
    host = args.host or ask("device IP", "172.16.42.1")
    port = args.port or (ask("ssh port", "22") if interactive else "22")
    agent = args.agent or os.environ.get("USER", "")

    xdg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME")
                       or pathlib.Path.home() / ".config")
    target = xdg / "porthole" / "config.env"
    if target.exists() and not args.force:
        raise Bail(f"{target} already exists", EX_FAIL,
                   "re-run with --force to overwrite, or edit it directly")

    extra = ""
    for key, found in (("FASTBOOT", shutil.which("fastboot")),
                       ("ADB", shutil.which("adb"))):
        if found:
            extra += f"{key}={found}\n"
    pmb = pathlib.Path.home() / ".local/var/pmbootstrap"
    if pmb.is_dir():
        extra += f"PORTHOLE_PMB_DIR={pmb}\n"
    if args.workdir:
        extra += f"PORTHOLE_WORKDIR={pathlib.Path(args.workdir).expanduser()}\n"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.format(device=device, user=user, host=host,
                                      port=port, agent=agent, extra=extra))

    payload = {
        "config": str(target), "device": device, "user": user,
        "host": host, "port": port, "agent": agent,
        "autodetected": dict(
            line.split("=", 1) for line in extra.strip().splitlines() if line),
    }

    def render():
        ctx.out.blank()
        ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} wrote {target}")
        for line in extra.strip().splitlines():
            ctx.out(ctx.out.paint(f"    autodetected {line}", "grey"))
        emit_sudo_snippet(ctx, user)
        ctx.out.blank()
        ctx.out.heading("Then")
        ctx.out.hint("porthole doctor                       check host and device")
        ctx.out.hint("porthole tools                        what can I run?")
        ctx.out.hint("porthole brain search --severity law  what to know first")
        ctx.out.hint("porthole use <codename>               switch device later")

    return ctx.emit(payload, render)


SPEC = {
    "verb": "init",
    "order": 10,
    "help": "set your identity and pick a device; writes config.env",
    "description": (
        "Writes ~/.config/porthole/config.env -- your username, your device's\n"
        "address, your tool paths. Never committed.\n\n"
        "Interactive at a terminal, flag-driven otherwise, so an agent can\n"
        "bootstrap headless."),
    # The positional IS the device. `device_pos` used to exist only because the
    # injected --device selector collided with it; both go away together.
    "device_flag": False,
    "args": [
        (["codename"], {"nargs": "?", "metavar": "CODENAME",
                        "help": "device profile to use"}),
        (["--user"], {"help": "ssh username on the device"}),
        (["--host"], {"help": "device IP"}),
        (["--port"], {"help": "ssh port (default 22)"}),
        (["--agent"], {"help": "default TK_AGENT for the device mutex"}),
        (["--workdir"], {"metavar": "PATH",
                         "help": "the device working repo (kernel, pmaports)"}),
        (["--force"], {"action": "store_true", "help": "overwrite an existing config"}),
        (["--non-interactive"], {"action": "store_true",
                                 "help": "never prompt, even at a terminal"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_init,
    "examples": [
        "porthole init",
        "porthole init google-taimen --user user --host 172.16.42.1",
        "porthole init google-taimen --non-interactive",
    ],
}
