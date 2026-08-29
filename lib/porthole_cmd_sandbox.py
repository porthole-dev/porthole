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
import shlex
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

# The lock path baked into the container at `up` time, recorded on the
# container itself. TK_DEVICE_LOCK is set once, at creation, and cannot follow
# a later `porthole use <other-device>` -- so the container would go on locking
# the old device's path while the host locks the new one, and two agents would
# drive one phone with the mutex looking healthy. The label is what lets `up`
# and `shell` notice.
LOCK_LABEL = "io.porthole.device-lock"

# Where the dedicated device ssh key lives on the host, and where it is mounted
# inside the container. Deliberately NOT under the /porthole repo mount: a key
# shadowing a path in the user's checkout is a confusing surprise.
DEVICE_KEY = ".porthole/device_key"      # relative to $HOME
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
    key = home / DEVICE_KEY
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
    #
    # READ-ONLY, and it must stay that way. config.env sets FASTBOOT and ADB
    # (lib/porthole.py), and the HOST executes those values as commands --
    # tools/tk-flash-boot.sh, tools/ph-build.sh, lib/porthole.py's Device.
    # Writable, anything in the container could put `FASTBOOT=/tmp/evil.sh`
    # in that file and get arbitrary execution as you on the host's next
    # flash. The mount exists so both sides resolve the same device; that
    # needs reads only. Do not widen it.
    xdg_config = pathlib.Path(
        os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
    porthole_config = xdg_config / "porthole"
    if porthole_config.is_dir():
        mounts.append((str(porthole_config), "/run/porthole/config/porthole", "ro"))
    for path in extra or []:
        src = str(pathlib.Path(path).expanduser())
        mounts.append((src, "/mnt/" + pathlib.Path(src).name, "rw"))
    return mounts


SPEC_LABEL = "io.porthole.containerfile-sha"


def _containerfile_sha(root: pathlib.Path) -> str:
    """Hash of the Containerfile the image should have been built from.

    Recorded as a label so a CHANGED Containerfile does not silently never
    reach anyone: the tag is keyed on VERSION, so `build` would skip an
    existing tag forever and the workspace would quietly stay on the old
    recipe. A stale thing winning silently is the failure this repo keeps
    paying for.
    """
    import hashlib

    try:
        data = (root / "sandbox" / "Containerfile").read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]


def _image_sha(tag: str) -> str:
    """The Containerfile hash the existing image records, "" if it has none."""
    proc = subprocess.run(
        ["podman", "image", "inspect", "-f",
         "{{index .Config.Labels " + json.dumps(SPEC_LABEL) + "}}", tag],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return ""
    out = proc.stdout.strip()
    return "" if out in ("<no value>", "") else out


def _build_argv(root: pathlib.Path, force: bool) -> list[str]:
    argv = ["podman", "build", "-t", _image_tag(root),
            "--label", f"{SPEC_LABEL}={_containerfile_sha(root)}",
            "-f", str(root / "sandbox" / "Containerfile")]
    if force:
        argv.append("--no-cache")
    argv.append(str(root / "sandbox"))
    return argv


# The container's own PATH, plus the repo's bin. Set from out here rather than
# baked into the image so an image built before this fix still gets it: an
# agent whose `porthole` is not on PATH cannot use the workspace at all.
CONTAINER_PATH = ("/porthole/bin:/usr/local/sbin:/usr/local/bin:"
                  "/usr/sbin:/usr/bin:/sbin:/bin")


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
            "-e", f"TK_DEVICE_LOCK={_lock_path(device)}",
            # /porthole/bin, or `porthole` is not a command in here at all --
            # reported from a real session as the first thing that stopped an
            # agent using the workspace. Set from out here rather than baked
            # into the image, so an image built before this fix still gets it.
            "-e", "PATH=" + CONTAINER_PATH,
            # The dedicated device key, at its in-container path. Unset, the
            # device tooling falls back to ~/.ssh/id_ed25519 -- which the
            # workspace deliberately cannot see, so ssh would simply fail.
            "-e", f"PORTHOLE_SSH_KEY={DEVICE_KEY_IN}",
            # Same value, recorded where a later command can read it back and
            # notice that `porthole use` has moved on. See LOCK_LABEL.
            "--label", f"{LOCK_LABEL}={_lock_path(device)}"]
    # The config mount carries the user-config LAYER, but the host's active
    # device usually comes from the ENVIRONMENT -- a layer that stops at the
    # container boundary. Without this the container resolves a DIFFERENT
    # device than the host: reported from a real session as "the container
    # defaults to cheetah" while the host was on taimen.
    if device:
        argv += ["-e", f"PORTHOLE_DEVICE={device}"]
    # And the mount is /work, so the value must be /work. The profile carries a
    # host path, which does not exist in here -- which is why that session's
    # build refused with "this profile cannot build yet".
    if any(dst == "/work" for _s, dst, _o in mounts):
        argv += ["-e", "PORTHOLE_WORKDIR=/work"]
    for src, dst, opts in mounts:
        argv += ["-v", f"{src}:{dst}:{opts}"]
    argv += [image, "sleep", "infinity"]
    return argv




