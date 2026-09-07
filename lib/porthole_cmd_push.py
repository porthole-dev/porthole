#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole push` -- put a helper on the device somewhere that survives a reboot.

Everything scp'd to /tmp dies on reboot, and a bring-up session reboots
constantly -- a dozen times in the day that produced docs/RETRO-2026-08-26.md,
with every helper re-pushed each time. That is an hour of nothing.

So: install to a real filesystem, on PATH, once. `/usr/local/bin` by default,
which means a pushed helper is runnable by name rather than by path.

**It does not survive a rootfs reflash**, and that is the honest limit rather
than a bug: a reflash replaces the filesystem this writes to. Anything that must
survive one belongs in the device package, where it is versioned and reviewed --
see `brain/laws/shipped-configuration-is-not-running-configuration.md`, which is
about exactly the state that lives only on a filesystem someone is about to
replace.
"""
from __future__ import annotations

import hashlib
import pathlib
import shlex

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_STATE, EX_USAGE

DEFAULT_SCRATCH = "/usr/local/bin"
# One name per line, so `push clear` removes what porthole put there and nothing
# else. Removing every file in /usr/local/bin because porthole was pointed at it
# would be an unpleasant surprise on a device that had other things installed.
MANIFEST = ".porthole-pushed"


def _scratch(ctx) -> str:
    return ctx.cfg.get("PORTHOLE_DEVICE_SCRATCH") or DEFAULT_SCRATCH


def _require_booted(ctx):
    state = ctx.device().state()
    if state != "BOOTED":
        raise Bail(f"the device is {state}, not BOOTED", EX_STATE,
                   "push writes to the device's filesystem over ssh")
    return ctx.device()


def cmd_push(args, ctx) -> int:
    scratch = _scratch(ctx)
    q = shlex.quote
    manifest = f"{scratch}/{MANIFEST}"

    # Actions are positional here, as they are for `brain` and `sandbox`: the
    # CLI grammar forbids encoding an action as a flag. Detected rather than
    # declared as argparse `choices`, because a positional with choices swallows
    # the first filename.
    files = list(args.files or [])
    action = files[0] if files[:1] in (["list"], ["clear"]) else None
    if action:
        files = files[1:]

    if action == "list":
        dev = _require_booted(ctx)
        out = dev.run(f"cat {q(manifest)} 2>/dev/null", timeout=15) or ""
        names = [n for n in out.split() if n]

        def render():
            o = ctx.out
            o.heading(f"pushed to {scratch}")
            if not names:
                o("  nothing")
                return
            for n in names:
                o(f"  {n}")
        return ctx.emit({"scratch": scratch, "pushed": names}, render)

    if action == "clear":
        dev = _require_booted(ctx)
        if not args.yes:
            out = dev.run(f"cat {q(manifest)} 2>/dev/null", timeout=15) or ""
            names = [n for n in out.split() if n]

            def render():
                o = ctx.out
                o.heading(f"would remove {len(names)} file(s) from {scratch}")
                for n in names:
                    o(f"  {n}")
                o.blank()
                o.hint("porthole push clear --yes")
            return ctx.emit({"scratch": scratch, "would_remove": names}, render)
        rc = dev.run_full(
            f"cd {q(scratch)} 2>/dev/null || exit 0; "
            f"[ -f {q(MANIFEST)} ] || exit 0; "
            f"while read -r n; do [ -n \"$n\" ] && sudo -n rm -f -- \"$n\"; done "
            f"< {q(MANIFEST)}; sudo -n rm -f {q(MANIFEST)}", timeout=30)[0]
        if rc != 0:
            raise Bail("could not clear the scratch", EX_FAIL)
        ctx.out(ctx.out.paint(f"  cleared {scratch}", "green"))
        return EX_OK

    if not files:
        raise Bail("nothing to push", EX_USAGE,
                   "porthole push tools/ph-foo.sh   (or: push list | push clear)")

    paths = []
    for name in files:
        p = pathlib.Path(name).expanduser()
        if not p.is_file():
            raise Bail(f"no such file: {name}", EX_USAGE)
        paths.append(p)

    dev = _require_booted(ctx)
    pushed = []
    for p in paths:
        want = hashlib.sha256(p.read_bytes()).hexdigest()
        data = p.read_bytes()
        target = f"{scratch}/{p.name}"

        # base64 over the existing ssh channel rather than scp: one connection,
        # one multiplexed round trip, and no second authentication. These are
        # helper scripts -- kilobytes -- so the encoding overhead is noise.
        import base64
        blob = base64.b64encode(data).decode()
        script = (
            f"set -e\n"
            f"sudo -n mkdir -p {q(scratch)}\n"
            f"printf '%s' {q(blob)} | base64 -d > /tmp/{q(p.name)}.push\n"
            # Verify what LANDED, never that the transfer exited 0. A module
            # once arrived 0 bytes and the tool that sent it reported success;
            # tools/ph-push-module.sh carries the same rule and the same scar.
            f"got=$(sha256sum /tmp/{q(p.name)}.push | cut -d' ' -f1)\n"
            f"[ \"$got\" = {q(want)} ] || {{ echo \"TRANSFER CORRUPT: $got\"; exit 1; }}\n"
            f"sudo -n install -m 0755 /tmp/{q(p.name)}.push {q(target)}\n"
            f"rm -f /tmp/{q(p.name)}.push\n"
            f"inst=$(sudo -n sha256sum {q(target)} | cut -d' ' -f1)\n"
            f"[ \"$inst\" = {q(want)} ] || {{ echo \"INSTALLED COPY DIFFERS: $inst\"; exit 1; }}\n"
            f"touch_m={q(scratch)}/{q(MANIFEST)}\n"
            f"sudo -n touch \"$touch_m\"\n"
            f"grep -qxF {q(p.name)} \"$touch_m\" 2>/dev/null || "
            f"echo {q(p.name)} | sudo -n tee -a \"$touch_m\" >/dev/null\n"
            f"echo OK\n")
        rc, out, err = dev.run_full(script, timeout=60)
        if rc != 0 or "OK" not in out:
            raise Bail(f"pushing {p.name} failed", EX_FAIL,
                       (out + err).strip()[:300] or "no output from the device")
        pushed.append({"name": p.name, "target": target, "sha256": want,
                       "bytes": len(data)})

    def render():
        o = ctx.out
        o.heading(f"pushed {len(pushed)} file(s) to {scratch}")
        for item in pushed:
            o(f"  {o.paint(item['name'], 'cyan')}  "
              f"{o.paint(str(item['bytes']) + ' bytes', 'grey')}")
        o.blank()
        o(o.paint("  On PATH, and it survives a reboot. It does NOT survive a\n"
                  "  rootfs reflash -- anything that must belongs in the device\n"
                  "  package.", "grey"))
    return ctx.emit({"scratch": scratch, "pushed": pushed}, render)


SPEC = {
    "verb": "push",
    "order": 46,
    "group": "device",
    "help": "install a helper on the device where it survives a reboot",
    "description": (
        "Everything scp'd to /tmp dies on reboot, and a bring-up session\n"
        "reboots constantly -- a dozen times in one day, with every helper\n"
        "re-pushed each time.\n\n"
        "This installs to /usr/local/bin, so the helper is on PATH and outlives\n"
        "the reboot. Content is verified after it lands rather than trusting\n"
        "the transfer's exit code, and what porthole pushed is recorded so\n"
        "`push clear` removes that and nothing else.\n\n"
        "It does not survive a rootfs reflash. Anything that must belongs in\n"
        "the device package."),
    "args": [
        (["files"], {"nargs": "*", "metavar": "FILE",
                     "help": "`list`, `clear`, or local files to install"}),
        (["--yes"], {"action": "store_true", "help": "clear: actually remove"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_push,
    "examples": [
        "porthole push tools/ph-sysstate.sh",
        "porthole push list",
        "porthole push clear --yes",
    ],
}
