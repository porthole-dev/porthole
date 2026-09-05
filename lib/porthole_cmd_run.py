# SPDX-License-Identifier: MIT
"""`porthole run` -- run a tool with the config applied.

Optional convenience, never mandatory: tools resolve config themselves, so
`tools/tk-fps.py` works directly and always will. This exists for the cases
where it genuinely helps -- running a profile-scoped tool without knowing which
directory it landed in, and holding the device mutex without spelling it out.
"""
from __future__ import annotations

import os
import shlex
import subprocess

from porthole_cli import Bail, EX_FAIL, child_env
from porthole_cmd_tools import collect


def cmd_run(args, ctx) -> int:
    cfg = ctx.cfg
    device = cfg.get("PORTHOLE_DEVICE", "")
    tools = {t.name: t for t in collect(ctx.root, device)}
    tools.update({t.path.stem: t for t in collect(ctx.root, device)
                  if t.path.stem not in tools})

    # `porthole run tools/tk-reboot.sh` is what the skills, the rung messages
    # and every runbook print, and it was rejected: the table is keyed on the
    # bare name. Fixing the caller means fixing every caller forever, so the
    # lookup takes the basename instead (#37).
    want = os.path.basename(args.tool)
    tool = tools.get(want)
    if tool is None:
        near = [n for n in tools if want in n]
        raise Bail(f"no tool named {want!r}", EX_FAIL,
                   f"did you mean: {', '.join(sorted(near)[:5])}?" if near
                   else "`porthole tools` lists them all")

    env = child_env(os.environ)
    env.update({k: str(v) for k, v in cfg.items()})
    env["PORTHOLE_ROOT"] = str(ctx.root)

    # An `on-device` tool executes ON the phone. Running it locally produces
    # plausible, entirely wrong output -- host load averages and host process
    # names, with nothing to signal the mistake. Pipe it over instead, which is
    # what each of those tools documents in its own header.
    if tool.needs.lower().startswith("on-device"):
        return _run_on_device(tool, args, ctx, cfg)

    argv = [str(tool.path), *args.args]
    if args.lock:
        # Declaring the state is the whole point of the mutex: exit 76 in a
        # second beats queueing ten minutes for a device that was never going
        # to answer. See brain/laws/the-lock-says-who-not-what.md.
        need = tool.needs.upper()
        wrapper = [str(ctx.root / "tools" / "tk-device.sh")]
        if need in ("BOOTED", "FASTBOOT"):
            wrapper.append(f"--need-{need.lower()}")
        env.setdefault("TK_AGENT", cfg.get("PORTHOLE_AGENT") or os.environ.get("USER", "porthole"))
        argv = wrapper + argv

    # A tool committed without the execute bit reached posix_spawn and came
    # back as a raw PermissionError traceback -- which reads as "porthole
    # crashed", not "chmod +x this file". Profile tools are the usual victims:
    # they are added by hand, and nothing on the way in checks the mode.
    if not os.access(tool.path, os.X_OK):
        raise Bail(f"{tool.path.name} is not executable", EX_FAIL,
                   f"chmod +x {tool.path}")
    try:
        return subprocess.run(argv, env=env).returncode
    except PermissionError:
        raise Bail(f"{tool.path.name} could not be executed", EX_FAIL,
                   f"chmod +x {tool.path}") from None
    except OSError as exc:
        # A missing shebang interpreter lands here too, and "Exec format error"
        # on a shell script means exactly that.
        raise Bail(f"could not run {tool.path.name}: {exc.strerror}", EX_FAIL,
                   "check the file's shebang and its execute bit") from None


def _run_on_device(tool, args, ctx, cfg) -> int:
    """Pipe a device-side script to the device and run it there.

    `sh -s` rather than scp: no file is left behind, and it works on a rootfs
    with nowhere writable. sudo -n because that is what these scripts assume --
    if it fails, `porthole doctor` explains why.
    """
    import porthole

    dev = ctx.device()
    state = dev.state()
    if state != "BOOTED":
        raise Bail(f"the device is {state}, and {tool.name} runs on the device",
                   76, "something has to move the device first; "
                       "`porthole brief` says what state it is in")

    interpreter = "sh" if tool.path.suffix != ".py" else "python3"
    remote = f"sudo -n {interpreter} -s" if interpreter == "sh" else "sudo -n python3 -"
    quoted = " ".join(shlex.quote(a) for a in args.args)
    if quoted and interpreter == "sh":
        remote = f"sudo -n {interpreter} -s -- {quoted}"

    argv = ["ssh", *porthole.ssh_opts(cfg), porthole.resolve_phone(cfg), remote]
    if args.lock:
        wrapper = [str(ctx.root / "tools" / "tk-device.sh"), "--need-booted"]
        argv = wrapper + argv

    ctx.out(ctx.out.paint(f"  running {tool.name} on the device", "grey"))
    with open(tool.path, "rb") as script:
        return subprocess.run(argv, stdin=script).returncode


SPEC = {
    "verb": "run",
    "order": 60,
    "help": "run a tool with the config applied",
    "description": (
        "Optional. Tools resolve config themselves, so `tools/tk-fps.py` works\n"
        "directly. Use this to reach a profile-scoped tool by name, or with\n"
        "--lock to take the device mutex with the right state declared."),
    "args": [
        (["tool"], {"help": "tool name, with or without extension"}),
        (["--lock"], {"action": "store_true",
                      "help": "hold the device mutex, declaring the tool's needs"}),
        (["args"], {"nargs": "...", "help": "arguments passed to the tool"}),
    ],
    # Not a report: stdout belongs to the tool being invoked, and
    # wrapping that in JSON would corrupt every tool that emits any.
    "reports": False,
    "run": cmd_run,
    "examples": [
        "porthole run tk-fps.py",
        "porthole run --lock tk-suspend-cycle.sh 20",
        "porthole run tk-sysstate.sh    # an on-device tool: piped over, not run here",
    ],
}
