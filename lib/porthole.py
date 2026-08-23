#!/usr/bin/env python3
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

import os
import pathlib
import shlex
import subprocess
import sys
import time

__all__ = [
    "Config", "ProfileNotFound", "load_config", "list_profiles",
    "resolve_phone", "resolve_host", "legacy", "ssh_opts", "Device",
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
    "PORTHOLE_WORKDIR": "",
    "PORTHOLE_NO_MUX": "0",
    "PORTHOLE_MUX_PERSIST": "60s",
    # Device facts, defaulted to the conservative answer. A device with no
    # profile is assumed to have no A/B slots, because the failure mode of
    # wrongly assuming slots is flashing a partition that does not exist.
    "PORTHOLE_HAS_AB_SLOTS": "0",
    "PORTHOLE_ACTIVE_SLOT": "",
    "PORTHOLE_SLOT_FORBIDDEN": "",
    "PORTHOLE_BOOT_RETRIES": "",
    "PORTHOLE_USB_LIES_AS_FASTBOOT": "0",
    "PORTHOLE_REBOOT_MODE_VIA_SYSCALL": "0",
    "PORTHOLE_WATCHDOG_MAX_S": "",
}

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

    def set(self, key: str, value: str, layer: str) -> None:
        self[key] = value
        self._sources[key] = layer

    def source(self, key: str) -> str:
        return self._sources.get(key, LAYER_DEFAULT)

    def sources(self) -> dict[str, str]:
        return dict(self._sources)


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
    """Device profiles, sorted. `_template` and dotfiles are not devices."""
    base = pathlib.Path(root or find_root()) / "profiles"
    if not base.is_dir():
        return []
    return sorted(
        p.name for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
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
    cfg.set("PORTHOLE_ROOT", str(root), cfg.source("PORTHOLE_ROOT"))
    cfg.setdefault("PORTHOLE_RUNDIR", str(root / ".run"))
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
        subprocess.run(["ssh", *ssh_opts(self.cfg), "-O", "exit", self.phone],
                       capture_output=True, stdin=subprocess.DEVNULL)

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
        except (subprocess.TimeoutExpired, OSError):
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
        for _ in range(2):
            out = self.run("cat /proc/sys/kernel/random/boot_id", timeout=12)
            if out:
                return out
        return ""

    def state(self) -> str:
        """BOOTED | FROZEN | FASTBOOT | ABSENT.

        The device lock says WHO is using the device, never WHAT it is doing.
        This is the probe that answers the second question.

        Order is forced by the hardware: a device in the bootloader has no USB
        network at all, so fastboot is asked first. ssh distinguishes BOOTED;
        ping alone distinguishes FROZEN (kernel alive, userspace gone) from
        ABSENT (needs a human).
        """
        forced = self.cfg.get("TK_DEVICE_STATE") or self.cfg.get("PORTHOLE_DEVICE_STATE")
        if forced:
            return forced
        if self.in_fastboot():
            return "FASTBOOT"
        if self.boot_id():
            return "BOOTED"
        rc = subprocess.run(["ping", "-c1", "-W2", self.host],
                            capture_output=True).returncode
        return "FROZEN" if rc == 0 else "ABSENT"

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
            return subprocess.run([self.fastboot, "reboot"],
                                  capture_output=True).returncode == 0
        if slot == forbidden:
            raise RuntimeError(
                f"refusing to set_active {slot}: the profile marks it "
                f"PORTHOLE_SLOT_FORBIDDEN (no known-good image)")
        for argv in ([self.fastboot, "set_active", slot],
                     [self.fastboot, "reboot"]):
            if subprocess.run(argv, capture_output=True).returncode != 0:
                return False
        return True

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
