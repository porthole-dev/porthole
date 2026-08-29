# SPDX-License-Identifier: MIT
"""`porthole doctor` -- will any of this work, and if not, what do I do?

This is the verb that makes porthole usable on a host that has nothing
installed. Its contract: every failure names a fix, and the fix is a command
you can paste. A check that reports a problem without a remedy is worse than
no check, because it converts "the tools do nothing" into "the tools do
nothing and something red happened".
"""
from __future__ import annotations

import os
import pathlib
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time

from porthole_cli import EX_FAIL, EX_OK

# Package names per distro family, so the hint is copy-pasteable rather than
# "install fastboot somehow". Keys are matched against /etc/os-release ID and
# ID_LIKE, so derivatives (Mint, Pop, Manjaro, postmarketOS itself) resolve to
# their parent.
PACKAGES = {
    "fastboot": {
        "debian": "sudo apt install android-sdk-platform-tools",
        "arch": "sudo pacman -S android-tools",
        "fedora": "sudo dnf install android-tools",
        # An ostree host has no dnf, and rpm-ostree needs a reboot. The
        # unpacked SDK avoids both and is what the reference host uses.
        "fedora-atomic": ("unpack Google's platform-tools into ~/.local/bin"
                          "    # or: rpm-ostree install android-tools "
                          "(needs a reboot)"),
        "alpine": "sudo apk add android-tools",
        "suse": "sudo zypper install android-tools",
        "macos": "brew install android-platform-tools",
    },
    "adb": {
        "debian": "sudo apt install android-sdk-platform-tools",
        "arch": "sudo pacman -S android-tools",
        "fedora": "sudo dnf install android-tools",
        # An ostree host has no dnf, and rpm-ostree needs a reboot. The
        # unpacked SDK avoids both and is what the reference host uses.
        "fedora-atomic": ("unpack Google's platform-tools into ~/.local/bin"
                          "    # or: rpm-ostree install android-tools "
                          "(needs a reboot)"),
        "alpine": "sudo apk add android-tools",
        "suse": "sudo zypper install android-tools",
        "macos": "brew install android-platform-tools",
    },
    "ssh": {
        "debian": "sudo apt install openssh-client",
        "arch": "sudo pacman -S openssh",
        "fedora": "sudo dnf install openssh-clients",
        "alpine": "sudo apk add openssh-client",
        "suse": "sudo zypper install openssh-clients",
        "macos": "(preinstalled)",
    },
    # NOT `pipx install pmbootstrap`. PyPI's newest is 2.1.0 -- the 3.x series
    # is not published there at all -- so pip would install a major version
    # behind what this toolbox targets. Verified 2026-08-29.
    "pmbootstrap": {
        "alpine": "sudo apk add pmbootstrap",
        "*": ("porthole sandbox up    # the workspace image ships it\n"
              "          on the host instead: your distro's pmbootstrap "
              "package, or a\n"
              "          git clone of pmbootstrap with "
              "PORTHOLE_PMBOOTSTRAP_SRC set to it"),
    },
    "podman": {
        "debian": "sudo apt install podman",
        "arch": "sudo pacman -S podman",
        "fedora": "sudo dnf install podman",
        "fedora-atomic": "(preinstalled on atomic Fedora)",
        "alpine": "sudo apk add podman",
        "suse": "sudo zypper install podman",
        "macos": "brew install podman && podman machine init",
    },
    "textual": {
        "debian": "sudo apt install python3-pip && pip install --user 'textual>=8,<9'",
        "arch": "sudo pacman -S python-pip && pip install --user 'textual>=8,<9'",
        "fedora": "sudo dnf install python3-pip && pip install --user 'textual>=8,<9'",
        "alpine": "sudo apk add py3-pip && pip install --user 'textual>=8,<9'",
        "suse": "sudo zypper install python3-pip && pip install --user 'textual>=8,<9'",
        "macos": "pip3 install 'textual>=8,<9'",
        "*": "pip install --user 'textual>=8,<9'",
    },
    "shellcheck": {
        "debian": "sudo apt install shellcheck",
        "arch": "sudo pacman -S shellcheck",
        "fedora": "sudo dnf install ShellCheck",
        "alpine": "sudo apk add shellcheck",
        "macos": "brew install shellcheck",
    },
}


