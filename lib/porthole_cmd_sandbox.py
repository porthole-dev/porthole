# SPDX-License-Identifier: MIT
"""`porthole sandbox` -- run pmbootstrap without handing the host to an agent.

Two tiers, because neither is sufficient alone:

  broker    ph-sudo validates every root request pmbootstrap makes, confining
            paths to declared roots. Always on, cheap, audited. Stops accidents
            and casual misuse; does not contain a determined chroot payload.

  container rootless podman where container-root maps to YOUR uid. Nothing in
            it can exceed your own privileges, so even a full escape reaches
            only the directories you mounted. This is the real boundary.

The threat model, and what each tier does and does not buy, is in
docs/SANDBOX.md. Read it before trusting either.
"""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import shutil
import subprocess
import sys

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE

BROKER_SRC = "sandbox/ph-sudo"
CLIENT_SRC = "sandbox/ph-sudo-client"
CLIENT_DST = "/usr/local/bin/ph-sudo-client"
BROKER_DST = "/usr/local/libexec/porthole/ph-sudo"
POLICY_DST = "/etc/porthole/sandbox.conf"
AUDIT = "/var/log/porthole-sandbox.log"
SUDOERS_DST = "/etc/sudoers.d/60-porthole-sandbox"

BASE_IMAGE = "docker.io/library/alpine:3.24"
CONTAINER = "porthole-sandbox"

# Where the dedicated device ssh key lives on the host, and where it is mounted
# inside the container. Deliberately NOT under the /porthole repo mount: a key
# shadowing a path in the user's checkout is a confusing surprise.
DEVICE_KEY = "~/.porthole/device_key"
DEVICE_KEY_IN = "/run/porthole/device_key"


def _image_tag(root: pathlib.Path) -> str:
    """The image is tagged with the toolbox VERSION, so an image is always
    traceable to the revision that built it."""
    version = (root / "VERSION").read_text().strip()
    return f"localhost/{CONTAINER}:{version}"


def _ensure_device_key(home: pathlib.Path) -> pathlib.Path:
    """A dedicated ssh key for the device, so the workspace never needs ~/.ssh.

    docs/SANDBOX.md promises that your ssh keys are not present in the
    container. `PORTHOLE_SSH_KEY` is unset by default, so the device tooling
    falls back to ~/.ssh/id_ed25519 -- a personal key. Generating one key that
    only ever reaches the phone is what lets both statements be true.
    """
    key = home / ".porthole" / "device_key"
    if key.exists():
        return key
    if not shutil.which("ssh-keygen"):
        raise Bail("ssh-keygen is not installed", EX_FAIL,
                   "the workspace needs a dedicated device key; install "
                   "openssh-client")
    key.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-q",
         "-C", "porthole-sandbox", "-f", str(key)],
        check=True)
    os.chmod(key, 0o600)
    return key


def _lock_path(device: str) -> str:
    """Must match tools/tk-device.sh:32 exactly, or the mutex is not shared.

    TK_DEVICE_LOCK overrides the default there, same as here -- an operator
    who sets it on the host and not for the container would otherwise get two
    different locks guarding the one physical phone.
    """
    return os.environ.get("TK_DEVICE_LOCK") or f"/tmp/porthole-{device or 'device'}.lock"