def _lock_drift(baked: str, want: str) -> str:
    """The refusal message, or "" when the two locks agree.

    Empty `baked` means a container from before the label existed: unknowable,
    so it is not treated as drift.
    """
    if not baked or baked == want:
        return ""
    return (f"{CONTAINER} locks {baked} but this device locks {want}")


def _lock_from_inspect(returncode: int, stdout: str, stderr: str) -> str:
    """Turn a `podman inspect` result into a label, or refuse.

    Pure, so the fail-closed decision is testable without podman -- which
    matters more here than anywhere else in this file, because this is the
    branch that decides whether the device-mutex guard speaks up at all.

    A FAILED inspect is not the same as an absent label. `podman inspect`
    exits 125 with empty stdout when the template names a field that no longer
    exists, so a future podman renaming one would otherwise disable drift
    detection for every container -- silently, which reads exactly like
    agreement.
    """
    if returncode != 0:
        detail = (stderr.strip().splitlines() or
                  ["podman inspect exited {}".format(returncode)])[-1]
        raise Bail("cannot read {}'s device-lock label".format(CONTAINER),
                   EX_FAIL,
                   detail + " -- refusing rather than assuming the locks agree")
    out = stdout.strip()
    return "" if out in ("<no value>", "") else out


def _container_lock() -> str:
    """The lock path recorded on the running container, "" if it carries none."""
    proc = subprocess.run(
        ["podman", "inspect", "-f",
         "{{index .Config.Labels " + json.dumps(LOCK_LABEL) + "}}", CONTAINER],
        capture_output=True, text=True)
    return _lock_from_inspect(proc.returncode, proc.stdout, proc.stderr)


def _assert_lock_matches(ctx) -> None:
    """Refuse to use a container that guards a different phone than we do."""
    baked = _container_lock()
    if not baked:
        # A container from before the label existed. Not drift, but not
        # protection either -- and an unprotected container that says nothing
        # is indistinguishable from a checked one. Say so.
        ctx.out.warn("{} carries no device-lock label, so drift cannot be "
                     "checked. `porthole sandbox down` then `up` to get the "
                     "guard.".format(CONTAINER))
        return
    drift = _lock_drift(baked, _lock_path(ctx.cfg.get("PORTHOLE_DEVICE", "")))
    if drift:
        raise Bail(drift, EX_FAIL,
                   "the device changed under the workspace; two agents would "
                   "drive one phone with the mutex looking healthy. Run "
                   "`porthole sandbox down` and `up` again")


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
        _assert_lock_matches(ctx)
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
            f"  device key: {key}\n"
            f"    put {key}.pub in the phone's authorized_keys -- one time.\n"
            f"    PORTHOLE_SSH_KEY is already wired to {DEVICE_KEY_IN} in\n"
            f"    here; set nothing yourself.", "grey"))
    return rc


def _down_argv() -> list[str]:
    """Remove the container, never the mounts.

    Every mount is a real directory of the user's -- the pmbootstrap workdir,
    the repo, the device lock. `podman rm -v` would be a data-loss bug, so the
    flag is absent and a test keeps it absent.
    """
    return ["podman", "rm", "-f", CONTAINER]


def _down_message(existed: bool) -> str:
    """Split out because this exact branch already reported a removal that
    never happened; a pure function is a branch a test can reach."""
    return (f"  {CONTAINER} removed (mounted directories untouched)"
            if existed else f"  {CONTAINER} was not running")