def is_atomic() -> bool:
    """Is this an image-based (ostree/bootc) host?

    It matters because the package-manager hint is WRONG on one: Silverblue
    reports ID=fedora and has no `dnf` at all, so `porthole doctor` was
    printing `sudo dnf install android-tools` on the very machine this toolbox
    is developed on. `/run/ostree-booted` is what ostree itself puts there.
    """
    return (pathlib.Path("/run/ostree-booted").exists()
            or pathlib.Path("/sysroot/ostree").is_dir())


def distro_family() -> str:
    """Best-effort distro family for install hints. 'unknown' is fine."""
    if sys.platform == "darwin":
        return "macos"
    try:
        text = pathlib.Path("/etc/os-release").read_text()
    except OSError:
        return "unknown"
    fields = dict(re.findall(r'^(\w+)=(.*)$', text, re.M))
    ids = [fields.get("ID", "").strip('"')]
    ids += fields.get("ID_LIKE", "").strip('"').split()
    for ident in ids:
        if ident in ("debian", "ubuntu"):
            return "debian"
        if ident in ("arch", "archlinux", "manjaro"):
            return "arch"
        if ident in ("fedora", "rhel", "centos"):
            return "fedora-atomic" if is_atomic() else "fedora"
        if ident in ("alpine", "postmarketos"):
            return "alpine"
        if ident in ("suse", "opensuse", "opensuse-leap", "opensuse-tumbleweed"):
            return "suse"
    return "unknown"


def install_hint(tool: str, family: str) -> str:
    """The fix line for a tool, most specific entry first.

    A `-atomic` family falls back to its base rather than to `*`: most tools
    install the same way on Silverblue as on Fedora, and only the ones that
    genuinely differ need their own entry.
    """
    table = PACKAGES.get(tool, {})
    base = family.split("-", 1)[0]
    return (table.get(family) or table.get(base) or table.get("*")
            or f"install {tool} with your package manager")


class Checks:
    """Collects verdicts.

    A failing check with no fix raises: the contract is enforced in code, not
    left to reviewer diligence.
    """

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, name, status, detail="", fix="", doc=""):
        if status == "fail" and not fix:
            raise AssertionError(f"failing check {name!r} offers no fix")
        self.rows.append({"name": name, "status": status, "detail": detail,
                          "fix": fix, "doc": doc})

    def worst(self) -> str:
        for level in ("fail", "warn"):
            if any(r["status"] == level for r in self.rows):
                return level
        return "ok"

    def counts(self) -> dict:
        out = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
        for row in self.rows:
            out[row["status"]] += 1
        return out


def _resolve(cfg, key, default):
    value = cfg.get(key) or default
    return shutil.which(value) or (value if os.path.isfile(value) else None)


def check_host(ch: Checks, cfg, family: str) -> None:
    ch.add("host: python", "ok" if sys.version_info >= (3, 8) else "fail",
           f"{platform.python_version()} at {sys.executable}",
           "" if sys.version_info >= (3, 8) else
           "porthole needs python 3.8+; install a newer python3")

    for tool, key, required, why in (
        ("ssh", None, True, "every device command goes over ssh"),
        ("fastboot", "FASTBOOT", True, "the only reliable way to reach the bootloader"),
        ("adb", "ADB", False, "only for talking to a stock or recovery system"),
        ("pmbootstrap", None, False, "needed to build and flash, not to probe"),
        ("shellcheck", None, False, "only for `make lint` when contributing"),
    ):
        found = _resolve(cfg, key, tool) if key else shutil.which(tool)
        if found:
            ch.add(f"host: {tool}", "ok", found)
        elif required:
            ch.add(f"host: {tool}", "fail", f"not found -- {why}",
                   install_hint(tool, family))
        else:
            ch.add(f"host: {tool}", "warn", f"not found -- {why}",
                   doc=install_hint(tool, family))

    _check_envkernel(ch, cfg)

    # flock backs the device mutex. Without it parallel workers corrupt each
    # other's sessions, which is a subtle failure rather than a loud one.
    if shutil.which("flock"):
        ch.add("host: flock", "ok", shutil.which("flock"))
    else:
        ch.add("host: flock", "warn",
               "not found -- the device mutex cannot serialise parallel workers",
               doc="part of util-linux; on macOS: brew install flock")


