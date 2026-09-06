# SPDX-License-Identifier: MIT
"""`porthole init` -- set this host up, once.

Interactive when a human is at a terminal, flag-driven otherwise, so an agent
can bootstrap headless. It never applies the sudoers change itself: see the
long comment in emit_sudo_snippet.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
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


# ------------------------------------------------------------- asking --

class Prompt:
    """Every question this command asks, in the toolkit's own vocabulary.

    init held the only hand-rolled interaction in the repository: a bare
    `input()` with a bracketed default, and numbered menus printed as
    `ctx.out("    1. ...")` with the indentation typed in by hand. It is the
    FIRST command a newcomer runs and it looked unlike every command they run
    afterwards -- no glyph to land on, no colour, no column the eye can follow
    down a menu, and a wrapped option that lined up with nothing.

    Nothing new is invented here. A question is a `heading`, its background is
    grey body text, its options are `kv` rows, and the input line carries the
    same `note` glyph `hint` uses -- the vocabulary `Out` already defines and
    `doctor`, `sandbox status` and `next` already print.

    Non-interactive, every method returns the default without printing a
    prompt. An agent must never be shown a menu it cannot answer.
    """

    def __init__(self, out, interactive: bool):
        self.out, self.interactive = out, interactive

    def why(self, *lines: str) -> None:
        """The background to a question. Grey, because it is read once."""
        for line in lines:
            self.out(self.out.paint(f"  {line}", "grey"))

    def _read(self, label: str, default: str) -> str:
        if not self.interactive:
            return default
        glyph = self.out.mark("note")
        suffix = self.out.paint(f" [{default}]", "grey") if default else ""
        try:
            got = input(f"  {glyph} {label}{suffix}: ").strip()
        except EOFError:
            return default
        return got or default

    def ask(self, label: str, default: str) -> str:
        return self._read(label, default)

    def yes(self, label: str, default: bool = True) -> bool:
        got = self._read(label, "y" if default else "n")
        return got.strip().lower().startswith("y")

    def choose(self, options, default: str = "1") -> str:
        """Numbered options, one `kv` row each. Returns the KEY that was picked.

        `options` is [(key, title, detail)]. The key is what comes back, so a
        caller never re-derives "which option was 2" from a string the user
        typed -- that indexing arithmetic, repeated per menu, is what made the
        two existing menus disagree about what `3` meant.
        """
        rows = list(options)
        if not self.interactive:
            return default
        width = max(len(title) for _, title, _ in rows)
        for i, (_, title, detail) in enumerate(rows, 1):
            self.out("  {}  {}{}".format(
                self.out.paint(str(i), "cyan"),
                "{:<{}}".format(title, width),
                self.out.paint(f"   {detail}", "grey") if detail else ""))
        got = self._read("pick one", default).strip()
        if got.isdigit() and 1 <= int(got) <= len(rows):
            return rows[int(got) - 1][0]
        # A key typed in full is a legitimate answer, and so is anything else:
        # returning it lets a caller treat an unrecognised reply as free text
        # (the codename menu does exactly that) instead of silently picking 1.
        return got


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
    try:
        r = subprocess.run(["git", *argv], capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _choose_tier(ctx, args, prompt, cfg) -> str:
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
    if not prompt.interactive:
        return "workspace"
    ctx.out.blank()
    ctx.out.heading("where should builds run?")
    prompt.why("Everything porthole builds -- packages, kernels, the whole "
               "system image --",
               "runs in one of these two places, and every build says which.")
    return prompt.choose([
        ("workspace", "the workspace",
         "a rootless container. Carries pmbootstrap and its "
         "helpers; nothing to install here"),
        ("host", "this host",
         "needs pmbootstrap, its chroots and its dependencies"),
    ], default="1")


def _ensure_pmbootstrap_src(ctx, cfg, prompt) -> str:
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
                ctx.out(ctx.out.paint(f"  found a pmbootstrap checkout at "
                                      f"{found}", "grey"))
                if not prompt.interactive or prompt.yes("use it?"):
                    return found
        except OSError:
            pass

    version = ""
    pmb = shutil.which("pmbootstrap")
    if pmb:
        try:
            version = subprocess.run([pmb, "--version"], capture_output=True,
                                     text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            version = ""
    dest = home / ".local/share/porthole/pmbootstrap"
    if dest.is_dir():
        return str(dest) if (dest / "helpers/envkernel.sh").is_file() else ""
    if not prompt.interactive:
        # Never clone unattended. A network fetch nobody asked for is the
        # wrong thing for an agent or a CI run to discover it has done, and
        # `doctor` already names this as a fixable warning.
        return ""
    if not prompt.yes(f"clone pmbootstrap into {dest}?"):
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
        f"  cloned pmbootstrap {version or '(default branch)'} into {dest}",
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


def _choose_pmaports(ctx, args, prompt, cfg, device) -> tuple[str, str]:
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
    if not prompt.interactive:
        # Adopt what resolves; never clone unattended. Same rule as the
        # pmbootstrap checkout above -- and printing a numbered menu to a pipe
        # is noise nobody can answer.
        return "", ""

    dest = pathlib.Path.home() / ".cache/porthole/aports" / (device or "pmaports")
    ctx.out.blank()
    ctx.out.heading("pmaports")
    prompt.why("Every device postmarketOS supports, and every package. It is "
               "where your",
               "device's kernel aport and device package live, and what "
               "`porthole build image`",
               "builds the system from.")
    if found:
        ctx.out.kv("found", str(found), 9)
        choice = prompt.choose([
            ("keep", "use it", str(found)),
            ("point", "a different checkout", "you give it a path"),
            ("clone", "clone a fresh one", str(dest)),
        ], default="1")
        if choice == "keep":
            return "", ""
    else:
        ctx.out.kv("found", ctx.out.paint("nothing on this host", "yellow"), 9)
        choice = prompt.choose([
            ("point", "an existing checkout", "you give it a path"),
            ("clone", "clone a fresh one", str(dest)),
        ], default="2")

    if choice == "point":
        path = prompt.ask("path to pmaports", "")
        if not path:
            return "", ""
        return key, _checked_pmaports(path)

    if (dest / "device").is_dir():
        return key, str(dest)
    ctx.out(ctx.out.paint(f"  cloning pmaports into {dest} -- this takes a "
                          f"minute", "grey"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    rc, err = _git("clone", PMAPORTS_URL, str(dest))
    if rc != 0:
        ctx.out.warn(f"could not clone pmaports: {err.splitlines()[-1] if err else rc}")
        return "", ""
    return key, str(dest)


# ------------------------------------------------------------ working repo --

# Where a device repo is usually kept, relative to $HOME. The parent of the
# porthole checkout is searched too and is not in this list, because it is not
# relative to $HOME -- see _workdir_candidates.
WORKDIR_ROOTS = ("", "src", "ws", "work", "workspace", "projects", "dev", "git")


def _short_name(codename: str) -> str:
    """`taimen` from `google-taimen`. The name people give the directory.

    Nobody calls the folder `google-taimen`; the reference host's is `taimen`,
    sitting beside `pmaports` and `porthole`. Searching for the codename alone
    found nothing on the one layout this tool is developed against.
    """
    return codename.split("-", 1)[1] if "-" in codename else codename


def _is_pmaports(path) -> bool:
    return ((path / "device").is_dir()
            and (path / "deviceinfo_schema.toml").is_file())


def _workdir_candidates(root, device, home, lister=None) -> list:
    """Directories that could be this device's working repo.

    PURE given `lister`, for the same reason `_autoselect_tree` is: this is a
    guess offered to a human, and a guess that can be wrong in a test is
    cheaper than one that is wrong on a first run.

    Name-based, and deliberately so. "Looks like a kernel checkout" would match
    pmaports, porthole itself and every unrelated clone in ~/src; the folder
    people actually make is named after the phone.
    """
    if lister is None:
        def lister(base):
            try:
                return sorted(p for p in base.iterdir() if p.is_dir())
            except OSError:
                return []

    root, home = pathlib.Path(root), pathlib.Path(home)
    wanted = {device, _short_name(device)}
    # The parent of the porthole checkout first: porthole, pmaports and the
    # device repo side by side is the layout every doc in this tree assumes,
    # and the one the reference host uses.
    bases, seen = [], set()
    for base in [root.parent] + [home / r if r else home for r in WORKDIR_ROOTS]:
        if base not in seen:
            seen.add(base)
            bases.append(base)

    out = []
    for base in bases:
        for path in lister(base):
            if path.name not in wanted or path in out:
                continue
            # Never offer the toolkit's own checkout or a pmaports clone as
            # the device repo. Writing either would make `porthole docs new`
            # put this device's notes into a shared tree.
            if path == root or _is_pmaports(path):
                continue
            out.append(path)
    return out


def _choose_workdir(ctx, args, prompt, cfg, device, home=None) -> tuple[str, str]:
    """The device working repo. Returns (config_key, value), "" for nothing.

    THE GAP THIS FILLS. `init` set an identity, a tier and pmaports, and then
    every build verb said "PORTHOLE_WORKDIR is not set in the profile" -- which
    is true, and misleading twice over: it is not a profile key, and `init`,
    the command whose whole job is to set this host up, never asked. A host
    that had just been "set up in one command" could not run `porthole build`,
    `porthole verify`, `porthole dts` or six of the milestones `porthole next`
    reports, and nothing on the screen connected the two facts.

    Written as PORTHOLE_WORKDIR_<CODENAME>, never the bare key. A working repo
    belongs to ONE device -- porthole.load_config says so at length -- and the
    bare key is ignored outright the moment a second device declares its own.
    `--workdir` wrote the bare key, so on any two-device host the value it
    wrote was silently unused, which is the worst of the three possible
    outcomes.
    """
    key = f"PORTHOLE_WORKDIR_{device.upper().replace('-', '_')}"
    if args.workdir:
        path = pathlib.Path(args.workdir).expanduser()
        if not path.is_dir():
            raise Bail(f"{path} does not exist", EX_FAIL,
                       f"mkdir -p {path}   then re-run, or pick another path")
        return key, str(path.resolve())

    current = (cfg.get(key) or "").strip()
    if current and pathlib.Path(current).is_dir():
        return "", ""                                   # already right: adopt

    root = pathlib.Path(ctx.root)
    # `home` is a seam, not a setting: the "two candidates, so ask" rule is
    # the one that must not be wrong, and it cannot be reached in a test
    # without deciding where the search looks.
    found = _workdir_candidates(root, device, home or pathlib.Path.home())
    default = root.parent / _short_name(device)

    if not prompt.interactive:
        # Silent, because stdout may be `--json` and every step here runs
        # before `ctx.emit`. Adopt a single unambiguous candidate; never
        # create a directory with nobody watching, and never choose between
        # two -- a question with no one to answer it is left unanswered, and
        # the payload's `workdir` field reports which happened.
        return (key, str(found[0].resolve())) if len(found) == 1 else ("", "")

    ctx.out.blank()
    ctx.out.heading("the working repo for this device")
    prompt.why(
        "Your notes, your logs, and the kernel tree if you build one. It is "
        "yours and",
        "it is per-device: porthole writes nothing into it, and `porthole "
        "build`, `verify`,",
        "`dts` and half of `porthole next` cannot answer anything without it.")
    if current:
        ctx.out.kv("configured", ctx.out.paint(
            f"{current} -- which does not exist", "yellow"), 11)

    options = [(str(p), p.name, str(p)) for p in found]
    options.append(("__type__", "somewhere else", "you give it a path"))
    # "create one" pointing at a directory already offered above it is the
    # same answer twice wearing two numbers.
    if default not in found:
        options.append(("__make__", "create one", str(default)))
    choice = prompt.choose(options, default="1" if found else str(len(options)))

    if choice == "__type__":
        typed = prompt.ask("path to the working repo", "")
        if not typed:
            return "", ""
        path = pathlib.Path(typed).expanduser()
        if not path.is_dir():
            if not prompt.yes(f"{path} does not exist -- create it?"):
                return "", ""
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                ctx.out.warn(f"could not create {path}: {exc}")
                return "", ""
        return key, str(path.resolve())

    if choice == "__make__":
        try:
            default.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ctx.out.warn(f"could not create {default}: {exc}")
            return "", ""
        ctx.out(ctx.out.paint(f"  created {default} -- empty, and yours. "
                              f"`git init` it when you have something to keep.",
                              "grey"))
        return key, str(default.resolve())

    path = pathlib.Path(choice).expanduser()
    return (key, str(path.resolve())) if path.is_dir() else ("", "")


# ---------------------------------------------------------------- address --

# The pmOS USB gadget. Not a guess and not this port's: postmarketOS's
# initramfs brings the gadget up on this address on every device it supports,
# which is what makes it the right default and the right FIRST answer.
GADGET_IP = "172.16.42.1"
GADGET_NET = "172.16.42.0/24"


def _gadget_link() -> str:
    """The host interface holding an address on the gadget subnet, or "".

    The same question `tools/boot-probe.sh` asks to decide the gadget is up.
    An answer here means the cable is in and the device has booted far enough
    to enumerate -- so GADGET_IP is not merely the documented default, it is
    reachable right now.
    """
    try:
        proc = subprocess.run(["ip", "-br", "addr", "show", "to", GADGET_NET],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in proc.stdout.splitlines():
        parts = line.split()
        if parts:
            return parts[0]
    return ""


def _reachable(addr: str, timeout: float = 1.0) -> bool:
    """One ping. False means "did not answer", never "is broken"."""
    if not addr:
        return False
    try:
        return subprocess.run(
            ["ping", "-c", "1", "-W", str(int(max(1, timeout))), addr],
            capture_output=True, timeout=timeout + 4).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _choose_host(ctx, args, prompt, before) -> str:
    """The device's address, with the two ways of getting one spelled out.

    `init` printed `device IP [172.16.42.1]` and nothing else. The number is
    correct -- it is the pmOS USB gadget, the same on every device -- and it is
    unrecognisable as anything but a hardcoded guess to somebody who has not
    read the postmarketOS wiki. So the one question a new developer could not
    answer from the screen was the one the screen asked most confidently.

    Say what the two routes are, then PROBE: the gadget subnet on this host is
    a fact, cheap to read, and it answers "is that number right for me today".
    """
    default = args.host or before.get("PORTHOLE_HOST", GADGET_IP)
    if args.host or not prompt.interactive:
        return default

    ctx.out.blank()
    ctx.out.heading("how to reach the device")
    prompt.why(
        "Two routes, and porthole talks over ssh on both -- never adb.",
        "",
        f"  over USB    {GADGET_IP}, always. postmarketOS brings a USB network",
        "              gadget up in its initramfs, so this address is the same on",
        "              every device and works before wifi is configured. Start here.",
        "  over wifi    the address your router gave the phone. Faster, survives",
        "              unplugging the cable, and it CHANGES -- re-run `porthole init`",
        "              or set PORTHOLE_HOST when it does. Read it off the device",
        "              with `ip -4 -br addr` once it is booted.")
    ctx.out.blank()

    link = _gadget_link()
    if link:
        ctx.out.kv("usb link", f"{link} carries {GADGET_NET}", 11,
                   note="the gadget is up right now")
        default = args.host or GADGET_IP
    else:
        ctx.out.kv("usb link", ctx.out.paint("no interface on " + GADGET_NET,
                                             "yellow"), 11,
                   note="not plugged in, or not booted that far")

    host = prompt.ask("device address", default)
    # Reported, never enforced. A device that is off is the normal state
    # during setup, and refusing an address because it did not answer would
    # make this command unrunnable in exactly the case it exists for.
    if _reachable(host):
        ctx.out("  {}  {} answers".format(ctx.out.status("ok", "up", 4), host))
    else:
        ctx.out("  {}  {} did not answer -- fine if the device is off; "
                "`porthole doctor` checks it later".format(
                    ctx.out.status("skip", "quiet", 5), host))
    return host


# --------------------------------------------------------------- scaffold --

def _scaffold(ctx, prompt) -> str:
    """Create a profile from inside init, so a first run can finish.

    Delegates to new-device rather than reimplementing it: the seeding rules,
    the SoC reporting and the checklist generation all live there, and a second
    copy of them here would be a second copy to keep true.
    """
    codename = prompt.ask("codename (<vendor>-<device>, e.g. google-cheetah)", "")
    if not codename:
        raise Bail("a codename is needed to create a profile", EX_FAIL,
                   "porthole new-device <codename>")
    soc = prompt.ask("SoC, if you know it (vendor-prefixed, e.g. qcom-sdm845)", "")
    argv = [sys.executable, str(pathlib.Path(ctx.root) / "bin" / "porthole"),
            "new-device", codename] + (["--soc", soc] if soc else [])
    ctx.out.blank()
    if subprocess.run(argv).returncode != 0:
        raise Bail(f"could not scaffold {codename}", EX_FAIL)
    ctx.out.blank()
    return codename


def _choose_device(ctx, prompt, devices) -> str:
    """Which phone. A newcomer with no profiles must not be told to go and read
    about a different verb -- offering to create one here is the difference
    between a first run that finishes and a first run that becomes a
    documentation search."""
    if not devices:
        ctx.out("  no device profiles yet -- let us make one.")
        ctx.out.blank()
        return _scaffold(ctx, prompt)
    ctx.out.blank()
    ctx.out.heading("which device?")
    choice = prompt.choose(
        [(name, name, "") for name in devices]
        + [("__new__", "something not ported yet", "scaffolds a profile")],
        default="1")
    if choice == "__new__":
        return _scaffold(ctx, prompt)
    return choice


# -------------------------------------------------------------------- run --

def cmd_init(args, ctx) -> int:
    root = ctx.root
    devices = porthole.list_profiles(root)
    interactive = sys.stdin.isatty() and not args.non_interactive
    prompt = Prompt(ctx.out, interactive)

    if interactive:
        ctx.out.heading("porthole setup")
        ctx.out(ctx.out.paint(
            "  Six questions, all of them re-askable: this command reads what "
            "is already", "grey"))
        ctx.out(ctx.out.paint(
            "  here and offers it back, so running it again changes nothing "
            "you keep.", "grey"))

    device = args.codename
    if not device and interactive:
        device = _choose_device(ctx, prompt, devices)
        devices = porthole.list_profiles(root)
    elif not device:
        device = prompt.ask("device codename", devices[0] if devices else "")

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

    if interactive:
        ctx.out.blank()
        ctx.out.heading("who you are on the device")
    user = args.user or prompt.ask("ssh username on the device",
                                   before.get("PORTHOLE_USER", "user"))
    host = _choose_host(ctx, args, prompt, before)
    port = args.port or prompt.ask("ssh port",
                                   before.get("PORTHOLE_SSH_PORT", "22"))
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

    # The config the later steps reason about is what this run is about to
    # write, not what the process was started with -- otherwise a first run
    # answers every question against an empty config.
    cfg_now = dict(ctx.cfg)
    cfg_now.update(before)
    cfg_now.update(resolved)

    tier = _choose_tier(ctx, args, prompt, cfg_now)
    if tier == "host":
        src = _ensure_pmbootstrap_src(ctx, cfg_now, prompt)
        if src:
            resolved["PORTHOLE_PMBOOTSTRAP_SRC"] = src
            cfg_now["PORTHOLE_PMBOOTSTRAP_SRC"] = src
        pmb = pathlib.Path(before.get("PORTHOLE_PMB_DIR")
                           or pathlib.Path.home() / ".local/var/pmbootstrap")
        resolved["PORTHOLE_PMB_DIR"] = str(pmb)
        cfg_now["PORTHOLE_PMB_DIR"] = str(pmb)

    pm_key, pm_val = _choose_pmaports(ctx, args, prompt, cfg_now, device)
    if pm_key:
        resolved[pm_key] = pm_val
        cfg_now[pm_key] = pm_val

    wd_key, wd_val = _choose_workdir(ctx, args, prompt, cfg_now, device)
    if wd_key:
        resolved[wd_key] = wd_val
        cfg_now["PORTHOLE_WORKDIR"] = wd_val

    workdir = (cfg_now.get(f"PORTHOLE_WORKDIR_{device.upper().replace('-', '_')}")
               or cfg_now.get("PORTHOLE_WORKDIR") or "")

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

    # Everything below reads the host as it NOW is, not as it was when this
    # process started -- see Ctx.reload.
    ctx.reload()

    payload = {
        "config": str(target), "device": device, "user": user,
        "host": host, "port": port, "agent": agent, "tier": tier,
        "workdir": workdir,
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
        else:
            o.kv("where", "this host", 9)
            o.kv("", o.paint("needs pmbootstrap, its chroots and its "
                             "dependencies", "grey"), 9)
        o.kv("repo", workdir or o.paint("not set -- `porthole build` and "
                                        "`porthole verify` need it", "yellow"), 9)
        # The rung a host that has just been set up can actually run, said
        # HERE. `porthole build` on a fresh host offers six rungs and every one
        # of them compiles a kernel tree a fresh host does not have -- so the
        # first build anybody could run was the one nothing named.
        tree = pathlib.Path(workdir) / "linux" if workdir else None
        has_tree = bool(tree and (tree / "Makefile").is_file())
        if not has_tree:
            o.kv("tree", o.paint("none yet, which is fine -- `image` builds "
                                 "the whole system from", "grey"), 9)
            o.kv("", o.paint("pmaports and compiles no kernel tree", "grey"), 9)
        else:
            o.kv("tree", str(tree), 9)
        if tier == "workspace":
            o.hint("porthole sandbox up      build the image and start it")
        if has_tree:
            o.hint("porthole build           the rung ladder, cheapest first")
        else:
            o.hint("porthole build image     the whole system image, from "
                   "pmaports")

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
        except Exception as exc:  # noqa: BLE001 -- init must finish regardless
            # Reported on stderr rather than swallowed. init PROMISES one next
            # step; a silent `except` turned "next could not run here" into a
            # heading with nothing under it, which reads as "there is nothing
            # to do" -- the opposite of true on a host that was just set up.
            o.warn(f"could not work out the next step: {exc}")
        o.hint("porthole doctor          check host and device")

    return ctx.emit(payload, render)


SPEC = {
    "verb": "init",
    "order": 10,
    "help": "set this host up: identity, address, build tier, pmaports, repo",
    "description": (
        "Sets this host up, and is safe to re-run on a half-configured one:\n"
        "it reads what is already there, offers it back as the default for\n"
        "every question, and rewrites only the lines you change.\n\n"
        "Writes ~/.config/porthole/config.env -- your username, your device's\n"
        "address, your working repo, your tool paths. Never committed.\n\n"
        "Interactive at a terminal, flag-driven otherwise, so an agent can\n"
        "bootstrap headless."),
    # It calls input() in a loop and would otherwise appear to hang in a
    # drawer with no visible prompt. The console hands the real tty over.
    "interactive": True,
    # The positional IS the device. `device_pos` used to exist only because the
    # injected --device selector collided with it; both go away together.
    "device_flag": False,
    "args": [
        (["codename"], {"nargs": "?", "metavar": "CODENAME",
                        "help": "device profile to use"}),
        (["--user"], {"help": "ssh username on the device"}),
        (["--host"], {"help": "device address (default: the USB gadget, "
                              + GADGET_IP + ")"}),
        (["--port"], {"help": "ssh port (default 22)"}),
        (["--agent"], {"help": "default TK_AGENT for the device mutex"}),
        (["--workdir"], {"metavar": "PATH",
                         "help": "the device working repo (notes, logs, the "
                                 "kernel tree if you build one)"}),
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
        "porthole init google-taimen --workdir ~/ws/pmos/taimen",
        "porthole init google-taimen --tier host --pmaports ~/src/pmaports",
    ],
}
