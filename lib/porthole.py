#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""porthole config resolution and device transport, for the python tools.

Import this instead of reading os.environ["PHONE"] directly. Every tool that
does its own environment lookup is a tool that has to be edited when a knob
moves; this is the one place that knows the layering.

    import porthole
    dev = porthole.Device()
    boot_id = dev.run("cat /proc/sys/kernel/random/boot_id")

Stdlib only, by design: the toolbox has to work on a bring-up machine that
has pmbootstrap and nothing else, and half of it is bash that cannot import
anything at all. lib/porthole.sh implements the same semantics; the two are
held together by tests/test_shell_lib.sh, which diffs their output.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

__all__ = [
    "Config", "ProfileNotFound", "load_config", "list_profiles",
    "resolve_phone", "resolve_host", "legacy", "ssh_opts", "slot_suffix", "Device",
    "find_root", "DEFAULTS",
]


# Built-in defaults -- layer 1, the lowest. Anything here is a value that is
# safe on a device nobody has told us about yet. Device-specific facts belong
# in a profile, not here.
DEFAULTS = {
    "PORTHOLE_DEVICE": "",
    "PORTHOLE_USER": "user",
    "PORTHOLE_HOST": "172.16.42.1",      # the pmOS USB-gadget address
    "PORTHOLE_SSH_PORT": "22",
    "PORTHOLE_SSH_KEY": "",
    "PORTHOLE_AGENT": "",
    "PORTHOLE_POLL": "0.5",
    "PORTHOLE_CONNECT_TIMEOUT": "2",
    "PORTHOLE_NET_DEVICE_IP": "172.16.42.1",
    "PORTHOLE_NET_HOST_IP": "172.16.42.2",
    "FASTBOOT": "fastboot",
    "ADB": "adb",
    "PORTHOLE_PMB_DIR": "",
    # Prefer PORTHOLE_WORKDIR_<CODENAME>; this is the fallback for a
    # single-device setup and for one-off overrides.
    "PORTHOLE_WORKDIR": "",
    "PORTHOLE_NO_MUX": "0",
    "PORTHOLE_MUX_PERSIST": "60s",
    # Device facts, defaulted to the conservative answer. A device with no
    # profile is assumed to have no A/B slots, because the failure mode of
    # wrongly assuming slots is flashing a partition that does not exist.
    "PORTHOLE_SLOTS_PROBED": "",
    # Build and verify inputs that cannot be guessed from the tree.
    "PORTHOLE_DTS_INCLUDES": "",
    "PORTHOLE_DTS_DEPS": "",
    "PORTHOLE_DTC_BASELINE": "",
    "PORTHOLE_DTC_IGNORE": "",
    "PORTHOLE_KERNEL_TREE": "",
    "PORTHOLE_ENVKERNEL": "",
    "PORTHOLE_PMBOOTSTRAP_SRC": "",
    "PORTHOLE_HAS_AB_SLOTS": "0",
    "PORTHOLE_ACTIVE_SLOT": "",
    "PORTHOLE_SLOT_FORBIDDEN": "",
    "PORTHOLE_BOOT_RETRIES": "",
    "PORTHOLE_USB_LIES_AS_FASTBOOT": "0",
    "PORTHOLE_REBOOT_MODE_VIA_SYSCALL": "0",
    "PORTHOLE_WATCHDOG_MAX_S": "",
}

ARCH_DIRS = {"aarch64": "arm64", "armv7": "arm", "armhf": "arm",
             "armv7l": "arm", "x86_64": "x86", "x86": "x86",
             "riscv64": "riscv"}

LAYER_DEFAULT = "default"
LAYER_PROFILE = "profile"
LAYER_USER = "user-config"
LAYER_ROOT = "root-env"
LAYER_ENV = "environment"


class ProfileNotFound(Exception):
    """A device was named that has no profiles/<name>/device.env.

    Deliberately fatal rather than resolving to an empty config: a typo in
    PORTHOLE_DEVICE that silently yielded no device facts would flash the
    default DTB onto whatever is plugged in.
    """


class FastbootUnavailable(Exception):
    """$FASTBOOT could not be executed, so the bootloader was never probed.

    Raised rather than answering "no", because the two are indistinguishable
    at the call site: a missing binary and a phone that is not in the
    bootloader both produce empty output. Conflating them cost a real flash --
    a stale FASTBOOT made `wait_fastboot` poll its entire budget insisting a
    phone that was sitting in the bootloader had never arrived.

    69 (EX_UNAVAILABLE), never 1: the check did not happen, which is not the
    same as the check failing. See brain/laws/exit-codes-are-an-api.md, and
    ph_need_fastboot in tools/tk-lib.sh, which guards the shell side.
    """