def _envkernel_candidates(cfg):
    """The same search order tools/ph-build.sh uses, in the same order.

    Duplicated deliberately rather than shelled out to: doctor must work when
    the build path is broken, and that is exactly when it is asked.
    """
    home = pathlib.Path.home()
    src = cfg.get("PORTHOLE_PMBOOTSTRAP_SRC", "")
    for label, cand in (
        ("PORTHOLE_ENVKERNEL", cfg.get("PORTHOLE_ENVKERNEL", "")),
        ("PORTHOLE_PMBOOTSTRAP_SRC", f"{src}/helpers/envkernel.sh" if src else ""),
        ("the pmbootstrap data dir", home / ".local/share/pmbootstrap/helpers/envkernel.sh"),
        ("the system install", "/usr/share/pmbootstrap/helpers/envkernel.sh"),
    ):
        if cand and pathlib.Path(cand).is_file():
            return label, str(cand)

    # pipx and pip installs put it beside the pmb package.
    try:
        import importlib.util
        spec = importlib.util.find_spec("pmb")
        if spec and spec.origin:
            base = pathlib.Path(spec.origin).parent.parent
            for cand in (base / "helpers/envkernel.sh",
                         base / "pmb/helpers/envkernel.sh"):
                if cand.is_file():
                    return "the installed pmb package", str(cand)
    except Exception:  # noqa: BLE001 -- a broken pmb must not break doctor
        pass
    return None, None


def _check_envkernel(ch: Checks, cfg) -> None:
    """envkernel.sh is what `porthole build` compiles through.

    Reported because on 2026-08-26 `build kernel` died at step one with "cannot
    find envkernel.sh" on a completely working setup -- a pmbootstrap checkout
    was sitting in the developer's home directory, simply unconfigured. Doctor
    names every other fix; not naming this one sent a session to read build
    internals to learn it needed one variable set.
    """
    label, found = _envkernel_candidates(cfg)
    if found:
        ch.add("host: envkernel", "ok", f"{found}  (via {label})")
        return

    # Not configured -- so look for a checkout before saying it is missing.
    # "It is right there and you did not tell me" is the whole complaint.
    seen = []
    home = pathlib.Path.home()
    for pattern in ("*/pmbootstrap/helpers/envkernel.sh",
                    "*/*/pmbootstrap/helpers/envkernel.sh"):
        try:
            seen.extend(sorted(home.glob(pattern))[:3])
        except OSError:
            pass
    if seen:
        checkout = seen[0].parent.parent
        ch.add("host: envkernel", "warn",
               f"not configured -- but a checkout is present at {checkout}",
               f"export PORTHOLE_PMBOOTSTRAP_SRC={checkout}"
               "    # or add it to ~/.config/porthole/config.env")
        return
    ch.add("host: envkernel", "warn",
           "not found -- `porthole build` cannot compile without it",
           "clone pmbootstrap and set PORTHOLE_PMBOOTSTRAP_SRC to it, "
           "or set PORTHOLE_ENVKERNEL to helpers/envkernel.sh directly")


def check_drift(ch: Checks, cfg) -> None:
    """The environment quietly outranking a committed layer.

    `porthole config` has always been able to show this; the point of a check
    is that nobody runs `porthole config` before a build. Twice now a stale
    export has built the wrong kernel.
    """
    import porthole

    drifts = porthole.drift(cfg)
    if not drifts:
        ch.add("config: drift", "ok", "the environment agrees with the profile")
        return
    for d in drifts:
        detail = (f"{d['key']}: environment says {d['winning']}, "
                  f"{d['committed_layer']} says {d['committed']}")
        if d["blocking"]:
            ch.add("config: drift", "fail", detail,
                   f"unset {d['key']}    # or `porthole build "
                   f"--allow-env-override` if you mean it")
        else:
            ch.add("config: drift", "warn", detail,
                   doc=f"unset {d['key']}, or `porthole use` to update the "
                       f"stored value")


