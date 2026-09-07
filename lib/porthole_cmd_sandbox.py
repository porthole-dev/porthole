# SPDX-License-Identifier: MIT
"""`porthole sandbox` -- run pmbootstrap without handing the host to an agent.

One boundary: a rootless podman container where container-root maps to YOUR
uid. Nothing in it can exceed your own privileges, so even a full escape
reaches only the directories you mounted.

There was a second tier once -- `ph-sudo`, a validating privilege broker for a
host without podman. It is gone. It granted a real sudoers entry, its own
documentation admitted it could not contain a determined chroot payload, and
keeping a weaker path available meant every reader had to decide which one they
were on. The workspace needs no sudoers entry at all.

The threat model is in docs/SANDBOX.md. Read it before trusting this.
"""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_STATE, EX_USAGE

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

# Where pmaports lands inside the workspace. Exactly the path pmbootstrap
# derives from `work` = /pmb, so both pmbootstrap and ph-build.sh's own
# _PH_APORTS fallback find it with no extra configuration on either side.
# Named once because three places have to agree: the mount, the pmbootstrap
# config written into the work dir, and the check that the workspace can see
# it at all.
APORTS_IN = "/pmb/cache_git/pmaports"
# Where the image keeps the pmbootstrap SOURCE checkout. The apk package
# ships no helpers/, and helpers/envkernel.sh is what every build uses.
PMBOOTSTRAP_SRC_IN = "/opt/pmbootstrap-src"

# The workspace gets its OWN pmbootstrap work directory, and does not share the
# host's.
#
# Not a preference. A work dir built by the old host-root path is owned by uid
# 0 on the host, and `--userns=keep-id:uid=0,gid=0` maps your uid and nothing
# else -- so inside the container it reads as `nobody` and root-in-there cannot
# write a byte of it. It surfaces as `cp /etc/resolv.conf ...chroot_native/etc/`
# failing, a message about resolv.conf, on the first chroot a build touches.
#
# And it cannot be converted. `chown -R` would flatten the uids INSIDE the
# chroots -- pmos, the build user that owns .output, collapses into root -- and
# the userns has no mapping for those host uids anyway (the subuid range starts
# far above them). A work dir the container creates itself has correct
# ownership on both sides by construction, which is the only version of this
# that works.
#
# `porthole build --host` still uses PORTHOLE_PMB_DIR, so the old one keeps
# working for anyone who wants it.
SANDBOX_PMB_DEFAULT = "~/.local/var/porthole-sandbox"

# Inside the container the work dir is /pmb, and pmbootstrap's config is pinned
# there by the image's wrapper. Anything naming a HOST path in that file is a
# path the container cannot resolve -- the bug class docs/SANDBOX-PROVISIONING.md
# calls out -- so `work` and `aports` are rewritten, never carried over.
PMB_CFG_NAME = "pmbootstrap_v3.cfg"

# Settings worth carrying from the host's own pmbootstrap config so the
# workspace builds the same postmarketOS the host would. Anything not listed --
# `work`, `aports`, any other path -- is deliberately dropped.
PMB_CFG_CARRY = ("ui", "kernel", "systemd", "service_manager", "user",
                 "timezone", "is_default_channel", "ssh_keys",
                 "extra_packages", "locale", "hostname")


def _stamp_work_version() -> tuple[bool, str]:
    """Give the fresh work dir the version marker `pmbootstrap init` would.

    pmbootstrap refuses to touch a work directory whose `version` file does not
    match, and an ABSENT file reads as version 0 -- so a brand new work dir gets
    "Your work folder version needs to be migrated (from version  to 8)" and
    then "we can't migrate that automatically ... delete your current work
    folder". A first run that tells you to delete the thing it just created.

    `pmbootstrap init` writes it, but init is interactive and this workspace
    exists to be driven with no TTY. So write the marker, and take the number
    from pmbootstrap itself rather than pinning it here -- a hardcoded 8 would
    silently be wrong the next time upstream bumps it.
    """
    script = (
        'test -f /pmb/version && exit 0; '
        'python3 -c "import pmb.config, pathlib; '
        'pathlib.Path(\'/pmb/version\').write_text(str(pmb.config.work_version))"'
    )
    done = subprocess.run(["podman", "exec", CONTAINER, "sh", "-c", script],
                          capture_output=True, text=True)
    return done.returncode == 0, (done.stderr or done.stdout).strip()