class Config(dict):
    """A resolved config that remembers which layer each value came from.

    The provenance is not decoration. `porthole config` prints it, and it is
    the fastest way to answer "why is this tool talking to the wrong phone" --
    the answer is nearly always a stale ~/.config/porthole/config.env or an
    exported PHONE somebody forgot about.
    """

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._sources: dict[str, str] = {}
        self._shadowed: dict[str, list] = {}

    def set(self, key: str, value: str, layer: str) -> None:
        # Remember what this displaced. The provenance above answers "where did
        # the winning value come from"; this answers "and what did it beat",
        # which is the question that matters when a stale export silently wins
        # over a committed profile -- a shell saying 6.18 while the profile in
        # git says 7.2, with a build quietly using the retired one.
        if key in self and self[key] != value:
            self._shadowed.setdefault(key, []).append(
                (self._sources.get(key, LAYER_DEFAULT), self[key]))
        self[key] = value
        self._sources[key] = layer

    def shadowed(self, key: str) -> list:
        """(layer, value) pairs this key had before the winner, oldest first."""
        return list(self._shadowed.get(key, []))

    def source(self, key: str) -> str:
        return self._sources.get(key, LAYER_DEFAULT)

    def sources(self) -> dict[str, str]:
        return dict(self._sources)


# The keys where a stale shell silently changes WHAT GETS BUILT OR FLASHED.
# Deliberately short. Everything else stays overridable without comment,
# because `PHONE=... tk-foo.sh` is a documented shape and the docs are full of
# it -- a guard that refuses those gets worked around, and then it protects
# nothing.
# Split by CONSEQUENCE, not by importance. A stale shell on one of these
# changes what gets built with no other symptom -- the build succeeds and the
# artefact is wrong -- so a build refuses.
GUARDED_KEYS = (
    "PORTHOLE_KERNEL_PKG",
    "PORTHOLE_KERNEL_TREE",
    "PORTHOLE_DEFCONFIG",
    # Same failure class as KERNEL_PKG, and it was missed on the first pass:
    # the profile's own comment says this must track PORTHOLE_KERNEL_PKG and
    # that a stale value fails the build with a bare `cp: cannot stat`. Both
    # were stale in the same shell for the same reason.
    "PORTHOLE_KCONFIG_FILE",
    "PORTHOLE_ARCH",
    "PORTHOLE_WORKDIR",
)

# PORTHOLE_DEVICE only WARNS, and that distinction was found by running the
# check rather than reasoning about it. Choosing a device from the environment
# (or `porthole -d`) is a documented workflow, and on the reference host the
# environment is the CORRECT one while the stored `porthole use` value is
# stale -- so refusing would block the normal setup to report that a config
# file needs tidying. It is also not silent the way the others are: every verb
# prints the device it is talking to.
ADVISORY_KEYS = ("PORTHOLE_DEVICE",)

# Set by `porthole -d CODENAME`, which reaches load_config as an environment
# value and is otherwise indistinguishable from a forgotten export. Without
# this the guard would refuse a documented flag on its first day.
DELIBERATE_DEVICE = "PORTHOLE_DEVICE_FROM_FLAG"


def drift(cfg: "Config") -> list:
    """Guarded keys where the ENVIRONMENT is beating a committed layer.

    Returns [{key, winning, winning_layer, committed, committed_layer,
    blocking}], empty when everything agrees. `blocking` is what a build
    refuses on; the rest is worth saying and not worth stopping for. Pure: a Config in, a list out, so every case is
    testable with no device and no repo state.

    Compares against the highest NON-environment layer that set the key, rather
    than against the profile specifically. That generalisation is load-bearing:
    PORTHOLE_KERNEL_PKG's committed source is the profile, but PORTHOLE_DEVICE's
    is the user config that `porthole use` writes, and one rule covers both.
    """
    out = []
    for key in GUARDED_KEYS + ADVISORY_KEYS:
        if cfg.source(key) != LAYER_ENV:
            continue
        if key == "PORTHOLE_DEVICE" and cfg.get(DELIBERATE_DEVICE):
            continue
        winning = cfg.get(key, "")
        for layer, value in reversed(cfg.shadowed(key)):
            if layer == LAYER_DEFAULT or not value:
                continue
            if value != winning:
                out.append({"key": key, "winning": winning,
                            "winning_layer": LAYER_ENV,
                            "committed": value, "committed_layer": layer,
                            "blocking": key in GUARDED_KEYS})
            break
    return out