def check_profile(ch: Checks, cfg, root: pathlib.Path) -> None:
    device = cfg.get("PORTHOLE_DEVICE", "")
    if not device:
        ch.add("profile", "fail", "no device selected",
               "porthole init <codename>",
               "`porthole devices` lists what exists")
        return
    ch.add("profile", "ok", device)

    required = ("PORTHOLE_SOC", "PORTHOLE_CODENAME", "PORTHOLE_ARCH")
    blank = [k for k in required if not cfg.get(k)]
    if blank:
        ch.add("profile: required keys", "fail", f"unset: {', '.join(blank)}",
               f"fill them in profiles/{device}/device.env")
    else:
        ch.add("profile: required keys", "ok", ", ".join(required))

    if cfg.get("PORTHOLE_CODENAME") and cfg["PORTHOLE_CODENAME"] != device:
        ch.add("profile: codename", "warn",
               f"PORTHOLE_CODENAME={cfg['PORTHOLE_CODENAME']!r} but the "
               f"directory is {device!r} -- one of them is wrong")

    if cfg.get("PORTHOLE_HAS_AB_SLOTS") == "1":
        if not cfg.get("PORTHOLE_ACTIVE_SLOT"):
            ch.add("profile: A/B slots", "warn",
                   "HAS_AB_SLOTS=1 but ACTIVE_SLOT is unset; recovery from the "
                   "bootloader cannot re-arm a slot")
        elif not cfg.get("PORTHOLE_SLOT_FORBIDDEN"):
            ch.add("profile: A/B slots", "warn",
                   "no SLOT_FORBIDDEN set -- porthole cannot stop you arming a "
                   "slot with no good image",
                   doc="leave empty only if both slots are genuinely safe")
        else:
            ch.add("profile: A/B slots", "ok",
                   f"active={cfg['PORTHOLE_ACTIVE_SLOT']} "
                   f"forbidden={cfg['PORTHOLE_SLOT_FORBIDDEN']}")

    # Measured, not inherited. Until it exists every boot verdict is a guess.
    if not cfg.get("PORTHOLE_REBOOT_BUDGET_S"):
        ch.add("profile: timing", "warn",
               "PORTHOLE_REBOOT_BUDGET_S unset -- measure cold boot to first ssh",
               doc="brain/traps/wait-long-enough-before-calling-a-boot-failed.md")


def check_identity(ch: Checks, cfg) -> None:
    # Judge the resolved target, not one input to it: somebody who exports
    # PHONE has a good identity even with PORTHOLE_USER unset.
    if cfg.source("PHONE") == "derived" and cfg.source("PORTHOLE_USER") == "default":
        ch.add("identity", "warn",
               f"nothing set a username; ssh will use {cfg['PORTHOLE_USER']!r}",
               doc="porthole init")
    else:
        ch.add("identity", "ok", f"{cfg['PHONE']} (via {cfg.source('PHONE')})")


def check_device(ch: Checks, ctx, cfg) -> None:
    dev = ctx.device()
    start = time.monotonic()
    state = dev.state()
    elapsed = (time.monotonic() - start) * 1000
    detail = f"{state} ({elapsed:.0f}ms)"

    if state == "BOOTED":
        ch.add("device: state", "ok", detail)
        rc, _, _ = dev.run_full("sudo -n true", timeout=8)
        if rc == 0:
            ch.add("device: sudo -n", "ok", "passwordless sudo works")
        else:
            user = cfg["PHONE"].partition("@")[0]
            ch.add("device: sudo -n", "fail",
                   "sudo -n fails; nearly every tool calls it, and -n does not "
                   "prompt -- it fails. They will all silently do nothing.",
                   f"on the DEVICE, once per install:\n"
                   f"          echo '{user} ALL=(ALL) NOPASSWD: ALL' | "
                   f"sudo tee /etc/sudoers.d/99-porthole-dev\n"
                   f"          sudo chmod 0440 /etc/sudoers.d/99-porthole-dev",
                   "must NOT ship in a device package others install")
        _, ver, _ = dev.run_full("cat /proc/version", timeout=8)
        if ver:
            ch.add("device: kernel", "ok", " ".join(ver.split()[:3]))
        _, py, _ = dev.run_full("command -v python3", timeout=8)
        if py:
            ch.add("device: python3", "ok", py.strip())
        elif cfg.get("PORTHOLE_REBOOT_MODE_VIA_SYSCALL") == "1":
            ch.add("device: python3", "warn",
                   "absent, so reaching the bootloader falls back to burning "
                   "boot retries (minutes instead of ~9s)",
                   doc="apk add python3 on the device")
    elif state == "FASTBOOT":
        ch.add("device: state", "warn",
               f"{detail} -- in the bootloader; nothing over ssh will work",
               doc="tools/tk-reboot.sh, or fastboot set_active + reboot")
    elif state == "INITRAMFS":
        ch.add("device: state", "warn",
               f"{detail} -- stopped in the pmOS initramfs debug shell",
               doc="tools/tsh.py 'dmesg | grep pmOS-rd'   # it will say why root did not mount")
    elif state == "FROZEN":
        ch.add("device: state", "warn",
               f"{detail} -- kernel alive, userspace gone",
               doc="brain/traps/frozen-is-not-hung.md; tools/tk-recover.sh")
    else:
        ch.add("device: state", "warn",
               f"{detail} -- not reachable. Suspended, powered off, or "
               f"unplugged. Not a failure if you are working offline.")


