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

HEADER = """\
# porthole identity -- written by `porthole init`.
#
# This file is yours: your username, your device's address, your tool paths.
# It is never committed. Device facts live in profiles/<codename>/device.env,
# which IS committed and shared.
#
# Anything here is overridden by the process environment, so a one-off
# `PHONE=other@host tools/tk-fps.py` works without editing this.
#
# `porthole init` is safe to re-run: it reads what is here, offers it back as
# the default for every question, and rewrites only the lines you change.
# Comments and keys it does not know about are left alone.
"""


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
    o = ctx.out
    o.blank()
    o.heading("on the DEVICE, once")
    o(o.paint("  Every tool calls `sudo -n`, and -n never prompts -- it fails. "
              "Without", "grey"))
    o(o.paint("  this the whole toolbox silently does nothing, and the symptom "
              "is", "grey"))
    o(o.paint("  \"every tool is broken\".", "grey"))
    o.hint(f"echo '{user} ALL=(ALL) NOPASSWD: ALL' | "
           f"sudo tee /etc/sudoers.d/99-porthole-dev")
    o.hint("sudo chmod 0440 /etc/sudoers.d/99-porthole-dev")
    o(o.paint("  Never ship this in a device package other people install.",
              "grey"))


# ------------------------------------------------------------------ tiers --

PMBOOTSTRAP_URL = "https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git"
PMAPORTS_URL = "https://gitlab.postmarketos.org/postmarketOS/pmaports.git"