def _down(ctx) -> int:
    if not shutil.which("podman"):
        raise Bail("podman is not installed", EX_FAIL, "nothing to stop")
    # `podman rm -f` exits 0 whether or not the container existed, so the
    # message has to come from a check made BEFORE removing -- not the rc.
    existed = subprocess.run(
        ["podman", "container", "exists", CONTAINER]).returncode == 0
    subprocess.run(_down_argv(), capture_output=True)
    ctx.out(_down_message(existed))
    return EX_OK


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


def _container_state(root: pathlib.Path) -> dict:
    out = {"podman": shutil.which("podman"), "image": _image_tag(root),
           "image_built": False, "container_running": False,
           "device_key": "", "issues": []}
    key = pathlib.Path.home() / DEVICE_KEY
    out["device_key"] = str(key) if key.exists() else ""
    if not out["podman"]:
        out["issues"].append("podman not installed -- the workspace is "
                             "unavailable. `porthole doctor` has install hints")
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
    out["image_built"] = subprocess.run(
        ["podman", "image", "exists", out["image"]]).returncode == 0
    if not out["image_built"]:
        out["issues"].append(f"{out['image']} is not built -- "
                             f"run `porthole sandbox build`")
    out["container_running"] = _container_running()
    if out["image_built"] and not out["container_running"]:
        out["issues"].append(f"{CONTAINER} is not running -- "
                             f"run `porthole sandbox up`")
    if not out["device_key"]:
        out["issues"].append("no device key yet -- `porthole sandbox up` "
                             "creates one so the workspace never needs ~/.ssh")
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
    if action == "down":
        return _down(ctx)
    raise Bail(f"unknown action {action!r}", EX_USAGE,
               "actions: status, install, shell, audit, uninstall, build, up, down")