HEADER_FIELDS = ("scope", "needs", "env", "exits")


def _declared_depends(cfg, root: pathlib.Path):
    """What the device package says the device needs.

    Parsed from the APKBUILD rather than asked of apk, because the question is
    "does the running device match what we ship", and asking the device both
    halves of that answers nothing.
    """
    pkg = cfg.get("PORTHOLE_DEVICE_PKG", "")
    workdir = cfg.get("PORTHOLE_WORKDIR", "")
    if not pkg or not workdir:
        return None, None
    for candidate in sorted(pathlib.Path(workdir).glob(
            f"pmaports/device/*/{pkg}/APKBUILD")):
        text = candidate.read_text(errors="replace")
        # Anchored to line start: a bare "depends=" search matched the word
        # inside a prose comment further up the file and parsed the sentence
        # as a package list.
        m = re.search(r'^depends=(["\'])(.*?)\1', text, re.M | re.S)
        if not m:
            continue
        deps = []
        for line in m.group(2).splitlines():
            line = line.split("#", 1)[0].strip()      # the APKBUILD comments
            for token in line.split():
                # A shell variable in a depends list cannot be resolved here,
                # and guessing is worse than skipping it.
                if token and not token.startswith("$"):
                    deps.append(token)
        return sorted(set(deps)), candidate
    return None, None


def check_device_packages(ch: Checks, ctx, cfg) -> None:
    """Declared dependencies versus what is actually installed.

    A rootfs reinstall silently replaced the pipewire audio backend with
    pulseaudio, whose capture reads silence on this board. The microphone was
    dead, a call carried no uplink, and a day went into debugging a driver that
    was not at fault. Nothing anywhere would have reported it: the working
    configuration existed only as manual state on a filesystem that got
    replaced.

    The device package now names what it needs. This is the half that checks the
    device agrees.
    """
    deps, apkbuild = _declared_depends(cfg, ctx.root)
    if not deps:
        ch.add("device: packages", "skip",
               "no device APKBUILD found (needs PORTHOLE_WORKDIR/pmaports)")
        return
    # Ask apk whether each dependency is SATISFIED, one round trip, and let it
    # answer -- rather than comparing the names it prints.
    #
    # Comparing names was wrong: `apk info -e mkbootimg` prints
    # `mkbootimg-osm0sis`, because the dependency is met by a provider under a
    # different name. That read as "declared but not installed" about a package
    # that was correctly there, which is the same class of wrong answer this
    # check exists to catch. apk knows about provides; a string compare does not.
    script = ("for p in " + " ".join(shlex.quote(d) for d in deps) +
              "; do apk info -e \"$p\" >/dev/null 2>&1 || echo \"$p\"; done")
    rc, out, err = ctx.device().run_full(script, timeout=25)
    if rc != 0:
        ch.add("device: packages", "warn",
               "could not query apk on the device",
               doc=(err or "").strip()[:120] or str(apkbuild))
        return
    missing = [d for d in out.split() if d in deps]
    if not missing:
        ch.add("device: packages", "ok",
               f"all {len(deps)} declared dependencies are satisfied")
        return
    ch.add("device: packages", "fail",
           f"{len(missing)} declared dependenc(ies) NOT installed: "
           + ", ".join(missing),
           "the running rootfs does not match what the device package "
           "declares.\n"
           "          Reinstall the device package, or reflash:\n"
           f"          pmbootstrap chroot -r -- apk add {' '.join(missing)}",
           f"declared in {apkbuild}")