def _git(*argv, timeout=600) -> tuple[int, str]:
    import subprocess
    try:
        r = subprocess.run(["git", *argv], capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _choose_tier(ctx, args, interactive, cfg) -> str:
    """workspace or host -- the one decision that changes what this host needs.

    It is asked because the answer decides whether pmbootstrap has to exist on
    this machine at all, and nothing said so before: the image carries the CLI
    *and* its source pinned to each other, so a workspace host needs neither.
    The reference host had a hand-installed pmbootstrap that no build was
    using, which is what asking prevents.
    """
    import porthole_cmd_sandbox as sandbox

    if args.tier:
        return args.tier
    state = sandbox._container_state(pathlib.Path(ctx.root), cfg)
    if not state["podman"]:
        # Do not offer a tier that cannot run. `doctor` names the install.
        ctx.out(ctx.out.paint(
            "  podman is not installed, so builds would run on this host.",
            "yellow"))
        ctx.out.hint("porthole doctor   has the install command for podman")
        return "host"
    if not interactive:
        return "workspace"
    ctx.out.blank()
    ctx.out("  Where should builds run?")
    ctx.out("    1. the workspace -- a rootless container. Carries pmbootstrap")
    ctx.out("       and its helpers, needs no privilege, and nothing to install"
            " here.")
    ctx.out("    2. this host -- needs pmbootstrap, its chroots and its"
            " dependencies.")
    return "host" if ask("pick one", "1").strip().startswith("2") else "workspace"


def _ensure_pmbootstrap_src(ctx, cfg, interactive) -> str:
    """The checkout `helpers/envkernel.sh` lives in. HOST TIER ONLY.

    The Alpine package installs the `pmb` python package and no `helpers/`, so
    a host can have a working `pmbootstrap` CLI and still be unable to compile
    a kernel -- which is exactly how it looked to the first person who tried.
    Cloned at the tag matching the installed CLI, the same rule the image uses,
    so the helper and the CLI cannot drift apart.
    """
    current = cfg.get("PORTHOLE_PMBOOTSTRAP_SRC", "")
    if current and (pathlib.Path(current) / "helpers/envkernel.sh").is_file():
        return current                                   # already right: adopt

    # It is right there and you did not tell me -- the whole complaint.
    home = pathlib.Path.home()
    for pattern in ("*/pmbootstrap/helpers/envkernel.sh",
                    "*/*/pmbootstrap/helpers/envkernel.sh"):
        try:
            for hit in sorted(home.glob(pattern))[:1]:
                found = str(hit.parent.parent)
                ctx.out(ctx.out.paint(f"    found a pmbootstrap checkout at "
                                      f"{found}", "grey"))
                if not interactive or ask("use it? [y/n]", "y").lower().startswith("y"):
                    return found
        except OSError:
            pass

    version = ""
    pmb = shutil.which("pmbootstrap")
    if pmb:
        import subprocess
        try:
            version = subprocess.run([pmb, "--version"], capture_output=True,
                                     text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            version = ""
    dest = home / ".local/share/porthole/pmbootstrap"
    if dest.is_dir():
        return str(dest) if (dest / "helpers/envkernel.sh").is_file() else ""
    if not interactive:
        # Never clone unattended. A network fetch nobody asked for is the
        # wrong thing for an agent or a CI run to discover it has done, and
        # `doctor` already names this as a fixable warning.
        return ""
    if not ask(f"clone pmbootstrap into {dest}? [y/n]", "y").lower().startswith("y"):
        return ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = ["clone", "--depth", "1"]
    if version:
        argv += ["--branch", version]
    rc, err = _git(*argv, PMBOOTSTRAP_URL, str(dest))
    if rc != 0:
        # Not fatal. A host with no pmbootstrap source can still use the
        # workspace, and a failed clone must not stop init writing the rest.
        ctx.out.warn(f"could not clone pmbootstrap: {err.splitlines()[-1] if err else rc}")
        return ""
    if not (dest / "helpers/envkernel.sh").is_file():
        ctx.out.warn(f"{dest} has no helpers/envkernel.sh -- kernel builds "
                     f"on this host will not work")
        return ""
    ctx.out(ctx.out.paint(
        f"    cloned pmbootstrap {version or '(default branch)'} into {dest}",
        "grey"))
    return str(dest)


def _checked_pmaports(path) -> str:
    """One gate for both routes into "use this checkout".

    A path with no `device/` is not pmaports, and writing it turns every later
    "device not found" into a puzzle about the wrong thing. The interactive
    route validated and `--pmaports` did not, which is the shape of bug that
    only ever bites the headless caller.
    """
    resolved = pathlib.Path(path).expanduser()
    if not (resolved / "device").is_dir():
        raise Bail(f"{resolved} does not look like pmaports (no device/)",
                   EX_FAIL, "pass the top of the checkout")
    return str(resolved)


def _choose_pmaports(ctx, args, interactive, cfg, device) -> tuple[str, str]:
    """Point at a checkout, or clone one. Returns (config_key, value).

    An empty key means "nothing to write" -- either it already resolves, or the
    developer declined. Adopting what resolves is the common case and must not
    write a key: a redundant PORTHOLE_PMAPORTS pinned to pmbootstrap's own
    cache_git is a second place to be wrong the day pmbootstrap moves it.
    """
    import porthole_pmaports as pmap

    key = (f"PORTHOLE_PMAPORTS_{device.upper().replace('-', '_')}"
           if device else "PORTHOLE_PMAPORTS")
    if args.pmaports:
        return key, _checked_pmaports(args.pmaports)

    found = pmap.find_pmaports(cfg)
    if not interactive:
        # Adopt what resolves; never clone unattended. Same rule as the
        # pmbootstrap checkout above -- and printing a numbered menu to a pipe
        # is noise nobody can answer.
        return "", ""
    if found:
        ctx.out.blank()
        ctx.out(f"  pmaports: {ctx.out.paint(str(found), 'cyan')}")
        ctx.out("    1. use it")
        ctx.out("    2. point at a different checkout")
        ctx.out("    3. clone a fresh one")
        choice = ask("pick one", "1").strip()
        if choice.startswith("1"):
            return "", ""
    else:
        ctx.out.blank()
        ctx.out(ctx.out.paint("  no pmaports checkout found.", "yellow"))
        ctx.out("    2. point at an existing checkout")
        ctx.out("    3. clone a fresh one")
        choice = ask("pick one", "3").strip()

    if choice.startswith("2"):
        path = ask("path to pmaports", "")
        if not path:
            return "", ""
        return key, _checked_pmaports(path)

    dest = pathlib.Path.home() / ".cache/porthole/aports" / (device or "pmaports")
    if (dest / "device").is_dir():
        return key, str(dest)
    ctx.out(ctx.out.paint(f"    cloning pmaports into {dest} -- this takes a "
                          f"minute", "grey"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    rc, err = _git("clone", PMAPORTS_URL, str(dest))
    if rc != 0:
        ctx.out.warn(f"could not clone pmaports: {err.splitlines()[-1] if err else rc}")
        return "", ""
    return key, str(dest)


def _scaffold(ctx, args) -> str:
    """Create a profile from inside init, so a first run can finish.

    Delegates to new-device rather than reimplementing it: the seeding rules,
    the SoC reporting and the checklist generation all live there, and a second
    copy of them here would be a second copy to keep true.
    """
    codename = ask("codename (<vendor>-<device>, e.g. google-cheetah)", "")
    if not codename:
        raise Bail("a codename is needed to create a profile", EX_FAIL,
                   "porthole new-device <codename>")
    soc = ask("SoC, if you know it (vendor-prefixed, e.g. qcom-sdm845)", "")
    argv = [sys.executable, str(pathlib.Path(ctx.root) / "bin" / "porthole"),
            "new-device", codename] + (["--soc", soc] if soc else [])
    import subprocess
    ctx.out.blank()
    if subprocess.run(argv).returncode != 0:
        raise Bail(f"could not scaffold {codename}", EX_FAIL)
    ctx.out.blank()
    return codename


def cmd_init(args, ctx) -> int:
    root = ctx.root
    devices = porthole.list_profiles(root)
    interactive = sys.stdin.isatty() and not args.non_interactive

    if interactive:
        ctx.out.heading("porthole setup")
        ctx.out(f"  profiles available: {', '.join(devices) or '(none yet)'}")
        ctx.out.blank()

    device = args.codename
    if not device and interactive:
        # A newcomer with no profiles must not be told to go and read about a
        # different verb. Offering to create one here is the difference between
        # a first run that finishes and a first run that becomes a documentation
        # search.
        if devices:
            ctx.out("  which device?")
            for i, name in enumerate(devices, 1):
                ctx.out(f"    {i}. {name}")
            ctx.out(f"    n. something not ported yet")
            ctx.out.blank()
            choice = ask("pick one", "1")
            if choice.lower() in ("n", "new"):
                device = _scaffold(ctx, args)
            elif choice.isdigit() and 1 <= int(choice) <= len(devices):
                device = devices[int(choice) - 1]
            else:
                device = choice
        else:
            ctx.out("  no device profiles yet — let us make one.")
            ctx.out.blank()
            device = _scaffold(ctx, args)
        devices = porthole.list_profiles(root)
    elif not device:
        device = ask("device codename", devices[0] if devices else "")

    if device and device not in devices:
        raise Bail(
            f"no profile for {device!r}. Known: {', '.join(devices) or '(none)'}",
            EX_FAIL, f"porthole new-device {device}   to create one")

    import porthole_cmd_use as use

    target = use.config_path()
    # Read before asking. Everything already on disk becomes the default for
    # its own question, which is what makes a second run a no-op instead of a
    # decision to make again -- and what makes this safe on a host somebody
    # else, or an earlier attempt, half-configured.
    try:
        before = porthole.parse_env(target.read_text())
    except OSError:
        before = {}

    if before and not args.force and not interactive:
        # Unchanged, and deliberately: with no human present nobody sees the
        # defaults, so taking flags over an existing identity WOULD be the
        # silent overwrite this has always refused. With a tty, the prompts
        # show the current value and Enter keeps it, which is not that.
        raise Bail(f"{target} already exists", EX_FAIL,
                   "re-run with --force to take these values, or run it "
                   "interactively to be asked key by key")

    user = args.user or ask("ssh username on the device",
                            before.get("PORTHOLE_USER", "user"))
    host = args.host or ask("device IP",
                            before.get("PORTHOLE_HOST", "172.16.42.1"))
    port = args.port or (ask("ssh port", before.get("PORTHOLE_SSH_PORT", "22"))
                         if interactive else before.get("PORTHOLE_SSH_PORT", "22"))
    agent = args.agent or before.get("PORTHOLE_AGENT") or os.environ.get("USER", "")

    resolved = {"PORTHOLE_DEVICE": device, "PORTHOLE_USER": user,
                "PORTHOLE_HOST": host, "PORTHOLE_SSH_PORT": port,
                "PORTHOLE_AGENT": agent}
    for key, found in (("FASTBOOT", shutil.which("fastboot")),
                       ("ADB", shutil.which("adb"))):
        # A value already on disk WINS over what is on PATH: someone who put an
        # unpacked platform-tools path here meant it. Still reported, as kept,
        # because a key missing from the report reads as a key not considered.
        if before.get(key) or found:
            resolved[key] = before.get(key) or found
    if args.workdir:
        resolved["PORTHOLE_WORKDIR"] = str(pathlib.Path(args.workdir).expanduser())

    # The config the tier and pmaports steps reason about is what this run is
    # about to write, not what the process was started with -- otherwise a
    # first run answers every question against an empty config.
    cfg_now = dict(ctx.cfg)
    cfg_now.update(before)
    cfg_now.update(resolved)

    tier = _choose_tier(ctx, args, interactive, cfg_now)
    if tier == "host":
        src = _ensure_pmbootstrap_src(ctx, cfg_now, interactive)
        if src:
            resolved["PORTHOLE_PMBOOTSTRAP_SRC"] = src
            cfg_now["PORTHOLE_PMBOOTSTRAP_SRC"] = src
        pmb = pathlib.Path(before.get("PORTHOLE_PMB_DIR")
                           or pathlib.Path.home() / ".local/var/pmbootstrap")
        resolved["PORTHOLE_PMB_DIR"] = str(pmb)
        cfg_now["PORTHOLE_PMB_DIR"] = str(pmb)

    pm_key, pm_val = _choose_pmaports(ctx, args, interactive, cfg_now, device)
    if pm_key:
        resolved[pm_key] = pm_val

    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(HEADER)
    # set_key rewrites one line and leaves the rest of the file -- comments,
    # hand-added keys, another device's overrides -- exactly where they were.
    verdicts = {}
    for key, value in resolved.items():
        if not value:
            continue
        verdicts[key] = ("kept" if before.get(key) == value
                         else "changed" if key in before else "added")
        if verdicts[key] != "kept":
            use.set_key(target, key, value)

    payload = {
        "config": str(target), "device": device, "user": user,
        "host": host, "port": port, "agent": agent, "tier": tier,
        "keys": {k: {"value": resolved[k], "verdict": v}
                 for k, v in verdicts.items()},
    }

    def render():
        o = ctx.out
        changed = sum(1 for v in verdicts.values() if v != "kept")
        o.blank()
        o.heading("config.env")
        o.kv("file", str(target), 9)
        o.blank()
        # The same status-row shape doctor and `sandbox status` use: a glyph
        # for the eye and a word for the grep. init inventing its own layout is
        # what made the one command a newcomer reads first the one that looks
        # unlike the rest of the tool.
        width = max((len(k) for k in verdicts), default=0)
        for key, verdict in verdicts.items():
            kind = {"kept": "skip", "changed": "warn", "added": "ok"}[verdict]
            o("  {}  {}  {}".format(
                o.status(kind, verdict, 7),
                o.paint("{:<{}}".format(key, width), "grey"),
                resolved[key]))
        if not changed:
            o.blank()
            o(o.paint("  nothing to change -- this host was already set up",
                      "grey"))

        o.blank()
        o.heading("builds")
        if tier == "workspace":
            o.kv("where", "the workspace (rootless container)", 9)
            # The sentence that would have saved this project an afternoon.
            o.kv("", o.paint("the image carries pmbootstrap and its helpers "
                             "-- do not install it here", "grey"), 9)
            o.hint("porthole sandbox up      build the image and start it")
        else:
            o.kv("where", "this host", 9)
            o.kv("", o.paint("needs pmbootstrap, its chroots and its "
                             "dependencies", "grey"), 9)

        emit_sudo_snippet(ctx, user)

        o.blank()
        o.heading("next")
        # ONE next step, not three. `next` is the verb whose whole job is to
        # know which one, so printing its answer AND an invitation to run it
        # was the same suggestion twice.
        try:
            import porthole_cmd_next as nxt
            _, summary, _ = nxt.collect(ctx)
            step = summary.get("next")
            if step and step.get("command"):
                o.kv("step", step["title"], 9)
                o.hint(step["command"])
        except Exception:  # noqa: BLE001 -- init must finish even if next cannot
            pass
        o.hint("porthole doctor          check host and device")

    return ctx.emit(payload, render)


SPEC = {
    "verb": "init",
    "order": 10,
    "help": "set this host up: identity, build tier, pmaports",
    "description": (
        "Sets this host up, and is safe to re-run on a half-configured one:\n"
        "it reads what is already there, offers it back as the default for\n"
        "every question, and rewrites only the lines you change.\n\n"
        "Writes ~/.config/porthole/config.env -- your username, your device's\n"
        "address, your tool paths. Never committed.\n\n"
        "Interactive at a terminal, flag-driven otherwise, so an agent can\n"
        "bootstrap headless."),
    # It calls ask() in a loop and would otherwise appear to hang in a
    # drawer with no visible prompt. The console hands the real tty over.
    "interactive": True,
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
        (["--tier"], {"choices": ["workspace", "host"],
                      "help": "where builds run (default: workspace)"}),
        (["--pmaports"], {"metavar": "PATH",
                          "help": "adopt this pmaports checkout"}),
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
        "porthole init google-taimen --tier host --pmaports ~/src/pmaports",
    ],
}