def _status(ctx) -> int:
    state = {
        "broker": _broker_state(ctx.root),
        "policy": _policy_state(),
        "sudo": _sudo_state(),
        "container": _container_state(ctx.root),
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

        o.heading("workspace")
        c = state["container"]
        line("podman", bool(c["podman"]), c["podman"] or o.paint("not installed", "grey"))
        line("image", c["image_built"],
             c["image"] if c["image_built"] else o.paint("not built", "grey"))
        line("container", c["container_running"],
             CONTAINER if c["container_running"] else o.paint("not running", "grey"))
        line("device key", bool(c["device_key"]),
             c["device_key"] or o.paint("not created", "grey"))
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
    """Emit the LEGACY broker install script. Requires --broker, deliberately.

    The workspace container replaced this. It needs no sudoers entry at all, so
    the default install now grants none: a boundary you do not need is one more
    thing that can be wrong. The broker remains for a host without podman, and
    for that host it is genuinely better than a blanket sudo cache -- but it is
    a fallback, and choosing a weaker boundary should be explicit.

    Still does not run the privileged steps itself. Installing a security
    boundary is a decision, not a side effect, and an agent cannot type a sudo
    password anyway -- so it prints exactly what will happen and lets a human
    run it, which is also how the human learns what they just trusted.
    """
    if not getattr(args, "broker", False):
        ctx.out.heading("the workspace is the supported path")
        ctx.out("  porthole sandbox up        build the image and start it")
        ctx.out("  porthole sandbox shell     a shell, or --command for one command")
        ctx.out.blank()
        ctx.out("  It needs no sudoers entry, so this command no longer writes one.")
        ctx.out("  Inside the container you are root and pmbootstrap uses no sudo.")
        ctx.out.blank()
        ctx.out(ctx.out.paint(
            "  Only if this host cannot run podman: `porthole sandbox install "
            "--broker`\n"
            "  installs the legacy privilege broker, which DOES grant a real "
            "sudoers entry\n"
            "  and cannot contain a determined chroot payload. See "
            "docs/SANDBOX.md.", "grey"))
        return EX_OK

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
            want = _containerfile_sha(ctx.root)
            got = _image_sha(tag)
            if want and got and want != got:
                ctx.out(ctx.out.paint(
                    "  the Containerfile changed since this image was built "
                    "-- rebuilding", "yellow"))
            elif want and not got:
                # Built before the label existed, so what recipe it came from
                # is unknowable. Say so rather than skipping silently -- a
                # rebuild is minutes, and a workspace quietly missing a change
                # is what sent a real session down three wrong paths.
                ctx.out(ctx.out.paint(
                    f"  {tag} predates the Containerfile hash label, so it may "
                    f"be stale.\n  `porthole sandbox build --force` to be sure.",
                    "yellow"))
                return EX_OK
            else:
                ctx.out(f"  {tag} already built -- `--force` to rebuild")
                return EX_OK
    ctx.out(f"  building {tag}")
    return subprocess.run(_build_argv(ctx.root, force)).returncode


# ------------------------------------------------------------------- shell --

# Whitespace or any shell metacharacter. A bare `ls` is a program; anything
# with a space, a pipe or a redirect is a line meant for a shell.
_SHELL_CHARS = frozenset(" \t|&;<>()$`\\\"'*?[]{}~#\n")


def _is_shell_line(text: str) -> bool:
    return any(ch in _SHELL_CHARS for ch in text)


def _exec_argv(command, tty: bool) -> list[str]:
    """`-it` ONLY for an interactive human.

    podman refuses `-t` when stdin is not a terminal, so an unconditional `-it`
    fails for every agent -- which is exactly what made the container tier
    unreachable from the thing it was built for.
    """
    argv = ["podman", "exec"]
    if tty:
        argv.append("-it")
    argv.append(CONTAINER)
    if not command:
        return argv + ["/bin/bash"]
    # `--command "pmbootstrap status"` arrives as ONE argv element, and podman
    # would try to exec a file with that literal name -- reported from a real
    # session as a crun "executable file not found" error, having done exactly
    # what the flag looked like it should do. A single element carrying shell
    # syntax is a shell line, so run it as one. Several elements are already
    # argv and are passed through untouched.
    if len(command) == 1 and _is_shell_line(command[0]):
        return argv + ["/bin/bash", "-lc", command[0]]
    return argv + list(command)


def _shell(ctx, args) -> int:
    """A shell (or one command) inside the persistent workspace."""
    if not shutil.which("podman"):
        raise Bail("podman is not installed", EX_FAIL,
                   "the workspace needs it; the broker tier does not")
    if not _container_running():
        raise Bail(f"{CONTAINER} is not running", EX_FAIL,
                   "run `porthole sandbox up` first")
    _assert_lock_matches(ctx)

    tty = sys.stdin.isatty() and not args.command
    argv = _exec_argv(args.command, tty)
    if args.dry_run:
        # shlex.quote: printed bare, a single-element `--command "a b"` looks
        # exactly like the two-argument form and hides the very bug this
        # wrapping exists to fix.
        print(" ".join(shlex.quote(a) for a in argv))
        return EX_OK
    return subprocess.run(argv).returncode


SPEC = {
    "verb": "sandbox",
    "order": 22,
    "help": "run pmbootstrap without handing the host to an agent",
    "description": (
        "pmbootstrap needs root. The usual workaround -- a multi-day sudo\n"
        "credential cache -- gives every process running as you silent,\n"
        "unlimited root, which is not something to hand an agent.\n\n"
        "So it runs in a persistent rootless container instead, where you are\n"
        "root inside and your own unprivileged uid outside. No sudoers entry,\n"
        "no standing privilege. `up` builds and starts it; `shell --command`\n"
        "works without a TTY, which is what makes it usable by an agent.\n\n"
        "`install --broker` is a legacy fallback for a host without podman.\n"
        "See docs/SANDBOX.md for the threat model."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["status", "install", "shell", "audit",
                                  "uninstall", "build", "up", "down"],
                      "help": "status | install | shell | audit | uninstall | build | up | down"}),
        (["--root"], {"action": "append", "metavar": "PATH",
                      "help": "install: a path the broker may touch (repeatable)"}),
        (["--mount"], {"action": "append", "metavar": "PATH",
                       "help": "up: extra path to mount into the workspace"}),
        (["--command"], {"nargs": "...", "help": "shell: command instead of a shell"}),
        (["--dry-run"], {"action": "store_true",
                         "help": "shell: print the podman command and stop"}),
        (["--force"], {"action": "store_true",
                       "help": "build: rebuild even if the tag exists"}),
        (["--broker"], {"action": "store_true",
                        "help": "install: the legacy sudoers broker, for a "
                                "host with no podman"}),
        (["--denied"], {"action": "store_true", "help": "audit: only denials"}),
        (["--limit"], {"type": int, "default": 40, "help": "audit: how many"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_sandbox,
    "examples": [
        "porthole sandbox up               # build if needed, then start it",
        "porthole sandbox shell            # a shell inside the workspace",
        "porthole sandbox shell --command pmbootstrap status",
        "porthole sandbox status",
        "porthole sandbox down",
    ],
}