def check_tools(ch: Checks, root: pathlib.Path) -> None:
    """Every tool must be self-describing in its first 30 lines."""
    tools = [p for p in sorted((root / "tools").iterdir())
             if p.is_file() and not p.is_symlink()
             and p.suffix in (".sh", ".py") or (p.is_file() and os.access(p, os.X_OK)
                                                and not p.is_symlink())]
    tools = [p for p in tools if p.name not in ("__pycache__",)]
    missing = {}
    for tool in tools:
        try:
            head = "".join(tool.read_text(errors="replace").splitlines(True)[:30])
        except OSError:
            continue
        gaps = [f for f in HEADER_FIELDS if f"{f}:" not in head]
        if gaps:
            missing[tool.name] = gaps
    if not tools:
        ch.add("tools: headers", "skip", "no tools found")
    elif missing:
        sample = ", ".join(list(missing)[:5])
        ch.add("tools: headers", "fail",
               f"{len(missing)} of {len(tools)} incomplete: {sample}"
               + (" ..." if len(missing) > 5 else ""),
               "add the standard header block; see docs/CONTRIBUTING.md",
               "`porthole tools lint` lists every gap")
    else:
        ch.add("tools: headers", "ok", f"all {len(tools)} self-describing")


def bench(ch: Checks, ctx) -> None:
    """Measure the budgets in docs/PERFORMANCE.md rather than claiming them."""
    dev = ctx.device()
    if dev.state() != "BOOTED":
        ch.add("bench", "skip", "device is not booted")
        return

    def timed(label, fn, budget_ms):
        start = time.monotonic()
        fn()
        ms = (time.monotonic() - start) * 1000
        ch.add(f"bench: {label}", "ok" if ms <= budget_ms else "warn",
               f"{ms:.0f}ms (budget {budget_ms}ms)",
               doc="" if ms <= budget_ms else "docs/PERFORMANCE.md")

    dev.run("true")                                    # warm the master
    timed("ssh round trip (warm)", lambda: dev.run("true"), 30)
    timed("fastboot devices", dev.in_fastboot, 250)
    timed("device state", dev.state, 100)


def _check_pmb_sudo(ch: Checks, ctx, state: dict) -> None:
    """PMB_SUDO set at all is now a leftover, and a dangerous one.

    The privilege broker it used to name is gone -- the workspace needs no
    sudoers entry, so a weaker path nobody needed was one more thing that could
    be wrong. But an export survives in a shell long after the file does, and
    pmbootstrap invokes PMB_SUDO directly, prefixing nothing: a stale one kills
    a build with **exit 78 deep inside pmbootstrap**, with nothing anywhere
    saying the words "PMB_SUDO". Reported from a real session, where the
    workaround reached for was `PMB_SUDO=sudo` -- the blanket credential cache
    this whole subsystem exists to retire.

    So: set is a failure, and the fix is spelled out.
    """
    value = (ctx.cfg.get("PMB_SUDO") or os.environ.get("PMB_SUDO") or "").strip()
    if not value:
        ch.add("host: PMB_SUDO", "ok", "unset -- nothing here uses it")
        return
    ch.add("host: PMB_SUDO", "fail",
           f"set to {value} -- a leftover; the privilege broker it named is gone",
           fix="unset PMB_SUDO"
               "    # a build otherwise dies with exit 78 deep inside "
               "pmbootstrap, naming nothing")