def drift_lines(drifts: list) -> list:
    """The refusal, as lines. Shared so build and flash cannot drift apart
    about drift, and so the wording is testable."""
    lines = []
    for d in drifts:
        lines.append(f"{d['key']} disagrees with the {d['committed_layer']}")
        lines.append(f"    {d['winning_layer']:<22} {d['winning']}   <- winning")
        lines.append(f"    {d['committed_layer']:<22} {d['committed']}")
    return lines


# ------------------------------------------------------------------ parsing --

def parse_env(text: str) -> dict[str, str]:
    """Parse KEY=value lines. Tolerant of what people actually type.

    Accepts a leading `export`, spaces around `=`, quoted values, and a
    trailing `# comment` on an unquoted value. Silently ignores lines it
    cannot parse rather than aborting: a device.env with one bad line should
    cost you one key, not the whole toolbox.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not (key[0].isalpha() or key[0] == "_"):
            continue
        value = value.strip()
        # A quoted value ends at its closing quote; anything after it is a
        # comment. Checking only value[-1] gets `KEY="a"  # why` wrong -- it
        # keeps the quotes -- and that silently breaks every comparison against
        # the value. profiles/ is full of documented values, so this is the
        # common case, not the edge case.
        if value[:1] in ("\"", "'"):
            quote, rest = value[0], value[1:]
            value = rest.split(quote, 1)[0]
        else:
            value = value.split(" #", 1)[0].rstrip()   # strip inline comment
        out[key] = value
    return out


def _read(path: pathlib.Path) -> dict[str, str]:
    try:
        return parse_env(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, UnicodeError):
        return {}


# -------------------------------------------------------------------- roots --

def find_root(start: str | os.PathLike | None = None) -> pathlib.Path:
    """Locate the porthole checkout.

    PORTHOLE_ROOT wins; otherwise walk up from this file. Walking up from
    __file__ rather than the cwd matters because tools are invoked from
    anywhere -- a kernel tree, a pmaports checkout, an agent's scratch dir.
    """
    env_root = os.environ.get("PORTHOLE_ROOT")
    if env_root:
        return pathlib.Path(env_root).expanduser().resolve()
    here = pathlib.Path(start or __file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "profiles").is_dir() and (parent / "lib").is_dir():
            return parent
    return here.parent.parent


def list_profiles(root: str | os.PathLike | None = None) -> list[str]:
    """Device profiles, sorted. `_template` and dotfiles are not devices.

    A profile is a directory holding a `device.env`, not merely a directory.
    Listing bare directories made this function contradict itself one line
    later: `porthole aports worktree -d zzz-ruletest2` answered "no profile
    for device 'zzz-ruletest2': .../device.env does not exist. Known
    devices: google-cheetah, google-taimen, zzz-ruletest2" -- naming the
    device as known and missing in the same breath, because `new-device`
    had created the directory and not yet written the file.
    """
    base = pathlib.Path(root or find_root()) / "profiles"
    if not base.is_dir():
        return []
    return sorted(
        p.name for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
        and (p / "device.env").is_file()
    )


# ---------------------------------------------------------------- resolution --

def load_config(root: str | os.PathLike | None = None,
                env: dict[str, str] | None = None) -> Config:
    """Resolve the five layers into one Config.

    Lowest to highest: defaults, profile, user config.env, root .env, process
    environment. The process environment winning is what keeps every
    `PHONE=... tk-foo.sh` line in the taimen docs working unchanged.
    """
    env = dict(os.environ if env is None else env)
    root = pathlib.Path(
        env.get("PORTHOLE_ROOT") or root or find_root()).expanduser()

    cfg = Config()
    for key, value in DEFAULTS.items():
        cfg.set(key, value, LAYER_DEFAULT)

    # Which device? The env/root-.env answer has to be known before the
    # profile can be read, so this one key is resolved out of order.
    xdg = pathlib.Path(
        env.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
    user_file = xdg / "porthole" / "config.env"
    root_file = root / ".env"
    user_vals, root_vals = _read(user_file), _read(root_file)
    device = (env.get("PORTHOLE_DEVICE")
              or root_vals.get("PORTHOLE_DEVICE")
              or user_vals.get("PORTHOLE_DEVICE") or "")

    if device:
        profile = root / "profiles" / device / "device.env"
        if not profile.is_file():
            raise ProfileNotFound(
                f"no profile for device {device!r}: {profile} does not exist. "
                f"Known devices: {', '.join(list_profiles(root)) or '(none)'}. "
                f"Create one with `porthole new-device {device}`.")
        for k, v in _read(profile).items():
            cfg.set(k, v, LAYER_PROFILE)

    for k, v in user_vals.items():
        cfg.set(k, v, LAYER_USER)
    for k, v in root_vals.items():
        cfg.set(k, v, LAYER_ROOT)
    for k, v in env.items():
        cfg.set(k, v, LAYER_ENV)

    cfg.set("PORTHOLE_DEVICE", device,
            cfg.source("PORTHOLE_DEVICE") if device else LAYER_DEFAULT)

    # A working repo belongs to ONE device, but PORTHOLE_WORKDIR was a single
    # global key -- so switching devices silently left the previous device's
    # repo in place. That is not cosmetic: `docs new` then wrote one device's
    # notes into another's repository, and the milestone probes read one
    # device's .dts files as evidence about another. Evidence read from the
    # wrong tree is worse than no evidence.
    #
    # Resolved here rather than in `use` so that EVERY consumer is fixed at
    # once. The global key stays valid as a fallback, and an explicit
    # environment override still wins, so no existing setup breaks.
    if device and "PORTHOLE_WORKDIR" not in env:
        per_device = f"PORTHOLE_WORKDIR_{device.upper().replace('-', '_')}"
        mine = cfg.get(per_device)
        if mine:
            cfg.set("PORTHOLE_WORKDIR", mine, cfg.source(per_device))
        elif any(k.startswith("PORTHOLE_WORKDIR_") and cfg.get(k)
                 for k in list(cfg.keys())):
            # At least one device declares its own repo, so this is a
            # multi-device setup -- and in one, the bare global key cannot
            # answer a per-device question. It belongs to whichever device set
            # it last, and handing it to a different device is precisely the
            # contamination this resolution exists to stop. Better to have no
            # workdir and say so.
            cfg.set("PORTHOLE_WORKDIR", "", LAYER_DEFAULT)
    # pmaports is ONE clone that pmbootstrap also writes to, on ONE branch. A
    # device may have its own git worktree of it, on its own branch, so that
    # building for one device cannot see another's uncommitted packages.
    if device and "PORTHOLE_PMAPORTS" not in env:
        per_device = f"PORTHOLE_PMAPORTS_{device.upper().replace('-', '_')}"
        if cfg.get(per_device):
            cfg.set("PORTHOLE_PMAPORTS", cfg.get(per_device),
                    cfg.source(per_device))

    cfg.set("PORTHOLE_ROOT", str(root), cfg.source("PORTHOLE_ROOT"))
    cfg.setdefault("PORTHOLE_RUNDIR", str(root / ".run"))
    # The kernel source directory for an arch is not the arch name: a package
    # says aarch64, the tree says arch/arm64.
    cfg.setdefault("PORTHOLE_ARCH_DIR", ARCH_DIRS.get(
        cfg.get("PORTHOLE_ARCH", "aarch64"), cfg.get("PORTHOLE_ARCH", "")))
    return cfg


def legacy(cfg: dict, old: str, new: str, default: str = "") -> str:
    """Value of a legacy knob, falling back to its porthole-namespaced twin.

    Old name wins. That is the whole never-break-taimen contract in one
    function: TK_POLL beats PORTHOLE_POLL, TK_AGENT beats PORTHOLE_AGENT.
    """
    return cfg.get(old) or cfg.get(new) or default


def resolve_host(cfg: dict) -> str:
    """The bare device IP.

    HOST and TK_HOST are honoured first because the taimen tools and docs use
    them. PHONE is mined last: tk-stream.sh does `HOST=${PHONE#*@}`, so
    somebody who sets only PHONE must still get a pingable address.
    """
    for key in ("HOST", "TK_HOST"):
        if cfg.get(key):
            return cfg[key]
    phone = cfg.get("PHONE", "")
    if phone:
        return phone.rpartition("@")[2] or phone
    return cfg.get("PORTHOLE_HOST") or DEFAULTS["PORTHOLE_HOST"]


def resolve_phone(cfg: dict) -> str:
    """The ssh target, `user@host`. PHONE verbatim if set, else composed."""
    if cfg.get("PHONE"):
        return cfg["PHONE"]
    user = cfg.get("PORTHOLE_USER") or DEFAULTS["PORTHOLE_USER"]
    return f"{user}@{resolve_host(cfg)}"


_SLOT_SUFFIX = re.compile(r"\bandroidboot\.slot_suffix=_([ab])\b")


def slot_suffix(cmdline: str) -> str:
    """The active slot letter from a kernel command line, or "".

    The bootloader passes it and the running kernel keeps it in
    /proc/cmdline, so a BOOTED device can answer "which slot am I on" without
    being in fastboot. Verified on taimen: `androidboot.slot_suffix=_b`,
    matching the profile's committed PORTHOLE_ACTIVE_SLOT="b".

    Shared with boot-partlabel derivation, which needs the same value. One
    helper, not two.
    """
    match = _SLOT_SUFFIX.search(cmdline or "")
    return match.group(1) if match else ""


def ssh_opts(cfg: dict) -> list[str]:
    """The ssh flags every tool must use.

    The first three are not tunable and not laziness: this device regenerates
    its host keys on essentially every boot, so a real known_hosts file turns
    every tool into an interactive prompt that BatchMode then fails outright.

    ConnectTimeout keeps a probe against a vanished USB interface from
    stalling a poll loop; when the device is down the connect fails instantly
    anyway, so it costs nothing in the common case.

    ControlMaster is the single biggest speed win in the toolbox: without it
    every probe pays a full handshake (~200ms), with it a warm round trip is
    ~15ms. It is safe only because every reboot path calls mux_reset() first
    -- see Device.mux_reset.
    """
    opts = [
        "-o", f"ConnectTimeout={cfg.get('PORTHOLE_CONNECT_TIMEOUT', '2')}",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "BatchMode=yes",
    ]
    port = cfg.get("PORTHOLE_SSH_PORT", "22")
    if port and port != "22":
        opts += ["-p", port]
    if cfg.get("PORTHOLE_SSH_KEY"):
        opts += ["-i", os.path.expanduser(cfg["PORTHOLE_SSH_KEY"]),
                 "-o", "IdentitiesOnly=yes"]
    if cfg.get("PORTHOLE_NO_MUX", "0") != "1":
        rundir = cfg.get("PORTHOLE_RUNDIR") or "/tmp/porthole"
        os.makedirs(rundir, exist_ok=True)
        opts += [
            "-o", "ControlMaster=auto",
            # %C hashes host+port+user, so two profiles or two developers on
            # one machine never share a socket.
            "-o", f"ControlPath={rundir}/ssh-%C",
            "-o", f"ControlPersist={cfg.get('PORTHOLE_MUX_PERSIST', '60s')}",
        ]
    return opts


# ------------------------------------------------------------------- device --

class Device:
    """SSH/fastboot transport with the device-state semantics the tools need.

    Mirrors the tk_* shell helpers one for one so a tool can be ported between
    languages without relearning the protocol.
    """

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg if cfg is not None else load_config()
        self.phone = resolve_phone(self.cfg)
        self.host = resolve_host(self.cfg)
        self.fastboot = self.cfg.get("FASTBOOT") or "fastboot"
        self.poll = float(legacy(self.cfg, "TK_POLL", "PORTHOLE_POLL", "0.5"))
        self._timing = self.cfg.get("PORTHOLE_TIMING", "0") == "1"

    # -- plumbing --

    def _timed(self, label: str, start: float) -> None:
        if self._timing:
            ms = (time.monotonic() - start) * 1000
            print(f"[porthole] {label} {ms:.0f}ms", file=sys.stderr)

    def ssh_argv(self, command: str) -> list[str]:
        return ["ssh", *ssh_opts(self.cfg), self.phone, command]

    def run(self, command: str, timeout: float = 12,
            check: bool = False) -> str:
        """Run a command on the device, return stdout stripped.

        Returns "" on any failure. Callers that need to tell "empty output"
        from "unreachable" must use run_full() -- conflating the two is the
        bug behind tk_boot_id's retry loop.
        """
        return self.run_full(command, timeout=timeout, check=check)[1]

    def run_full(self, command: str, timeout: float = 12,
                 check: bool = False) -> tuple[int, str, str]:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                self.ssh_argv(command), capture_output=True, text=True,
                timeout=timeout, stdin=subprocess.DEVNULL)
            rc, out, err = proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except subprocess.TimeoutExpired:
            rc, out, err = 124, "", f"timed out after {timeout}s"
        except OSError as exc:
            rc, out, err = 127, "", str(exc)
        self._timed(f"ssh({command.split()[0] if command else '-'})", start)
        if check and rc != 0:
            raise RuntimeError(f"remote command failed ({rc}): {err or command}")
        return rc, out, err

    def mux_reset(self) -> None:
        """Tear down the ssh control master.

        MANDATORY before any reboot. Host keys change on essentially every
        boot here, so a master socket that outlives a reboot is a live handle
        to a dead sshd: the next command inherits the dead channel and hangs
        until ControlPersist expires instead of failing fast.
        """
        if self.cfg.get("PORTHOLE_NO_MUX", "0") == "1":
            return
        try:
            subprocess.run(["ssh", *ssh_opts(self.cfg), "-O", "exit",
                            self.phone], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass      # no ssh, or no master to tear down; neither is fatal

    # -- probes --

    def in_fastboot(self) -> bool:
        """True only in the real bootloader.

        This is the ONLY reliable discriminator on devices where lsusb
        mislabels the running pmOS gadget as fastboot (18d1:d001 on taimen).
        USB IDs cannot tell a booted phone from a bootloader.
        """
        start = time.monotonic()
        try:
            proc = subprocess.run([self.fastboot, "devices"],
                                  capture_output=True, text=True, timeout=5)
            found = bool(proc.stdout.strip())
        except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            # Ordered before OSError, which is their parent. The TOOL could
            # not run; that is not a phone that is absent from the bootloader.
            raise FastbootUnavailable(
                "FASTBOOT={!r} is not an executable command -- the TOOL is "
                "missing, not the phone ({}). Nothing was probed.".format(
                    self.fastboot, exc.strerror or exc)) from None
        except (subprocess.TimeoutExpired, OSError):
            # The tool RAN and gave no usable answer in time. A real "no".
            found = False
        self._timed("fastboot devices", start)
        return found

    def boot_id(self) -> str:
        """The running kernel's boot_id, or "" if unreachable.

        IT RETRIES, AND THAT IS LOAD-BEARING. A single timed-out read returns
        empty, and an empty boot_id compares unequal to every real one -- so a
        caller that takes its BASELINE through one transient failure sees "the
        device rebooted" on the very next read, forever. Empty must mean
        "unknown", never "changed".

        Paid for on taimen 2026-08-19: sshd was taking ~7.9s to answer a
        trivial command (the FROZEN/PAM stall), a 6s timeout ate the baseline,
        and a 20-cycle camera test printed BOOT_ID CHANGED and FAIL against a
        phone that had not rebooted. The timeout must sit ABOVE that stall or
        retrying buys nothing: two attempts at 12s, not three at 6s.
        """
        return self._boot_id(retry="always")

    def _boot_id(self, retry: str = "always") -> str:
        """boot_id with a choice about when the retry is worth its wall time.

        `retry="stall-only"` retries only when the first attempt looked like
        the FROZEN/PAM stall it exists for -- that is, it spent most of its
        timeout waiting. A connection that is refused or has no route comes
        back in milliseconds, and asking a second time buys nothing but two
        more seconds of a user staring at a prompt.

        The distinction is measured, not parsed: ssh's error text varies by
        version and by what went wrong, and elapsed time is exactly the signal
        that separates "answering slowly" from "not there".
        """
        timeout = 12
        for attempt in range(2):
            began = time.monotonic()
            out = self.run("cat /proc/sys/kernel/random/boot_id", timeout=timeout)
            if out:
                return out
            if retry == "stall-only" and attempt == 0:
                if time.monotonic() - began < timeout * 0.5:
                    return ""       # a fast no is a real no
        return ""

    def state(self, max_age: float = 0.0) -> str:
        """BOOTED | INITRAMFS | FROZEN | FASTBOOT | ABSENT.

        The device lock says WHO is using the device, never WHAT it is doing.
        This is the probe that answers the second question.

        Order is forced by the hardware: a device in the bootloader has no USB
        network at all, so fastboot is authoritative; ssh distinguishes BOOTED;
        ping alone distinguishes FROZEN (kernel alive, userspace gone) from
        ABSENT (needs a human).

        INITRAMFS sits between BOOTED and FROZEN: the boot stopped in the pmOS
        initramfs debug shell, which is a busybox telnetd nobody has to guess
        at -- tools/tsh.py talks to it, and it will hand over dmesg and the
        initramfs log that say WHY the root did not mount. Reporting that as
        FROZEN is true but useless: FROZEN reads as "needs a human with a
        cable", and it cost a session probing ports by hand to discover the
        device was sitting there, fully interrogable, all along.

        The three probes now run CONCURRENTLY and the verdict is resolved by
        that same precedence rather than by which answered first. Serially they
        cost 6.3s against an absent device -- and `porthole brief`, the one
        command AGENTS.md tells every agent to run first, paid it every time.
        Ordering was never about the probes interfering; it was about which
        answer wins, and that is preserved exactly.

        `max_age` lets a DISPLAY caller reuse a recent verdict. It defaults to
        0, so anything that is about to act on the device still probes fresh:
        a cached "BOOTED" handed to something that then flashes is precisely
        the kind of stale reading `brain/laws/` exists to forbid.
        """
        forced = self.cfg.get("TK_DEVICE_STATE") or self.cfg.get("PORTHOLE_DEVICE_STATE")
        if forced:
            return forced

        if max_age > 0:
            cached = self._cached_state(max_age)
            if cached:
                return cached

        import threading
        results = {}

        def probe(name, fn):
            try:
                results[name] = fn()
            except Exception:  # noqa: BLE001 -- one probe failing is a "no"
                results[name] = None

        threads = [threading.Thread(target=probe, args=a, daemon=True)
                   for a in (("fastboot", self.in_fastboot),
                             # A display verdict does not need the stall
                             # retry unless the device is actually stalling.
                             ("boot_id",
                              lambda: self._boot_id(retry="stall-only")),
                             ("initramfs", self._in_initramfs),
                             ("ping", self._pings))]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)

        if results.get("fastboot"):
            verdict = "FASTBOOT"
        elif results.get("boot_id"):
            verdict = "BOOTED"
        elif results.get("initramfs"):
            verdict = "INITRAMFS"
        elif results.get("ping"):
            verdict = "FROZEN"
        else:
            verdict = "ABSENT"
        self._remember_state(verdict)
        return verdict

    def _state_cache(self) -> pathlib.Path:
        import hashlib
        base = os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache")
        tag = hashlib.sha256(str(self.host).encode()).hexdigest()[:12]
        return pathlib.Path(base) / "porthole" / f"state-{tag}.json"

    def _cached_state(self, max_age: float):
        """A recent verdict, or None. Never used by a caller that will act."""
        try:
            blob = json.loads(self._state_cache().read_text())
            if time.time() - blob["at"] <= max_age:
                return blob["state"]
        except Exception:  # noqa: BLE001 -- a bad cache is not an error
            pass
        return None

    def cached_state(self, max_age: float):
        """`(state, age_seconds)` from the cache, or None. NEVER probes.

        A file read, so it is safe everywhere a probe is not: offline, with no
        device attached, inside `brief --no-device`, and inside a milestone
        probe that must not hang on a dead phone.

        The age is returned rather than swallowed because that is the whole
        point. `porthole next` reported "device state not probed" beside a
        `brief` that had just printed BOOTED, and told the reader to run the
        command that had already answered. A reading with its age attached is
        a fact; the same reading without it is a claim.
        """
        try:
            blob = json.loads(self._state_cache().read_text())
            age = time.time() - blob["at"]
        except Exception:  # noqa: BLE001 -- a bad cache is not an error
            return None
        if age < 0 or age > max_age:
            return None
        state = blob.get("state")
        return (state, age) if state else None

    def _remember_state(self, verdict: str) -> None:
        try:
            path = self._state_cache()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"state": verdict, "at": time.time(),
                                        "host": self.host}))
        except OSError:
            pass

    def _in_initramfs(self) -> bool:
        """Is the pmOS initramfs debug shell answering?

        It is a busybox telnetd on port 23. A plain TCP connect is enough and
        is the only probe that separates "stopped in the initramfs" from
        "userspace died" -- both ping, neither answers ssh.

        Bounded, because this runs against possibly-absent hardware: a closed
        port on a live host RSTs instantly, and an absent host must not hold
        the verdict open. Any failure is a "no".
        """
        import socket
        port = int(self.cfg.get("PORTHOLE_INITRAMFS_PORT") or 23)
        try:
            with socket.create_connection((self.host, port), timeout=2):
                return True
        except OSError:
            return False

    def _pings(self) -> bool:
        """Does the device answer ICMP?

        Guarded because a minimal host may not have `ping` at all -- a slim
        container image does not -- and an unguarded FileNotFoundError here
        took down `porthole doctor` entirely, which is the one command someone
        on a bare host runs first.

        Without ping we cannot separate FROZEN from ABSENT, so we return the
        more conservative answer: ABSENT means "needs a human", and claiming a
        device is merely FROZEN when we do not know invites an automated
        recovery that cannot work.
        """
        try:
            return subprocess.run(["ping", "-c1", "-W2", self.host],
                                  capture_output=True,
                                  timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    # -- actions --

    def request_reboot(self, target: str = "") -> None:
        """Ask the device to reboot. Detached remotely so sshd going away
        mid-command does not block us; a non-zero exit here is normal."""
        self.mux_reset()
        force = legacy(self.cfg, "TK_FORCE", "PORTHOLE_FORCE", "0") == "1"
        if force and not target:
            cmd = "sudo -n sync; (sudo -n reboot -f >/dev/null 2>&1 &); exit 0"
        else:
            cmd = (f"sudo -n sync; (sudo -n reboot {target} >/dev/null 2>&1 &);"
                   " exit 0")
        self.run_full(cmd, timeout=12)
        self.mux_reset()

    def request_bootloader(self) -> None:
        """Reboot into the bootloader the way `adb reboot bootloader` does.

        `reboot bootloader` does NOT do this where /usr/sbin/reboot is
        busybox: its applet is `reboot [-d DELAY] [-nf]` and takes no mode
        argument, so the word "bootloader" is silently discarded and you get a
        plain reboot. Every apparent success is the A/B retry counter running
        out on its own.

        The kernel side is there: a PMIC pon node with mode-bootloader means
        the reboot-mode framework writes the mode to a spare register the
        bootloader reads. It just needs reboot(2) with LINUX_REBOOT_CMD_RESTART2
        rather than the plain RB_AUTOBOOT busybox issues, so we make the
        syscall ourselves via python3 on the device.

          142        = __NR_reboot on arm64 (generic syscall table)
          0xfee1dead = LINUX_REBOOT_MAGIC1
          0x28121969 = LINUX_REBOOT_MAGIC2
          0xa1b2c3d4 = LINUX_REBOOT_CMD_RESTART2 (the one carrying a string)

        Does NOT burn a boot retry: the bootloader stops on purpose rather
        than because the counter hit 0.
        """
        self.mux_reset()
        py = (
            "import ctypes;l=ctypes.CDLL(None);s=l.syscall;"
            "s.restype=ctypes.c_long;"
            "s.argtypes=[ctypes.c_long,ctypes.c_uint,ctypes.c_uint,"
            "ctypes.c_uint,ctypes.c_char_p];"
            's(142,0xfee1dead,0x28121969,0xa1b2c3d4,b"bootloader")'
        )
        # Foreground, not backgrounded. Detaching it races: ssh tears the
        # session down before the child reaches the syscall often enough that
        # the request is simply lost. Let the connection die mid-call -- that
        # death IS the success signal.
        self.run_full(f"sudo -n sync; sudo -n python3 -c {shlex.quote(py)}",
                      timeout=25)
        self.mux_reset()

    def rearm_and_boot(self) -> bool:
        """Re-arm the good slot and leave the bootloader.

        Standard recovery for the every-Nth-boot drop-to-bootloader: nothing
        in pmOS reports a successful boot, so the bootloader scores EVERY boot
        as failed and decrements the retry counter; at 0 it keeps the device.
        set_active clears unbootable AND resets the counter.
        """
        self.mux_reset()
        slot = self.cfg.get("PORTHOLE_ACTIVE_SLOT", "")
        forbidden = self.cfg.get("PORTHOLE_SLOT_FORBIDDEN", "")
        if not slot:
            return self._fastboot("reboot")
        if slot == forbidden:
            raise RuntimeError(
                f"refusing to set_active {slot}: the profile marks it "
                f"PORTHOLE_SLOT_FORBIDDEN (no known-good image)")
        return self._fastboot("set_active", slot) and self._fastboot("reboot")

    def _fastboot(self, *args) -> bool:
        """Run fastboot, surviving its absence. A host with no android-tools
        should get a clean False rather than a traceback."""
        try:
            return subprocess.run([self.fastboot, *args], capture_output=True,
                                  timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    # -- waiting --

    def wait_ssh(self, old_boot_id: str, deadline: float,
                 reissue=None, reissue_after: float = 25) -> str:
        """Poll until the device is up on a NEW boot_id. "" on deadline.

        Never sleeps a fixed interval and hopes. Auto-recovers if the device
        lands in the bootloader instead. If `reissue` is given it is called
        when the OLD boot_id is still answering after `reissue_after` seconds
        -- i.e. the reboot request was swallowed and never took effect.
        """
        recovered = 0
        last_request = time.monotonic()
        while time.monotonic() < deadline:
            current = self.boot_id()
            if current:
                if current != old_boot_id:
                    if recovered:
                        print(f">> (recovered from the bootloader "
                              f"{recovered} time(s))", file=sys.stderr)
                    return current
                if reissue and time.monotonic() - last_request >= reissue_after:
                    print(">> reboot request looks swallowed, re-issuing",
                          file=sys.stderr)
                    reissue()
                    last_request = time.monotonic()
            elif self.in_fastboot():
                recovered += 1
                print(f">> landed in the bootloader -- re-arming "
                      f"(recovery #{recovered})", file=sys.stderr)
                if not self.rearm_and_boot():
                    print(">> WARNING: fastboot re-arm failed", file=sys.stderr)
                last_request = time.monotonic()
            time.sleep(self.poll)
        return ""

    def wait_fastboot(self, deadline: float) -> bool:
        while time.monotonic() < deadline:
            if self.in_fastboot():
                return True
            time.sleep(self.poll)
        return False


if __name__ == "__main__":
    cfg = load_config()
    for key in sorted(cfg):
        print(f"{key}={cfg[key]}\t# {cfg.source(key)}")