def _sandbox_pmb(cfg) -> pathlib.Path:
    """The workspace's own pmbootstrap work dir."""
    value = (cfg.get("PORTHOLE_SANDBOX_PMB_DIR")
             or os.environ.get("PORTHOLE_SANDBOX_PMB_DIR")
             or SANDBOX_PMB_DEFAULT)
    return pathlib.Path(value).expanduser()


def _host_pmb_cfg() -> dict:
    """What the host's pmbootstrap is configured to build, if it is configured.

    Read on the HOST and only for the keys in PMB_CFG_CARRY. The file itself is
    never mounted: it names host paths, and a container that resolved them
    would be reaching outside the boundary this whole design exists to keep.
    """
    xdg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME")
                       or pathlib.Path.home() / ".config")
    out = {}
    try:
        for line in (xdg / PMB_CFG_NAME).read_text().splitlines():
            key, _, value = line.partition("=")
            key = key.strip()
            if key in PMB_CFG_CARRY:
                out[key] = value.strip()
    except OSError:
        pass
    return out


def resolve_pmb_kernel(cfg, pmaports, device: str) -> tuple[str, str]:
    """(value, why) for pmbootstrap's `kernel` setting. "" when it has no say.

    pmbootstrap defaults this to `stable` and only `pmbootstrap init` -- an
    interactive command the workspace tier exists to avoid needing -- ever
    changes it. A device offering only `mainline`, which is most bring-ups,
    therefore fails INSIDE `pmbootstrap install`, after the rootfs chroot has
    been created, with "run 'pmbootstrap init' to select a valid kernel": a
    twenty-minute path to a question pmaports had already answered.

    Precedence, and each step earns its place: an explicit
    PORTHOLE_PMB_KERNEL is the developer's word; the host's own pmbootstrap
    config is what they configured for host builds and the workspace should
    not silently differ from it; and the derived answer is used only when the
    device offers exactly one, because choosing between two flavours changes
    which kernel lands on the phone and is not a guess to make for someone.
    """
    import porthole_pmaports as pmap

    explicit = (cfg.get("PORTHOLE_PMB_KERNEL") or "").strip()
    if explicit:
        return explicit, "PORTHOLE_PMB_KERNEL"
    carried = (_host_pmb_cfg().get("kernel") or "").strip()
    if carried:
        return carried, "the host's pmbootstrap config"
    if not pmaports:
        return "", ""
    flavours = pmap.device_kernels(pmaports, device)
    if len(flavours) == 1:
        only = next(iter(flavours))
        return only, f"the only one device-{device} offers"
    return "", ""


def pmb_kernel_problem(value: str, pmaports, device: str) -> str:
    """Why pmbootstrap will refuse this `kernel` setting, or "".

    Same check `pmb.install.get_kernel_package` makes, run before the build
    rather than after the chroot work. An empty flavour set means the kernel
    is hardcoded in the device package's depends and the setting is not
    consulted at all -- so it cannot be wrong.
    """
    import porthole_pmaports as pmap

    if not pmaports or value == "none":
        return ""
    flavours = pmap.device_kernels(pmaports, device)
    if not flavours or value in flavours:
        return ""
    offered = ", ".join(sorted(flavours))
    if not value:
        return (f"pmbootstrap will default `kernel` to `stable`, which "
                f"device-{device} does not offer ({offered}). Set "
                f"PORTHOLE_PMB_KERNEL")
    return (f"`kernel = {value}` is not one device-{device} offers "
            f"({offered}) -- pmbootstrap refuses it inside `install`, after "
            f"the rootfs chroot is built")


def pmb_config_text(device: str, carry: dict | None = None,
                    kernel: str = "") -> str:
    """The pmbootstrap config the workspace uses, as an INI string.

    `work` AND `aports` both have to be set. `aports` defaults to
    `work / "cache_git" / "pmaports"` evaluated against the DEFAULT work dir at
    class-definition time, so setting `work` alone still sends pmbootstrap
    looking under /root -- which reads as "pmaports dir not found: /root/..."
    and looks like a missing clone rather than a config that half applied.
    """
    rows = {"work": "/pmb", "aports": APORTS_IN}
    if device:
        rows["device"] = device
    # Resolved by the caller, which has already consulted `carry` -- so it is
    # set here rather than setdefault'd, and an empty value leaves pmbootstrap
    # its own default.
    if kernel:
        rows["kernel"] = kernel
    for key, value in (carry or {}).items():
        rows.setdefault(key, value)
    body = "".join(f"{k} = {v}\n" for k, v in rows.items())
    return f"[pmbootstrap]\n{body}\n[providers]\n\n[mirrors]\n"


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