def check_workspace(ch: Checks, ctx, family: str) -> None:
    """podman and the build workspace.

    podman is the ONE thing that still needs a package manager. Everything else
    the build needs -- pmbootstrap, the toolchain, fuse2fs, android-tools --
    lives in the image, which is why this is the only host prerequisite worth
    failing on.
    """
    import porthole_cmd_sandbox as sandbox

    state = sandbox._container_state(ctx.root)
    if not state["podman"]:
        ch.add("host: podman", "fail",
               "not found -- builds run in a rootless container",
               install_hint("podman", family))
        return
    ch.add("host: podman", "ok", state["podman"])
    if state["image_built"]:
        ch.add("workspace: image", "ok", state["image"])
    else:
        ch.add("workspace: image", "warn", "not built",
               doc="porthole sandbox up")
    if state["container_running"]:
        ch.add("workspace: container", "ok", sandbox.CONTAINER)
    else:
        ch.add("workspace: container", "warn", "not running",
               doc="porthole sandbox up")
    # The workspace is rootless: `--userns=keep-id:uid=0,gid=0` maps YOUR uid
    # to root inside and nothing else. So it needs a work dir it owns, which is
    # why it has its own rather than sharing the host's -- one built by the old
    # host-root path reads as `nobody` in there and cannot be written or
    # converted. porthole_cmd_sandbox.SANDBOX_PMB_DEFAULT has the full why.
    #
    # This check exists because the failure is otherwise unrecognisable: it
    # surfaces four commands later as `cp /etc/resolv.conf ...` failing.
    pmb = sandbox._sandbox_pmb(ctx.cfg)
    cfg_file = pmb / sandbox.PMB_CFG_NAME
    if not pmb.exists():
        ch.add("workspace: work dir", "warn",
               f"{pmb} does not exist yet",
               doc="porthole sandbox up    creates and configures it")
    elif pmb.stat().st_uid != os.getuid():
        ch.add("workspace: work dir", "fail",
               f"{pmb} is owned by uid {pmb.stat().st_uid}, not you -- "
               "the workspace cannot write it",
               # NOT plain rm -rf: a populated work dir holds files owned by
               # the chroot's own uids, which live in your subuid range and
               # are not yours outside the userns. `podman unshare` enters it.
               fix=f"podman unshare rm -rf {pmb} && porthole sandbox up",
               doc="a rootless container can only write what your uid owns")
    elif not cfg_file.is_file():
        ch.add("workspace: work dir", "warn",
               f"{pmb} has no {sandbox.PMB_CFG_NAME}",
               doc="porthole sandbox up    writes it")
    else:
        ch.add("workspace: work dir", "ok", str(pmb))

    # binfmt is host-global and needs root once. Named, never automated: it is
    # a person installing software on their own machine, not a privilege the
    # agent holds.
    _check_pmb_sudo(ch, ctx, state)

    binfmt = pathlib.Path("/proc/sys/fs/binfmt_misc/qemu-aarch64")
    if binfmt.exists():
        ch.add("host: binfmt aarch64", "ok", "registered")
    else:
        ch.add("host: binfmt aarch64", "warn",
               "not registered -- cross-arch package builds need it",
               doc="install qemu-user-static (host-global, needs root once)")


def cmd_doctor(args, ctx) -> int:
    family = distro_family()
    ch = Checks()
    cfg = ctx.cfg

    check_host(ch, cfg, family)
    check_workspace(ch, ctx, family)
    check_drift(ch, cfg)
    check_profile(ch, cfg, ctx.root)
    check_identity(ch, cfg)
    if args.no_device:
        ch.add("device: state", "skip", "--no-device")
    else:
        check_device(ch, ctx, cfg)
        check_device_packages(ch, ctx, cfg)
    if args.tools or args.all:
        check_tools(ch, ctx.root)
    if args.bench and not args.no_device:
        bench(ch, ctx)

    payload = {"verdict": ch.worst(), "counts": ch.counts(),
               "distro_family": family, "checks": ch.rows}

    def render():
        mark = {"ok": ("ok", "green"), "warn": ("warn", "yellow"),
                "fail": ("FAIL", "red"), "skip": ("skip", "grey")}
        width = max(len(r["name"]) for r in ch.rows)
        for row in ch.rows:
            label, colour = mark[row["status"]]
            ctx.out(f"  {ctx.out.paint(f'{label:>4}', colour)}  "
                    f"{row['name']:<{width}}  {row['detail']}")
            if row["fix"]:
                ctx.out(ctx.out.paint(f"        fix: {row['fix']}", "cyan"))
            elif row["doc"]:
                ctx.out(ctx.out.paint(f"        see: {row['doc']}", "grey"))
        counts = ch.counts()
        ctx.out.blank()
        summary = (f"{counts['ok']} ok, {counts['warn']} warn, "
                   f"{counts['fail']} fail, {counts['skip']} skipped")
        ctx.out({"ok": ctx.out.paint("everything checks out", "green"),
                 "warn": ctx.out.paint("usable, with warnings", "yellow"),
                 "fail": ctx.out.paint("something needs fixing", "red"),
                 }[ch.worst()] + ctx.out.paint(f"  ({summary})", "grey"))
        if ch.worst() == "fail":
            ctx.out.blank()
            ctx.out("Nothing above is fatal to the repo -- fix the FAIL lines "
                    "and re-run `porthole doctor`.")

    ctx.emit(payload, render)

    if getattr(args, "fix", False):
        return _fix(ch, ctx, args)
    return EX_FAIL if ch.worst() == "fail" else EX_OK