def _mounts(root, pmb_dir, workdir, key, device, extra):
    """(host_src, container_dst, options) for everything the workspace sees.

    This list IS the isolation boundary. Nothing reaches the container that is
    not named here, so adding to it is a security decision.
    """
    mounts = [
        (str(pathlib.Path(pmb_dir).expanduser()), "/pmb", "rw"),
        (str(root), "/porthole", "rw"),
        ("/dev/bus/usb", "/dev/bus/usb", "rw"),
        (_lock_path(device), _lock_path(device), "rw"),
        (str(key), DEVICE_KEY_IN, "ro"),
    ]
    if workdir:
        mounts.append((str(pathlib.Path(workdir).expanduser()), "/work", "rw"))
    # porthole's own user config layer (lib/porthole.py load_config), where
    # `porthole use` writes the active device. Without this the container
    # resolves a DIFFERENT PORTHOLE_DEVICE than the host, tk-device.sh
    # computes a different lock path from that, and the device-mutex mount
    # above ends up guarding nothing while looking correct.
    xdg_config = pathlib.Path(
        os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
    porthole_config = xdg_config / "porthole"
    if porthole_config.is_dir():
        mounts.append((str(porthole_config), "/run/porthole/config/porthole", "rw"))
    for path in extra or []:
        src = str(pathlib.Path(path).expanduser())
        mounts.append((src, "/mnt/" + pathlib.Path(src).name, "rw"))
    return mounts


def _build_argv(root: pathlib.Path, force: bool) -> list[str]:
    argv = ["podman", "build", "-t", _image_tag(root),
            "-f", str(root / "sandbox" / "Containerfile")]
    if force:
        argv.append("--no-cache")
    argv.append(str(root / "sandbox"))
    return argv


def _up_argv(root, image, mounts, device) -> list[str]:
    """The persistent workspace.

    Not `--rm`: all state lives in the mounts above, so the container itself is
    disposable and keeping it costs nothing -- while recreating it per command
    would cost a second every time and lose the running adb server.

    Deliberately NOT here: `--network=host` (pasta already reaches the device
    on the default netns) and `--privileged` (the entire point).
    """
    argv = ["podman", "run", "-d", "--name", CONTAINER,
            "--userns=keep-id:uid=0,gid=0",
            # SYS_ADMIN for bind mounts, SYS_CHROOT for chroots, MKNOD for the
            # chroot's device nodes. Inside a rootless userns none of these
            # confer anything beyond that namespace.
            "--cap-add", "SYS_ADMIN,SYS_CHROOT,MKNOD",
            # fuse2fs mounts ext4 from a plain file, which is how the rootfs
            # image is built with no loop device and no privilege.
            "--device", "/dev/fuse",
            "--security-opt", "label=disable",
            "--hostname", CONTAINER,
            # So the in-container load_config finds the config mounted at
            # /run/porthole/config/porthole above, and tk-device.sh computes
            # the SAME lock path the host mounted rather than a different one
            # for the same device -- the whole point of that mount.
            "-e", "XDG_CONFIG_HOME=/run/porthole/config",
            "-e", f"TK_DEVICE_LOCK={_lock_path(device)}"]
    for src, dst, opts in mounts:
        argv += ["-v", f"{src}:{dst}:{opts}"]
    argv += [image, "sleep", "infinity"]
    return argv


def _container_running() -> bool:
    out = subprocess.run(
        ["podman", "ps", "-q", "-f", f"name=^{CONTAINER}$"],
        capture_output=True, text=True).stdout
    return bool(out.strip())


def _up(ctx, args) -> int:
    if not shutil.which("podman"):
        raise Bail("podman is not installed", EX_FAIL,
                   "the workspace needs it; see `porthole doctor`")
    if _container_running():
        ctx.out(f"  {CONTAINER} is already up")
        return EX_OK

    tag = _image_tag(ctx.root)
    if subprocess.run(["podman", "image", "exists", tag]).returncode != 0:
        rc = _build(ctx, args)
        if rc != EX_OK:
            return rc

    # A stopped container of the same name blocks `run --name`. Removing it is
    # safe precisely because no state lives in it.
    subprocess.run(["podman", "rm", "-f", CONTAINER],
                   capture_output=True)

    key = _ensure_device_key(pathlib.Path.home())
    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    lock = _lock_path(device)
    pathlib.Path(lock).touch(exist_ok=True)

    pmb = ctx.cfg.get("PORTHOLE_PMB_DIR") or str(
        pathlib.Path.home() / ".local/var/pmbootstrap")
    pathlib.Path(pmb).expanduser().mkdir(parents=True, exist_ok=True)

    mounts = _mounts(ctx.root, pmb, ctx.cfg.get("PORTHOLE_WORKDIR"), key,
                     device, args.mount)
    rc = subprocess.run(_up_argv(ctx.root, tag, mounts, device)).returncode
    if rc == 0:
        ctx.out(ctx.out.paint(
            f"  {CONTAINER} up. root inside maps to uid {os.getuid()} outside.\n"
            f"  mounted: {', '.join(d for _s, d, _o in mounts)}\n"
            f"  nothing else on this host is reachable from in here.\n"
            f"  device key: {key} -- add its .pub to the phone, then set\n"
            f"    PORTHOLE_SSH_KEY={key}", "grey"))
    return rc


# ------------------------------------------------------------------ status --

def _broker_state(root: pathlib.Path) -> dict:
    out = {"installed": False, "path": BROKER_DST, "issues": []}
    try:
        st = os.stat(BROKER_DST)
    except OSError:
        out["issues"].append("not installed")
        return out
    out["installed"] = True
    out["mode"] = f"{st.st_mode & 0o777:o}"
    out["owner_uid"] = st.st_uid
    if st.st_uid != 0:
        out["issues"].append(f"not owned by root (uid {st.st_uid}) -- an agent "
                             f"could edit the broker itself")
    if st.st_mode & 0o022:
        out["issues"].append(f"writable by non-root (mode {st.st_mode & 0o777:o})")
    # Drift: an installed broker older than the source is a broker missing
    # whatever the source learned since.
    src = root / BROKER_SRC
    if src.is_file() and src.read_bytes() != pathlib.Path(BROKER_DST).read_bytes():
        out["issues"].append("differs from sandbox/ph-sudo in this checkout -- "
                             "re-run `porthole sandbox install`")
    return out


def _policy_state() -> dict:
    out = {"installed": False, "path": POLICY_DST, "roots": [], "issues": []}
    try:
        st = os.stat(POLICY_DST)
    except OSError:
        out["issues"].append("not installed")
        return out
    out["installed"] = True
    out["mode"] = f"{st.st_mode & 0o777:o}"
    if st.st_uid != 0:
        out["issues"].append("not owned by root -- a policy the user can edit "
                             "is not a policy")
    if st.st_mode & 0o022:
        out["issues"].append("writable by non-root")
    try:
        for raw in pathlib.Path(POLICY_DST).read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.startswith("root"):
                out["roots"].append(line.partition("=")[2].strip())
            elif line.startswith("allow_chroot"):
                out["allow_chroot"] = line.partition("=")[2].strip()
    except OSError as exc:
        out["issues"].append(str(exc))
    return out


def _sudo_state() -> dict:
    """The thing this project exists to replace: a long root credential cache."""
    out = {"timestamp_timeout": None, "nopasswd_all": False, "issues": []}
    try:
        proc = subprocess.run(["sudo", "-n", "grep", "-rhs",
                               "timestamp_timeout\\|NOPASSWD",
                               "/etc/sudoers", "/etc/sudoers.d/"],
                              capture_output=True, text=True, timeout=10)
        text = proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        if "timestamp_timeout" in line:
            value = line.partition("timestamp_timeout")[2].lstrip("= ").split()[0]
            out["timestamp_timeout"] = value
            try:
                if float(value) > 60:
                    out["issues"].append(
                        f"timestamp_timeout={value} minutes: any process running "
                        f"as you gets silent root for {float(value)/60:.0f} hours. "
                        f"This is the hole the broker replaces.")
            except ValueError:
                pass
        if "NOPASSWD: ALL" in line and not line.startswith("#"):
            out["nopasswd_all"] = True
            out["issues"].append("a NOPASSWD: ALL rule grants unrestricted "
                                 "passwordless root")
    return out


def _container_state() -> dict:
    out = {"podman": shutil.which("podman"), "issues": []}
    if not out["podman"]:
        out["issues"].append("podman not installed -- the container tier is "
                             "unavailable; the broker still works")
        return out
    user = getpass.getuser()
    for path in ("/etc/subuid", "/etc/subgid"):
        try:
            if not any(l.startswith(user + ":")
                       for l in pathlib.Path(path).read_text().splitlines()):
                out["issues"].append(f"no range for {user} in {path} -- rootless "
                                     f"containers cannot map uids")
        except OSError:
            out["issues"].append(f"cannot read {path}")
    return out


def cmd_sandbox(args, ctx) -> int:
    action = args.action or "status"
    if action == "status":
        return _status(ctx)
    if action == "install":
        return _install(ctx, args)
    if action == "audit":
        return _audit(ctx, args)
    if action == "shell":
        return _shell(ctx, args)
    if action == "uninstall":
        return _uninstall(ctx)
    if action == "build":
        return _build(ctx, args)
    if action == "up":
        return _up(ctx, args)
    raise Bail(f"unknown action {action!r}", EX_USAGE,
               "actions: status, install, shell, audit, uninstall, build, up")


def _status(ctx) -> int:
    state = {
        "broker": _broker_state(ctx.root),
        "policy": _policy_state(),
        "sudo": _sudo_state(),
        "container": _container_state(),
        "env": {"PMB_SUDO": os.environ.get("PMB_SUDO", "")},
    }
    issues = sum((v.get("issues", []) for v in state.values()
                  if isinstance(v, dict)), [])
    state["ok"] = not issues

    def render():
        o = ctx.out
        tick, cross = o.sym("✓", "ok"), o.sym("✗", "XX")

        def line(label, good, detail):
            mark = o.paint(tick, "green") if good else o.paint(cross, "red")
            o(f"  {mark} {label:<22} {detail}")

        o.heading("privilege broker")
        b = state["broker"]
        line("ph-sudo", b["installed"] and not b["issues"],
             BROKER_DST if b["installed"] else o.paint("not installed", "grey"))
        p = state["policy"]
        line("policy", p["installed"] and not p["issues"],
             ", ".join(p["roots"]) if p["roots"]
             else o.paint("not installed", "grey"))
        line("PMB_SUDO", bool(state["env"]["PMB_SUDO"]),
             state["env"]["PMB_SUDO"] or o.paint("unset -- pmbootstrap will "
                                                 "use plain sudo", "yellow"))
        o.blank()

        o.heading("host sudo")
        s = state["sudo"]
        line("credential cache", not s["issues"],
             f"timestamp_timeout={s['timestamp_timeout']}"
             if s["timestamp_timeout"] else "default")
        o.blank()

        o.heading("container tier")
        c = state["container"]
        line("podman", bool(c["podman"]) and not c["issues"],
             c["podman"] or o.paint("not installed", "grey"))
        o.blank()

        if issues:
            o.heading("issues")
            for issue in issues:
                o(f"  {o.paint(o.sym('•', '-'), 'yellow')} {issue}")
            o.blank()
            o.hint("porthole sandbox install    set up the broker")
            o.hint("docs/SANDBOX.md             the threat model")
        else:
            o(o.paint("sandbox is configured", "green"))

    return ctx.emit(state, render)


# ----------------------------------------------------------------- install --

def _install(ctx, args) -> int:
    """Emit the install script. Deliberately does not run privileged steps.

    Installing a security boundary is a decision, not a side effect. And an
    agent cannot type a sudo password anyway -- so it prints exactly what will
    happen and lets a human run it, which is also how the human learns what
    they just trusted.
    """
    src = ctx.root / BROKER_SRC
    client_src = ctx.root / CLIENT_SRC
    if not src.is_file():
        raise Bail(f"{src} is missing from this checkout", EX_FAIL)

    roots = args.root or []
    if not roots:
        pmb = ctx.cfg.get("PORTHOLE_PMB_DIR") or str(
            pathlib.Path.home() / ".local/var/pmbootstrap")
        roots = [pmb]
        workdir = ctx.cfg.get("PORTHOLE_WORKDIR")
        if workdir:
            roots.append(workdir)

    # pmbootstrap copies its bundled apk signing keys out of its own install
    # directory into the workdir, which is outside the roots. Only bites on a
    # fresh workdir, and then it stops the first chroot -- so find it now.
    # pmbootstrap is often run from a checkout rather than installed as a
    # library, so ask its own entry point where it lives rather than trying to
    # import it from here.
    readable_extra = []
    entry = shutil.which("pmbootstrap")
    if entry:
        pkg = pathlib.Path(entry).resolve().parent / "pmb"
        keys = pkg / "data" / "keys"
        if keys.is_dir():
            readable_extra = [
                "",
                "# pmbootstrap copies its bundled apk signing keys out of its",
                "# own install directory into the workdir. Read-only, and it",
                "# only bites on a fresh workdir -- where it stops the first",
                "# chroot.",
                f"readable = {keys}",
            ]

    policy = ["# porthole sandbox policy. Root-owned; the broker refuses to run",
              "# if this file is writable by anyone else.",
              "#",
              "# Every path argument in a brokered root request must resolve",
              "# inside one of these roots, symlinks followed.",
              ""]
    for r in roots:
        policy.append(f"root = {pathlib.Path(r).expanduser()}")
    policy += [
        "",
        "# Host files a request may READ but never write. pmbootstrap copies",
        "# the host resolv.conf into the chroot so the chroot has DNS, and",
        "# that source is outside the roots by design. Listing it grants",
        "# nothing: it is world-readable, so root reading it discloses nothing",
        "# you cannot already read. It can never be a copy DESTINATION, which",
        "# is what stops a listed file being overwritten as root.",
        "readable = /etc/resolv.conf",
        *readable_extra,
        "",
        "# chroot runs an arbitrary command as root, and root inside a chroot",
        "# can escape a chroot. Set to 0 to refuse it on the host entirely and",
        "# do chroot work only in `porthole sandbox shell`.",
        "allow_chroot = 1",
        "",
    ]
    policy_text = "\n".join(policy)

    user = getpass.getuser()
    script = f"""\
set -eu

# 1. the broker: root-owned, not writable by you. If you can edit it, it is
#    not a boundary -- an agent running as you would simply rewrite it.
sudo install -d -m 0755 /usr/local/libexec/porthole
sudo install -m 0755 -o root -g root {src} {BROKER_DST}

# 2. the client: what PMB_SUDO points at. pmbootstrap invokes it directly and
#    prefixes nothing, so something has to supply the sudo step; the broker
#    itself stays strictly root-only.
sudo install -m 0755 -o root -g root {client_src} {CLIENT_DST}

# 3. the policy: same reasoning.
sudo install -d -m 0755 /etc/porthole
sudo install -m 0644 -o root -g root /dev/stdin {POLICY_DST} <<'POLICY'
{policy_text}POLICY

# 4. the audit log, writable by you so the broker can append to it.
sudo install -m 0664 -o root -g {user} /dev/null {AUDIT} 2>/dev/null || \\
  sudo touch {AUDIT} && sudo chown root:{user} {AUDIT} && sudo chmod 0664 {AUDIT}

# 5. ONE sudoers entry, for the broker alone. visudo -c validates before
#    install, because a malformed sudoers file can lock you out of sudo.
printf '%s\\n' '{user} ALL=(root) NOPASSWD: {BROKER_DST}' \\
  | sudo tee {SUDOERS_DST} >/dev/null
sudo chmod 0440 {SUDOERS_DST}
sudo visudo -c -f {SUDOERS_DST}

echo
echo "Now REMOVE the blanket cache, which is the actual hole:"
echo "  sudo visudo    # delete any 'Defaults:{user} timestamp_timeout=<large>'"
echo
echo "Then add to your shell profile:"
echo "  export PMB_SUDO={CLIENT_DST}"
"""

    def render():
        o = ctx.out
        o.heading("porthole sandbox install")
        o.blank()
        o("This sets up a privilege broker so pmbootstrap works without giving")
        o("every process running as you unrestricted root on this host.")
        o.blank()
        o.heading("roots the broker will permit")
        for r in roots:
            o(f"  {pathlib.Path(r).expanduser()}")
        o.blank()
        o("Nothing outside those paths can be touched through the broker.")
        o.blank()
        o.heading("what it installs")
        o("  the broker    " + BROKER_DST + "  (root-owned, 0755)")
        o("  the client    " + CLIENT_DST + "  (what PMB_SUDO points at)")
        o("  the policy    " + POLICY_DST + "  (root-owned, 0644)")
        o("  one sudoers   " + SUDOERS_DST + "  (for the broker alone)")
        o("  an audit log  " + AUDIT)
        o.blank()
        o.heading("review, then run")
        o(o.paint("It needs sudo, and installing a security boundary should be a "
                  "decision you\nmake rather than something a tool does to you. "
                  "The script is written out\nrather than printed so you can "
                  "read it in an editor first -- and so a\ncopy-paste cannot "
                  "mangle the heredoc.", "grey"))
        o.blank()
        o(o.paint(f"  less {out_path}", "cyan"))
        o(o.paint(f"  bash {out_path}", "cyan"))
        o.blank()
        o.heading("then")
        o.hint("porthole sandbox status")
        o.hint(f"export PMB_SUDO={CLIENT_DST}   # add to your shell profile")
        o.hint("sudo visudo   # and delete the blanket timestamp_timeout")

    if args.json:
        return ctx.emit({"script": script, "roots": roots,
                         "policy": policy_text, "broker_src": str(src)})

    # Written, not printed: a 30-line script with a heredoc in it does not
    # survive copy-paste, and a security boundary deserves to be read in an
    # editor before it is trusted.
    out_dir = ctx.root / ".run"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "sandbox-install.sh"
    out_path.write_text("#!/bin/bash\n# Generated by `porthole sandbox install`. Review before running.\n\n" + script)
    out_path.chmod(0o755)

    render()
    return EX_OK


def _uninstall(ctx) -> int:
    user = getpass.getuser()
    ctx.out.heading("remove the sandbox")
    ctx.out.blank()
    for line in [f"sudo rm -f {SUDOERS_DST} {BROKER_DST} {CLIENT_DST} {POLICY_DST}",
                 f"# your PMB_SUDO export in ~/.bashrc or ~/.zshrc too"]:
        ctx.out(ctx.out.paint("  " + line, "cyan"))
    ctx.out.blank()
    ctx.out(f"The audit log at {AUDIT} is left in place deliberately.")
    return EX_OK


# ------------------------------------------------------------------- audit --

def _audit(ctx, args) -> int:
    path = pathlib.Path(os.environ.get("PH_SUDO_AUDIT", AUDIT))
    if not path.exists():
        raise Bail(f"no audit log at {path}", EX_FAIL,
                   "nothing has gone through the broker yet, or it is not "
                   "installed -- `porthole sandbox status`")
    entries = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if args.denied:
        entries = [e for e in entries if e.get("verdict", "").startswith("DENY")]
    entries = entries[-args.limit:]

    def render():
        if not entries:
            ctx.out("nothing recorded.")
            return
        for e in entries:
            verdict = e.get("verdict", "?")
            colour = {"ALLOW": "grey", "DENY": "red"}.get(
                verdict, "yellow" if verdict.startswith("ALLOW-") else "grey")
            argv = " ".join(e.get("argv", []))
            ctx.out(f"  {e.get('t', '')}  "
                    f"{ctx.out.paint(f'{verdict:<13}', colour)} {argv[:100]}")
            if e.get("reason") and verdict == "DENY":
                ctx.out(ctx.out.paint(f"      {e['reason'][:110]}", "grey"))
        ctx.out.blank()
        denies = sum(1 for e in entries if e.get("verdict") == "DENY")
        ctx.out(f"{len(entries)} shown, {denies} denied.")

    return ctx.emit(entries, render)


# ------------------------------------------------------------------- build --

def _build(ctx, args) -> int:
    if not shutil.which("podman"):
        raise Bail("podman is not installed", EX_FAIL,
                   "the workspace needs it; see `porthole doctor`")
    tag = _image_tag(ctx.root)
    force = getattr(args, "force", False)
    if not force:
        have = subprocess.run(["podman", "image", "exists", tag]).returncode == 0
        if have:
            ctx.out(f"  {tag} already built -- `--force` to rebuild")
            return EX_OK
    ctx.out(f"  building {tag}")
    return subprocess.run(_build_argv(ctx.root, force)).returncode


# ------------------------------------------------------------------- shell --

def _shell(ctx, args) -> int:
    """A rootless container where root maps to your uid.

    `--userns=keep-id:uid=0` is the whole trick: inside, you are root and
    pmbootstrap therefore uses no sudo at all (it checks `os.getuid() == 0`);
    outside, every file it creates is owned by you and it holds none of your
    privileges. An escape gets your uid, not the machine.
    """
    if not shutil.which("podman"):
        raise Bail("podman is not installed", EX_FAIL,
                   "the container tier needs it; the broker tier does not")

    pmb = ctx.cfg.get("PORTHOLE_PMB_DIR") or str(
        pathlib.Path.home() / ".local/var/pmbootstrap")
    mounts = [(pmb, "/pmb"), (str(ctx.root), "/porthole")]
    workdir = ctx.cfg.get("PORTHOLE_WORKDIR")
    if workdir:
        mounts.append((workdir, "/work"))
    for extra in args.mount or []:
        mounts.append((extra, "/mnt/" + pathlib.Path(extra).name))

    argv = ["podman", "run", "--rm", "-it",
            "--userns=keep-id:uid=0,gid=0",
            # SYS_ADMIN is needed for bind mounts; inside a rootless userns it
            # confers nothing outside the container's own mount namespace.
            "--cap-add", "SYS_ADMIN,SYS_CHROOT,MKNOD",
            "--security-opt", "label=disable",
            "--hostname", "porthole-sandbox"]
    for src, dst in mounts:
        src = str(pathlib.Path(src).expanduser())
        if not pathlib.Path(src).exists():
            ctx.out.warn(f"skipping {src}: does not exist")
            continue
        argv += ["-v", f"{src}:{dst}:rw"]
    argv += ["-w", "/work" if workdir else "/pmb", args.image or _image_tag(ctx.root)]
    argv += args.command or ["/bin/sh"]

    if args.dry_run:
        print(" ".join(argv))
        return EX_OK

    ctx.out(ctx.out.paint(
        f"  rootless container: root inside maps to uid {os.getuid()} outside.\n"
        f"  mounted: {', '.join(d for _, d in mounts)}\n"
        f"  nothing else on this host is reachable from in here.", "grey"))
    return subprocess.run(argv).returncode


SPEC = {
    "verb": "sandbox",
    "order": 22,
    "help": "run pmbootstrap without handing the host to an agent",
    "description": (
        "pmbootstrap needs root. The usual workaround -- a multi-day sudo\n"
        "credential cache -- gives every process running as you silent,\n"
        "unlimited root, which is not something to hand an agent.\n\n"
        "Two tiers: a validating broker that confines every root request\n"
        "pmbootstrap makes, and a rootless container where root maps to your\n"
        "own uid. See docs/SANDBOX.md for the threat model."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["status", "install", "shell", "audit",
                                  "uninstall", "build", "up"],
                      "help": "status | install | shell | audit | uninstall | build | up"}),
        (["--root"], {"action": "append", "metavar": "PATH",
                      "help": "install: a path the broker may touch (repeatable)"}),
        (["--mount"], {"action": "append", "metavar": "PATH",
                       "help": "shell: extra path to mount in"}),
        (["--image"], {"metavar": "REF", "help": "shell: container image"}),
        (["--command"], {"nargs": "...", "help": "shell: command instead of a shell"}),
        (["--dry-run"], {"action": "store_true",
                         "help": "shell: print the podman command and stop"}),
        (["--force"], {"action": "store_true",
                       "help": "build: rebuild even if the tag exists"}),
        (["--denied"], {"action": "store_true", "help": "audit: only denials"}),
        (["--limit"], {"type": int, "default": 40, "help": "audit: how many"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_sandbox,
    "examples": [
        "porthole sandbox status",
        "porthole sandbox install",
        "porthole sandbox shell            # rootless container",
        "porthole sandbox audit --denied   # what was refused",
    ],
}