def _mounts(root, pmb_dir, workdir, key, device, extra, aports=None):
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
    # pmaports is SHARED with the host rather than cloned fresh, and that is a
    # correctness decision before it is a disk one. This checkout is on a
    # bring-up branch and carries the device's own aports -- the kernel package
    # every rung builds lives there and nowhere upstream. A fresh clone would
    # come up on master without them, and the failure would read as a missing
    # package rather than as the wrong pmaports.
    #
    # Mounted INSIDE /pmb, at exactly the path pmbootstrap derives from `work`,
    # so both pmbootstrap and ph-build.sh's own `_PH_APORTS` fallback find it
    # with no extra configuration on either side.
    if aports:
        mounts.append((str(aports), APORTS_IN, "rw"))
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


def _up_argv(root, image, mounts, device, channels_cfg: str = "") -> list[str]:
    """The persistent workspace.

    Not `--rm`: all state lives in the mounts above, so the container itself is
    disposable and keeping it costs nothing -- while recreating it per command
    would cost a second every time and lose the running adb server.

    Deliberately NOT here: `--network=host` (pasta already reaches the device
    on the default netns) and `--privileged` (the entire point).
    """
    argv = ["podman", "run", "-d", "--name", CONTAINER,
            # PID 1 here is `sleep infinity`, which reaps nothing. An
            # interrupted build leaves its compiler children unreaped: four
            # zombie clang++ processes were left by one cancelled webkit run,
            # and this container is meant to live for weeks. --init supplies a
            # real init that reaps them.
            "--init",
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
    # Same shape again: the user config names a HOST pmbootstrap checkout, and
    # that path does not exist in here. The image carries its own at a fixed
    # path, matching the installed CLI's version.
    argv += ["-e", "PORTHOLE_PMBOOTSTRAP_SRC=" + PMBOOTSTRAP_SRC_IN]
    # The fourth of these, found by audit rather than by a failing build. The
    # work dir is MOUNTED at /pmb but the config still named the host path, so
    # ph-build.sh and the pmaports lookup would both have read a directory that
    # does not exist in here.
    argv += ["-e", "PORTHOLE_PMB_DIR=/pmb"]
    # The fifth, and the one that cost a flash. config.env names the HOST's
    # platform-tools fastboot; in here that path does not exist, so every
    # `fastboot devices` exited 127 with EMPTY STDOUT -- byte-for-byte what a
    # phone that is NOT in the bootloader looks like. A `fast` build reached
    # "ALL CHECKS PASSED - safe to flash", sent the phone to the bootloader,
    # then burned its whole budget and timed out after 181.2s reporting that it
    # never got there, with the phone sitting in fastboot the entire time. The
    # image's own fastboot is on PATH, so name it bare.
    #
    # ADB is the same shape -- config.env names the host's platform-tools adb
    # too -- and is deliberately left for its own change: nothing in lib/ or
    # tools/ ever EXECUTES $ADB (doctor only checks the path, init only writes
    # it), so in here it is a latent host path rather than a live one. Verified
    # read-only 2026-09-01: the image does carry a working /usr/bin/adb from
    # android-tools, so `adb` is the right value when that change is made.
    argv += ["-e", "FASTBOOT=fastboot"]
    # The sixth, and the one that stops a forked pmaports dead. pmbootstrap
    # reads channels.cfg from `<upstream-remote>/main` and identifies the
    # upstream by matching a remote URL against two hardcoded postmarketOS
    # ones -- so a bring-up fork, which is the normal state for this tool,
    # matches neither and every `pmbootstrap install` dies naming a remote
    # rather than the file it wanted. Decided on the HOST, where git is
    # reliable and the real checkout is, and sent as the CONTAINER path: the
    # same translation PORTHOLE_KERNEL_TREE gets, and for the same reason.
    if channels_cfg:
        argv += ["-e", f"PMB_CHANNELS_CFG={APORTS_IN}/channels.cfg"]
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


def _container_mounts() -> set:
    """Destination paths mounted into the running container, "" set on error.

    A container is created ONCE, with the mounts its config named at that
    moment. Set a working repo afterwards -- which `porthole init` now does,
    and which is the ordinary order of events on a new host -- and the
    workspace still has no /work: ph-build.sh dies on its own
    `${PORTHOLE_WORKDIR:?}` with a message about a variable, naming neither the
    container nor the fix.
    """
    try:
        proc = subprocess.run(
            ["podman", "inspect", "-f",
             "{{range .Mounts}}{{.Destination}}\n{{end}}", CONTAINER],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _inside(path, base) -> bool:
    """Is `path` at or under `base`? Resolved, so a symlink cannot lie."""
    try:
        pathlib.Path(path).resolve().relative_to(pathlib.Path(base).resolve())
    except (ValueError, OSError):
        return False
    return True


def aports_gap(cfg, mounts) -> str:
    """Why the workspace cannot see pmaports, or "". PURE given `mounts`.

    Two ways it can be there and only one of them is a mount: the workspace's
    own work dir may hold a clone at the path pmbootstrap derives (`pmbootstrap
    init` run inside the container puts one there), and that arrives through
    /pmb rather than through a bind of its own. Checking the mount list alone
    would call a working workspace broken.
    """
    if APORTS_IN in mounts:
        return ""
    own = _sandbox_pmb(cfg) / "cache_git" / "pmaports"
    if (own / "device").is_dir():
        return ""
    # WHICH of the two problems it is. They have different fixes, and telling
    # someone to go and find a pmaports checkout they have already configured
    # is how "one command sets this host up" stops being believed.
    import porthole_pmaports as pmap

    found, via = pmap.find_pmaports_with_source(cfg)
    if found:
        return (f"the workspace cannot see pmaports ({found}, via {via}) -- "
                f"it started before that was configured, and mounts are fixed "
                f"when the container is created. `porthole sandbox down` then "
                f"`up`")
    return ("no pmaports checkout on this host, so nothing in the workspace "
            "can build -- `porthole init` adopts one or clones it, then "
            "`porthole sandbox down` and `up`")


def workdir_drift(cfg, mounts) -> str:
    """Why the running workspace cannot build for this device, or "". PURE.

    Only the case that actually breaks a build: a working repo is configured
    on the host and the container has no /work to find it at. The reverse --
    a container carrying /work while the host has forgotten the key -- is the
    host's problem and `porthole build` already names it.
    """
    workdir = (cfg.get("PORTHOLE_WORKDIR") or "").strip()
    if not workdir or "/work" in mounts:
        return ""
    return ("the running workspace has no working repo mounted, so no build "
            "can run in it -- `porthole sandbox down` then `up`")


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
        # "already up" was a complete answer only while the mounts could not
        # go stale. They can: they are fixed at creation, so a workspace
        # started before `porthole init` set the working repo has no /work and
        # cannot build -- and `up`, the command someone runs to fix exactly
        # that, said "already up" and changed nothing. Recreating it silently
        # would be worse (it is the caller's container and a build may be in
        # it), so say what is wrong and name the two commands.
        mounts = _container_mounts()
        stale = [m for m in (aports_gap(ctx.cfg, mounts),
                             workdir_drift(ctx.cfg, mounts)) if m]
        if stale:
            for message in stale:
                ctx.out.warn(message)
            ctx.out.hint("porthole sandbox down",
                         "then `up` -- mounts are fixed when the container is created")
            return EX_STATE
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

    # The workspace's own work dir, not the host's -- see SANDBOX_PMB_DEFAULT.
    pmb = _sandbox_pmb(ctx.cfg)
    pmb.mkdir(parents=True, exist_ok=True)
    # Written every `up`, not once: it is cheap, it keeps the device in step
    # with `porthole use`, and a config that silently went stale is the kind of
    # thing that surfaces four minutes into a build as the wrong device.
    # The kernel flavour has to be decided HERE, where pmaports is readable and
    # the profile is loaded. Deciding it inside the container would mean
    # `pmbootstrap init`, which is interactive and is the thing this tier
    # exists to remove.
    import porthole_pmaports as _pmap

    _checkout = _pmap.find_pmaports(ctx.cfg)
    kernel, kernel_why = resolve_pmb_kernel(ctx.cfg, _checkout, device)
    (pmb / PMB_CFG_NAME).write_text(
        pmb_config_text(device, _host_pmb_cfg(), kernel))
    if kernel:
        ctx.out(ctx.out.paint(f"  kernel: {kernel}  ({kernel_why})", "grey"))
    problem = pmb_kernel_problem(kernel, _checkout, device)
    if problem:
        ctx.out.warn(problem)

    # The host's pmaports checkout, shared rather than re-cloned.
    #
    # Through the SHARED resolver, not a hardcoded path. This looked only in
    # `$PORTHOLE_PMB_DIR/cache_git/pmaports` -- pmbootstrap's own clone -- so a
    # host whose pmaports is named by PORTHOLE_PMAPORTS or the per-device key
    # was told "the workspace has nothing to build from, run `pmbootstrap init`
    # on the host", which is both false and the opposite of what the workspace
    # tier exists to avoid: `porthole init` had already ASKED for that checkout
    # and written the key, and `sandbox up` did not read it. The workspace then
    # came up with no pmaports mounted at all and every build in it would have
    # failed on a missing aport.
    #
    # Four things can answer "where is pmaports" and find_pmaports_with_source
    # is the one place that knows all four and can say which answered.
    import porthole_pmaports as pmap

    checkout, via = pmap.find_pmaports_with_source(ctx.cfg)
    aports = checkout
    if aports and _inside(aports, pmb):
        # It already lives in the work dir mounted at /pmb, so it arrives in
        # the container for free. Binding it over itself is at best redundant
        # and at worst a nested mount podman has to unpick.
        ctx.out(ctx.out.paint(f"  pmaports: {aports}  (inside the work dir)",
                              "grey"))
        aports = None
    elif aports:
        ctx.out(ctx.out.paint(f"  pmaports: {aports}  (via {via})", "grey"))
    else:
        ctx.out(ctx.out.paint(
            "  note: no pmaports checkout found on this host, so the "
            "workspace\n"
            "  has nothing to build from. `porthole init` adopts one or "
            "clones it.",
            "yellow"))

    mounts = _mounts(ctx.root, str(pmb), ctx.cfg.get("PORTHOLE_WORKDIR"), key,
                     device, args.mount, aports)
    # Asked on the host, where git works and the checkout really is. Empty for
    # a stock clone, which keeps pmbootstrap's own behaviour exactly.
    # `checkout`, not `aports`: a clone that lives inside the work dir needs no
    # mount and still needs this, and reading the one that was zeroed for the
    # mount decision would skip exactly that case.
    channels_cfg = pmap.channels_cfg_override(checkout) if checkout else ""
    if channels_cfg:
        ctx.out(ctx.out.paint(
            "  pmaports has no postmarketOS remote (a fork), so pmbootstrap "
            "cannot find\n"
            "  channels.cfg the way it insists on reading it. Pointing "
            "PMB_CHANNELS_CFG at\n"
            "  the checkout's own copy -- its own documented override, and no "
            "write to your repo.", "grey"))
    rc = subprocess.run(
        _up_argv(ctx.root, tag, mounts, device, channels_cfg)).returncode
    if rc == 0:
        # Device nodes are bind mounts and live in the container's mount
        # namespace, so a restart leaves empty files where a chroot's dev/null
        # was. Re-arm before anything runs; see the image's porthole-devnodes.
        subprocess.run(["podman", "exec", CONTAINER, "porthole-devnodes"],
                       capture_output=True)
        stamped, why = _stamp_work_version()
        if not stamped:
            ctx.out(ctx.out.paint(
                f"  WARNING: could not stamp {pmb}/version -- pmbootstrap will\n"
                f"  refuse the work dir and advise deleting it. {why}", "yellow"))
        ctx.out(ctx.out.paint(
            f"  {CONTAINER} up. root inside maps to uid {os.getuid()} outside.\n"
            f"  work dir: {pmb}  (the workspace's own; --host uses yours)\n"
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
            # Split on comma AS WELL as whitespace. sudoers joins Defaults
            # options with commas -- `timestamp_timeout=9999,timestamp_type=
            # global` is what this host actually has -- so splitting on
            # whitespace alone captured "9999,timestamp_type=global", float()
            # raised, and the except below swallowed it. The check for a
            # 167-hour root cache had therefore never fired on the machine it
            # was written for.
            value = re.split(r"[\s,]", line.partition("timestamp_timeout")[2]
                             .lstrip("= "))[0]
            out["timestamp_timeout"] = value
            try:
                if float(value) > 60:
                    out["issues"].append(
                        f"timestamp_timeout={value} minutes: any process running "
                        f"as you gets silent root for {float(value)/60:.0f} hours. "
                        f"Nothing here needs it -- the workspace has no "
                        f"sudoers entry at all.")
            except ValueError:
                pass
        if "NOPASSWD: ALL" in line and not line.startswith("#"):
            out["nopasswd_all"] = True
            out["issues"].append("a NOPASSWD: ALL rule grants unrestricted "
                                 "passwordless root")
    return out


def _device_key_authorized(cfg, key):
    """True / False / None-for-unknown: does the phone accept the device key?

    Three states, not two. An unreachable device or a name that does not
    resolve is not evidence the key is bad, and a check that cries failure when
    it does not know is a check people learn to scroll past. Only an explicit
    "Permission denied" is a False.

    Run from the host with the same key file the container mounts read-only.
    The container is where it matters, but a check that needs a running
    workspace to answer says nothing on the day the workspace is what broke --
    and it is the same bytes on both sides of the mount.
    """
    phone = cfg.get("PHONE") or ""
    if not phone or not key.exists() or not shutil.which("ssh"):
        return None
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
             # IdentitiesOnly, or a working agent key masks a device key that
             # is not installed -- the check would pass for the wrong reason.
             "-i", str(key), "-o", "IdentitiesOnly=yes",
             phone, "true"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 0:
        return True
    return False if "Permission denied" in proc.stderr else None


def _container_state(root: pathlib.Path, cfg=None, probe_device: bool = True) -> dict:
    out = {"podman": shutil.which("podman"), "image": _image_tag(root),
           "image_built": False, "container_running": False,
           "device_key": "", "issues": []}
    # The workspace's own work dir. It must be OURS: a rootless container maps
    # your uid and nothing else, so one built by host root is unwritable in
    # there and cannot be converted.
    pmb = _sandbox_pmb(cfg or {})
    out["work_dir"] = str(pmb)
    try:
        out["work_dir_ok"] = pmb.stat().st_uid == os.getuid()
    except OSError:
        out["work_dir_ok"] = False
    if pmb.exists() and not out["work_dir_ok"]:
        out["issues"].append(
            f"{pmb} is not owned by you -- the workspace cannot write it. "
            f"`podman unshare rm -rf {pmb}` then `porthole sandbox up`")
    key = pathlib.Path.home() / DEVICE_KEY
    out["device_key"] = str(key) if key.exists() else ""
    # Does the DEVICE accept it -- not merely, does the file exist.
    #
    # `porthole doctor` reported this green on a phone whose authorized_keys
    # had been lost in a fresh install, so doctor was ok while every workspace
    # push failed with `scp: Connection closed`. Same class of error as
    # trusting an exit code over content: the file being present is a PROXY for
    # the thing that matters, and the proxy held while the thing did not.
    # `None` is already this field's "could not tell" -- an unreachable device
    # is not evidence the key is bad -- so a skipped probe needs no new state
    # and no reader has to change. See _device_key_authorized.
    out["device_key_authorized"] = (
        _device_key_authorized(cfg or {}, key) if probe_device else None)
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
    # The mounts are fixed at creation. A workspace started before the working
    # repo was configured has no /work, so every build in it fails on
    # ph-build.sh's own `${PORTHOLE_WORKDIR:?}` -- and `sandbox status`
    # reported "sandbox is configured" the whole time, which is the shape of
    # green this file has already been burned by twice.
    out["workdir"] = ((cfg or {}).get("PORTHOLE_WORKDIR") or "").strip()
    # Tri-state, the same shape as the device key one row below, and for the
    # same reason: "no working repo is configured" and "one is configured and
    # the container cannot see it" are different problems with different
    # fixes, and only the second is the workspace's fault.
    out["workdir_mounted"] = None
    out["aports_visible"] = None
    if out["container_running"]:
        # One inspect answers both questions.
        mounts = _container_mounts()
        gap = aports_gap(cfg or {}, mounts)
        out["aports_visible"] = not gap
        if gap:
            out["issues"].append(gap)
        if out["workdir"]:
            drift = workdir_drift(cfg or {}, mounts)
            out["workdir_mounted"] = not drift
            if drift:
                out["issues"].append(drift)
    if not out["device_key"]:
        out["issues"].append("no device key yet -- `porthole sandbox up` "
                             "creates one so the workspace never needs ~/.ssh")
    elif out["device_key_authorized"] is False:
        out["issues"].append(
            "the device refuses the workspace key, so every build that pushes "
            "will fail. Add it to the phone's authorized_keys: "
            f"ssh-keygen -y -f {out['device_key']}")
    return out


def cmd_sandbox(args, ctx) -> int:
    action = args.action or "status"
    if action == "status":
        return _status(ctx)
    if action == "shell":
        return _shell(ctx, args)
    if action == "build":
        return _build(ctx, args)
    if action == "up":
        return _up(ctx, args)
    if action == "down":
        return _down(ctx)
    raise Bail(f"unknown action {action!r}", EX_USAGE,
               "actions: status, shell, build, up, down")


def _status(ctx) -> int:
    state = {
        "sudo": _sudo_state(),
        "container": _container_state(ctx.root, ctx.cfg),
        "env": {"PMB_SUDO": os.environ.get("PMB_SUDO", "")},
        "stale_env": (["PMB_SUDO=" + os.environ["PMB_SUDO"]]
                      if os.environ.get("PMB_SUDO") else []),
    }
    # The host's sudo cache is reported even though the workspace does not use
    # it: a 167-hour credential cache is a hazard on this machine whether or
    # not anything here needs root, and "we stopped needing it" is not the same
    # as "it is harmless".
    workspace_up = (state["container"]["image_built"]
                    and state["container"]["container_running"])
    issues = sum((state[k].get("issues", []) for k in ("container", "sudo")), [])
    state["workspace_up"] = workspace_up
    state["ok"] = not issues

    def render():
        o = ctx.out
        tick, cross = o.sym("✓", "ok"), o.sym("✗", "XX")

        def line(label, good, detail):
            mark = o.paint(tick, "green") if good else o.paint(cross, "red")
            o(f"  {mark} {label:<22} {detail}")

        o.heading("workspace")
        c = state["container"]
        line("podman", bool(c["podman"]), c["podman"] or o.paint("not installed", "grey"))
        line("image", c["image_built"],
             c["image"] if c["image_built"] else o.paint("not built", "grey"))
        line("container", c["container_running"],
             CONTAINER if c["container_running"] else o.paint("not running", "grey"))
        line("work dir", c["work_dir_ok"],
             c["work_dir"] if c["work_dir_ok"]
             else o.paint(f"{c['work_dir']} -- not yours", "yellow"))
        # Tri-state: a present key that the device refuses is the case this
        # whole row exists for, and it is not the same as "not created".
        authorized = c.get("device_key_authorized")
        if not c["device_key"]:
            line("device key", False, o.paint("not created", "grey"))
        elif authorized is True:
            line("device key", True, f"{c['device_key']} -- the device accepts it")
        elif authorized is False:
            line("device key", False,
                 o.paint(f"{c['device_key']} -- the device REFUSES it", "red"))
        else:
            line("device key", True,
                 f"{c['device_key']} " + o.paint("(device not reachable to "
                                                 "check)", "grey"))
        # Without pmaports there is no aport to build and no device package,
        # so a workspace missing it is up and useless -- which is exactly what
        # it looked like, because nothing reported it.
        if c.get("aports_visible") is True:
            line("pmaports", True, f"mounted at {APORTS_IN}")
        elif c.get("aports_visible") is False:
            line("pmaports", False,
                 o.paint("not visible in the workspace -- nothing can build",
                         "red"))
        # A kernel or image build cannot run without this, and its absence was
        # invisible from every other row.
        if c.get("workdir_mounted") is True:
            line("working repo", True, f"{c['workdir']}  at /work")
        elif c.get("workdir_mounted") is False:
            line("working repo", False,
                 o.paint(f"{c['workdir']} is NOT mounted -- this workspace "
                         f"started before it was set", "red"))
        elif not c["workdir"]:
            # Not a cross: `porthole pkg build` needs no working repo, so a
            # workspace without one is not broken -- only unable to build a
            # kernel or a system image, which is what the note says.
            o("  {} {:<22} {}".format(
                o.mark("skip"), "working repo",
                o.paint("none set -- `porthole init` writes it; kernel and "
                        "image builds need it", "grey")))
        o.blank()

        o.heading("host sudo")
        s = state["sudo"]
        line("credential cache", not s["issues"],
             f"timestamp_timeout={s['timestamp_timeout']}"
             if s["timestamp_timeout"] else "default")
        o.blank()

        if issues:
            o.heading("issues")
            for issue in issues:
                o(f"  {o.paint(o.sym('•', '-'), 'yellow')} {issue}")
            o.blank()
            if not workspace_up:
                o.hint("porthole sandbox up", "start the workspace")
            o.hint("docs/SANDBOX.md", "the threat model")
        else:
            o(o.paint("sandbox is configured", "green"))

    return ctx.emit(state, render)


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


# A raw pmbootstrap call inside the sandbox skips everything the wrapper verbs
# exist to provide -- the buildroot lock, --lax, the log, the progress bar, the
# artifact check. Every one of those was paid for:
#
#   no lock       two builds share one buildroot and abuild wipes $srcdir, so
#                 a second build silently deletes the first one's source tree.
#                 Cost 37 minutes on taimen; cost a whole kernel build on the
#                 redfin port, where a `checksum` run killed an active build.
#   no --lax      a non-lax build cannot run in the rootless workspace at all.
#   no progress   a multi-hour build that reports nothing, which is the exact
#                 black box `porthole pkg` was built to end.
#
# So this is not a style preference, and it is why the redirect is a refusal
# rather than a hint: a hint printed above four hours of silence is not read.
_REDIRECT = (
    ("build", "porthole pkg build <aport>"),
    ("checksum", "porthole aports checksum <aport>"),
    ("pkgrel_bump", "porthole aports bump <aport>"),
)


def redirect_for(command) -> tuple:
    """`(pmbootstrap subcommand, the porthole verb to use)`, or `()`.

    Only the subcommands that MUTATE the buildroot. `pmbootstrap status`,
    `log`, `config` and `pull` are read-only or cheap and stay available --
    refusing those would make this guard something people route around.
    """
    if not command:
        return ()
    text = " ".join(command) if isinstance(command, (list, tuple)) else str(command)
    if "pmbootstrap" not in text:
        return ()
    words = text.replace(";", " ").replace("&&", " ").split()
    for i, word in enumerate(words):
        if not word.endswith("pmbootstrap"):
            continue
        for candidate in words[i + 1:]:
            if candidate.startswith("-"):
                continue
            for name, verb in _REDIRECT:
                if candidate == name:
                    return (name, verb)
            break
    return ()


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
                   "`porthole doctor` names how to install it")
    if not _container_running():
        raise Bail(f"{CONTAINER} is not running", EX_FAIL,
                   "run `porthole sandbox up` first")
    _assert_lock_matches(ctx)

    hit = () if getattr(args, "raw", False) else redirect_for(args.command)
    if hit:
        name, verb = hit
        raise Bail(
            f"`pmbootstrap {name}` here takes no buildroot lock", EX_USAGE,
            f"use `{verb}` instead -- it locks the buildroot, passes --lax, "
            f"logs, and shows progress. `--raw` overrides this if you really "
            f"mean to bypass all of that.")

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
    "group": "build",
    "help": "run pmbootstrap without handing the host to an agent",
    "description": (
        "pmbootstrap needs root. The usual workaround -- a multi-day sudo\n"
        "credential cache -- gives every process running as you silent,\n"
        "unlimited root, which is not something to hand an agent.\n\n"
        "So it runs in a persistent rootless container instead, where you are\n"
        "root inside and your own unprivileged uid outside. No sudoers entry,\n"
        "no standing privilege. `up` builds and starts it; `shell --command`\n"
        "works without a TTY, which is what makes it usable by an agent.\n\n"
        "`build` here means the CONTAINER IMAGE. To build a PACKAGE, the\n"
        "verb is `porthole pkg build <aport>` -- anyone reaching for\n"
        "\"build a package with porthole\" lands on this verb first and\n"
        "loses five minutes to it.\n\n"
        "See docs/SANDBOX.md for the threat model."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["status", "shell", "build", "up", "down"],
                      "help": "status | shell | build (the container IMAGE, "
                              "not a package) | up | down"}),
        (["--mount"], {"action": "append", "metavar": "PATH",
                       "help": "up: extra path to mount into the workspace"}),
        (["--command"], {"nargs": "...", "help": "shell: command instead of a shell"}),
        (["--dry-run"], {"action": "store_true",
                         "help": "shell: print the podman command and stop"}),
        (["--raw"], {"action": "store_true",
                     "help": "shell: allow a raw pmbootstrap build/checksum, "
                             "bypassing the buildroot lock"}),
        (["--force"], {"action": "store_true",
                       "help": "build: rebuild the container image even if "
                               "the tag exists"}),
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