def _fix(ch: Checks, ctx, args) -> int:
    """Run the fixes the checks named -- after showing them, and after asking.

    Every command is printed before anything runs, because this is the verb
    people reach for on a machine they do not know well, and a tool that
    installs software without saying what is a tool you cannot trust twice.

    An agent must NOT get past the prompt: the remaining privileged step needs
    a password, and asking the human is the correct behaviour rather than a
    limitation. --dry-run prints the plan and stops, which is what the distro
    matrix in tests/ci-local.sh exercises.
    """
    todo = [r for r in ch.rows if r["status"] in ("fail", "warn")
            and (r["fix"] or r["doc"])]
    if not todo:
        ctx.out(ctx.out.paint("nothing to fix", "green"))
        return EX_OK

    ctx.out.blank()
    ctx.out.heading("what --fix would run")
    for row in todo:
        ctx.out(f"  {row['name']}")
        ctx.out(ctx.out.paint(f"      {row['fix'] or row['doc']}", "cyan"))
    ctx.out.blank()

    if getattr(args, "dry_run", False):
        ctx.out(ctx.out.paint("--dry-run: nothing was run", "grey"))
        return EX_OK

    if not sys.stdin.isatty():
        ctx.out.warn("not a terminal, so nothing was run. These need a "
                     "password you should type yourself -- run "
                     "`porthole doctor --fix` interactively.")
        return EX_FAIL if ch.worst() == "fail" else EX_OK

    ctx.out("Run these now? Each is printed again as it runs. [y/N] ")
    try:
        if (input().strip().lower() or "n")[0] != "y":
            ctx.out("nothing was run")
            return EX_OK
    except (EOFError, KeyboardInterrupt):
        ctx.out.blank()
        return EX_OK

    failed = 0
    for row in todo:
        cmd = (row["fix"] or row["doc"]).split("#", 1)[0].strip()
        # Multi-line and prose hints are guidance, not commands. Saying so is
        # better than running the first line of a paragraph.
        if not cmd or "\n" in (row["fix"] or row["doc"]) or cmd.startswith("("):
            ctx.out.warn(f"{row['name']}: do this one by hand -- {cmd or 'see above'}")
            continue
        ctx.out(ctx.out.paint(f"  $ {cmd}", "cyan"))
        if subprocess.run(cmd, shell=True).returncode != 0:
            failed += 1
            ctx.out.warn(f"{row['name']}: that command failed")
    ctx.out.blank()
    ctx.out("re-run `porthole doctor` to see where you are")
    return EX_FAIL if failed else EX_OK


SPEC = {
    "verb": "doctor",
    "order": 20,
    "help": "check the host, the profile and the device; name every fix",
    "description": (
        "The first thing to run on a new host, and the first thing to run when\n"
        "'all the tools are broken'. Every failure names a command that fixes\n"
        "it, chosen for your distribution where that matters.\n\n"
        "Exits non-zero only on FAIL. Warnings are things you can work without."),
    "args": [
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
        (["--tools"], {"action": "store_true",
                       "help": "also check every tool is self-describing"}),
        (["--bench"], {"action": "store_true",
                       "help": "also measure the performance budgets"}),
        (["--all"], {"action": "store_true", "help": "every check"}),
        (["--no-device"], {"action": "store_true",
                           "help": "skip anything that touches the device"}),
        (["--fix"], {"action": "store_true",
                     "help": "show the fixes, then offer to run them"}),
        (["--dry-run"], {"action": "store_true",
                         "help": "--fix: print the plan and stop"}),
    ],
    "run": cmd_doctor,
    "examples": [
        "porthole doctor",
        "porthole doctor --no-device        # host only, device unplugged",
        "porthole doctor --all --json       # everything, for an agent",
        "porthole doctor --bench            # measure, do not trust, the budgets",
    ],
}
