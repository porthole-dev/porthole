# SPDX-License-Identifier: MIT
"""`porthole doctor` -- will any of this work, and if not, what do I do?

This is the verb that makes porthole usable on a host that has nothing
installed. Its contract: every failure names a fix, and the fix is a command
you can paste. A check that reports a problem without a remedy is worse than
no check, because it converts "the tools do nothing" into "the tools do
nothing and something red happened".
"""
from __future__ import annotations

import collections
import errno
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


# (tool, config key, required, why, version flag)
#
# The version flag is per-tool and it matters: `ssh --version` is not a thing.
# ssh rejects it with exit 255 and a usage block, so probing every tool the
# same way reported a perfectly good ssh as FAIL on the first host this ran
# on -- the same false verdict this check exists to remove, pointing the other
# way. A tool is asked the question it answers.
HOST_TOOLS = (
    ("ssh", None, True, "every device command goes over ssh", "-V"),
    ("fastboot", "FASTBOOT", True,
     "the only reliable way to reach the bootloader", "--version"),
    ("adb", "ADB", False,
     "only for talking to a stock or recovery system", "--version"),
    ("pmbootstrap", None, False,
     "needed to build and flash, not to probe", "--version"),
    ("shellcheck", None, False,
     "only for `make lint` when contributing", "--version"),
)


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


def _first_line(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0][:120] if lines else ""


def _resolve(cfg, key, default):
    """Where a host tool is, or None.

    The isfile fallback used to accept any file that EXISTS. shutil.which
    enforces X_OK; this branch did not -- so a config naming a non-executable
    file rendered `ok` on FASTBOOT, a REQUIRED row whose entire purpose is to
    fail when the bootloader is unreachable.
    """
    value = cfg.get(key) or default
    if not value:
        return None
    found = shutil.which(value)
    if found:
        return found
    return value if (os.path.isfile(value)
                     and os.access(value, os.X_OK)) else None


def _runs(path: str, flag: str = "--version") -> str:
    """"" if the tool actually executes, else a one-line reason it did not.

    `flag` is per-tool and not a detail: `ssh --version` is not a thing. ssh
    rejects it with exit 255 and a usage block, so probing every tool the same
    way failed a perfectly good ssh on the first host this ran on. The flag
    each tool actually supports lives in check_host's table, beside the other
    per-tool facts.

    Presence is not the question doctor is asked. A +x script whose shebang
    interpreter no longer exists passes shutil.which and dies at exec with 126
    -- the ordinary pipx failure, a venv whose base python was removed or
    version-bumped, and routine on an rpm-ostree host like the reference one.
    Doctor printed `ok` for such a pmbootstrap for as long as the check
    existed, and the first symptom was a build failing much later for reasons
    that named neither doctor nor the interpreter.

    The failure this catches was found and first fixed by Alessandro Ianne in
    PR #2, which probed it by parsing the shebang and checking the interpreter
    exists. That probe catches strictly less than running the tool -- it passes
    a live interpreter whose venv is broken, which is the more common pipx
    failure -- and its stated reason for avoiding execution ("a pmbootstrap
    without a config file exits non-zero on every invocation") does not hold
    for --version, which argparse answers before any config is read. Both are
    measured in brain/findings/a-shebang-probe-is-a-subset-of-running-the-tool.md,
    which keeps that implementation as the reference it deserves to be. Naming
    the interpreter, in _shebang() below, is its idea and a good one.

    Deliberately NOT porthole_cmd_version's cached tool_version(): doctor must
    work when the build path is broken -- see _envkernel_candidates, which
    duplicates ph-build.sh's search for the same reason -- and a cache is one
    more thing that can be stale exactly when doctor is asked. The cost is
    ~223ms, dominated by pmbootstrap starting a second interpreter (the repo's
    own measurement, in porthole_cmd_version), against device rows that
    already spend 8s apiece.
    """
    try:
        proc = subprocess.run([path, flag], capture_output=True,
                              text=True, timeout=10)
    except OSError as exc:
        # A broken shebang reaches us as ENOENT, because the kernel resolves
        # the INTERPRETER and cannot find it. Reported raw, that is "no such
        # file" about a file the user can plainly see -- the same misleading
        # shape this check exists to remove. A shell prints 126 here and says
        # `bad interpreter`; say the same thing, and name the interpreter.
        if exc.errno == errno.ENOENT and os.path.exists(path):
            return "{} has a bad interpreter{}".format(
                path, _shebang(path)) + " -- it cannot start"
        return "{} does not run: {}".format(path, exc.strerror or exc)
    except subprocess.TimeoutExpired:
        return "{} did not answer {} within 10s".format(path, flag)
    if proc.returncode == 0:
        return ""
    tail = _first_line(proc.stderr) or _first_line(proc.stdout)
    return "{} exited {}".format(path, proc.returncode) + (
        ": " + tail if tail else "")


def _shebang(path: str) -> str:
    """ " (#!/usr/bin/python3.11)" if the file names an interpreter, else "".

    Named because it is the actionable half: "pmbootstrap has a bad
    interpreter" sends you looking at pmbootstrap, and the fix is to the venv
    whose python was removed.
    """
    try:
        with open(path, "rb") as fh:
            first = fh.readline(256).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    return " ({})".format(first) if first.startswith("#!") else ""


def check_host(ch: Checks, cfg, family: str) -> None:
    ch.add("host: python", "ok" if sys.version_info >= (3, 8) else "fail",
           f"{platform.python_version()} at {sys.executable}",
           "" if sys.version_info >= (3, 8) else
           "porthole needs python 3.8+; install a newer python3")

    for tool, key, required, why, flag in HOST_TOOLS:
        found = _resolve(cfg, key, tool) if key else shutil.which(tool)
        if found:
            # Found is not the same as usable, and every row here reports on
            # something a later command will actually invoke. See _runs.
            broken = _runs(found, flag)
            if not broken:
                ch.add(f"host: {tool}", "ok", found)
            elif required:
                ch.add(f"host: {tool}", "fail", broken,
                       install_hint(tool, family))
            else:
                ch.add(f"host: {tool}", "warn", broken,
                       doc=install_hint(tool, family))
        elif required:
            ch.add(f"host: {tool}", "fail", f"not found -- {why}",
                   install_hint(tool, family))
        else:
            ch.add(f"host: {tool}", "warn", f"not found -- {why}",
                   doc=install_hint(tool, family))

    _check_envkernel(ch, cfg)
    _check_pmaports(ch, cfg)
    _check_device_workdir(ch, cfg)
    _check_host_workdir(ch, cfg)
    _check_disk(ch, cfg)

    # flock backs the device mutex. Without it parallel workers corrupt each
    # other's sessions, which is a subtle failure rather than a loud one.
    if shutil.which("flock"):
        ch.add("host: flock", "ok", shutil.which("flock"))
    else:
        ch.add("host: flock", "warn",
               "not found -- the device mutex cannot serialise parallel workers",
               doc="part of util-linux; on macOS: brew install flock")


def _check_pmaports(ch: Checks, cfg) -> None:
    """WHICH pmaports checkout, and which key put it there.

    There are four ways to answer "where is pmaports" -- a per-device key, a
    global key, pmbootstrap's own cache_git, and the legacy cache path -- and
    until now nothing printed the answer. The complaint that produced this row
    was not "it is broken", it was "I cannot tell which variable is effective",
    which is a question a tool should answer rather than a manual.
    """
    import porthole_pmaports as pmap

    found, via = pmap.find_pmaports_with_source(cfg)
    if not found:
        ch.add("host: pmaports", "warn",
               "no checkout found -- device facts, SoC siblings and package "
               "builds all read it",
               "porthole init    adopts an existing one, or clones one")
        return
    # The layer comes from the resolver, never re-derived here: a label
    # computed in parallel with the search is a label that can disagree with
    # the path beside it, which is worse than printing no label at all.
    ch.add("host: pmaports", "ok", f"{found}  (via {via})")


def _check_device_workdir(ch: Checks, cfg) -> None:
    """The DEVICE working repo -- a third directory, and the one that was
    reported nowhere.

    doctor already prints two work dirs, both of them pmbootstrap's. This one
    is the developer's: notes, logs, and the kernel tree if there is one.
    Without it `porthole build`, `verify`, `dts` and six milestones have
    nothing to read, and every one of them reported that as a variable name
    rather than as a directory that does not exist -- with no row here to say
    which of the three "work dir" things was meant.
    """
    device = cfg.get("PORTHOLE_DEVICE", "")
    key = (f"PORTHOLE_WORKDIR_{device.upper().replace('-', '_')}"
           if device else "PORTHOLE_WORKDIR")
    workdir = (cfg.get("PORTHOLE_WORKDIR") or "").strip()
    if not workdir:
        ch.add("host: working repo", "warn",
               f"none for {device or 'this device'} -- `porthole build`, "
               f"`verify` and `dts` need it",
               fix="porthole init    finds or creates one and writes " + key)
        return
    if not pathlib.Path(workdir).is_dir():
        ch.add("host: working repo", "fail",
               f"{key} names {workdir}, which does not exist",
               fix=f"mkdir -p {workdir}    # or `porthole init` to point it "
                   f"somewhere real")
        return
    tree = pathlib.Path(workdir) / "linux"
    note = ("" if (tree / "Makefile").is_file()
            else "  (no kernel tree in it; `porthole build image` needs none)")
    ch.add("host: working repo", "ok", f"{workdir}  (via {key}){note}")


def _check_host_workdir(ch: Checks, cfg) -> None:
    """The HOST pmbootstrap work dir -- the other one.

    Reported beside `workspace: work dir` on purpose. They are different
    directories, they are not interchangeable, and a reader who has seen only
    one of them reasonably assumes it is the one their build used.
    """
    workdir = cfg.get("PORTHOLE_PMB_DIR")
    via = "PORTHOLE_PMB_DIR" if workdir else "default"
    path = pathlib.Path(workdir or (pathlib.Path.home() / ".local/var/pmbootstrap"))
    if path.is_dir():
        ch.add("host: work dir", "ok", f"{path}  (via {via})")
    else:
        # Not a warning: on the workspace tier this directory is never created
        # and never needed, and a yellow row for a tier you are not on is how
        # a setup starts looking broken to somebody who is fine.
        ch.add("host: work dir", "ok",
               f"{path} does not exist  (host builds only; the workspace has "
               f"its own)")


# Below this, a kernel build's chroot plus its ccache plus one rootfs image do
# not fit. A warning rather than a failure: probing, `brain`, `config` and a
# module push need almost nothing, and a host doing that work is not broken.
DISK_WARN_BYTES = 10 * 1024 ** 3


def _write_probe(path: pathlib.Path):
    """("", "") if a file can actually be created here, else (ERRNO, why).

    The errno is handed back separately because the REMEDY differs: EDQUOT and
    ENOSPC mean free something, EACCES and EROFS mean the directory is wrong
    and no amount of deleting will help. A row that prints "free space" at
    someone whose problem is a mode bit is the kind of confident wrong fix this
    file's docstring calls worse than no check at all.

    Free space is NOT the question, and asking it is how this was missed. A
    quota'd filesystem reports terabytes free through statvfs and still refuses
    the write with EDQUOT, because a quota is charged to you and `df` is
    charged to the disk.

    Not hypothetical. On 2026-09-09 an exhausted quota made every command in an
    agent session return exit 1 with completely empty output -- the harness
    could not write the file it captures command output into -- and three
    sessions went into debugging the harness, because nothing asked the one
    question that separates "the tool is broken" from "you are out of quota".
    `df` looked fine throughout. The first process to report it was a `curl`
    exiting 23, which is literally "write error", and that was read as a
    network problem.

    So: try the write. One probe catches EDQUOT, ENOSPC, a read-only remount
    and a permissions problem, and statvfs can see exactly none of them.
    """
    probe = path / ".porthole-write-probe"
    try:
        # 4 KiB rather than an empty file: a block quota is charged on
        # allocation, so a zero-byte create can succeed on a filesystem with no
        # room for anything you would actually put there.
        with open(probe, "wb") as fh:
            fh.write(b"\0" * 4096)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        name = errno.errorcode.get(exc.errno, str(exc.errno))
        return name, "{} -- {}".format(name, exc.strerror or exc)
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return "", ""


def _sandbox_pmb_or_none(cfg):
    """The workspace work dir, or None if that module will not import.

    Same rule _envkernel_candidates duplicates ph-build.sh for: doctor must run
    when the build path is broken, because that is exactly when it is asked.
    """
    try:
        import porthole_cmd_sandbox as sandbox
        return sandbox._sandbox_pmb(cfg)
    except Exception:  # noqa: BLE001 -- a broken sandbox must not break doctor
        return None


def _check_disk(ch: Checks, cfg) -> None:
    """Can porthole write where it builds, and is there room to build there?

    doctor checked thirty-two things and not one of them was disk. It printed
    `25 ok, 3 warn, 1 fail` on a host whose quota was already exhausted and
    never mentioned it, while every build, every fetch and the agent's own
    shell were failing for that one reason. A toolbox whose ordinary rungs
    write tens of GB -- chroots, ccache, a kernel tree, a rootfs image, and a
    chromium tree that reached 12 GB at 9% of a build -- owes this row.

    One row per distinct filesystem, not per directory: on most hosts all four
    of these are the same mount, and four identical rows is how a reader learns
    to skim the section.
    """
    seen = {}
    for label, raw in (
        ("host work dir", cfg.get("PORTHOLE_PMB_DIR")
         or pathlib.Path.home() / ".local/var/pmbootstrap"),
        ("workspace work dir", _sandbox_pmb_or_none(cfg)),
        ("working repo", cfg.get("PORTHOLE_WORKDIR")),
        # Scratch, for pmbootstrap and for whatever harness is driving this.
        # Listed separately because it is so often a DIFFERENT filesystem --
        # a tmpfs, or a separately quota'd mount -- so a green home directory
        # says nothing at all about it. That is the split that hid the 2026-09-09
        # outage: writes to $HOME kept working the whole time.
        ("temp dir", os.environ.get("TMPDIR") or "/tmp"),
    ):
        if not raw:
            continue
        path = pathlib.Path(raw)
        if not path.is_dir():
            continue
        try:
            seen.setdefault(path.stat().st_dev, []).append((label, path))
        except OSError:
            continue

    if not seen:
        ch.add("host: disk", "skip", "no work directory exists yet")
        return

    for paths in seen.values():
        primary, path = paths[0]
        also = ", ".join(lbl for lbl, _ in paths[1:])
        where = "{}{}".format(path, " (also " + also + ")" if also else "")
        code, broken = _write_probe(path)
        if code in ("ENOSPC", "EDQUOT"):
            ch.add("host: disk ({})".format(primary), "fail",
                   "cannot write {}: {}".format(where, broken),
                   fix="free space -- start here, it deletes nothing until "
                       "you add --yes:\n"
                       "          porthole sandbox gc\n"
                       "          porthole build ccache --max 25G"
                       "    # if gc says ccache is the hog\n"
                       "          # gc leaves distfiles and chroots alone on "
                       "purpose and says what they cost",
                   doc="EDQUOT is a quota rather than a full disk -- `df` still "
                       "looks fine, and every tool that writes goes silent")
            continue
        if code:
            # Not a space problem, so do not send anyone deleting things.
            ch.add("host: disk ({})".format(primary), "fail",
                   "cannot write {}: {}".format(where, broken),
                   fix="fix the directory, not the disk: check its mode and "
                       "owner, and that the mount is not read-only\n"
                       "          ls -ld {}".format(path))
            continue
        free = shutil.disk_usage(str(path)).free
        gib = free / 1024 ** 3
        # The probe proves you can write AT ALL; this number is the only thing
        # that speaks to whether a build fits. Neither answers the other's
        # question, which is why both are here.
        if free < DISK_WARN_BYTES:
            ch.add("host: disk ({})".format(primary), "warn",
                   "{:.1f} GiB free on {} -- a kernel build and one rootfs "
                   "image do not fit".format(gib, where),
                   doc="porthole build purge, or pmbootstrap zap")
        else:
            ch.add("host: disk ({})".format(primary), "ok",
                   "{:.0f} GiB free on {}".format(gib, where))


def _envkernel_candidates(cfg):
    """The same search order tools/ph-build.sh uses, in the same order.

    Duplicated deliberately rather than shelled out to: doctor must work when
    the build path is broken, and that is exactly when it is asked.
    """
    home = pathlib.Path.home()
    src = cfg.get("PORTHOLE_PMBOOTSTRAP_SRC", "")
    for label, cand in (
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
           "porthole init    clones it and writes the key, or use the "
           "workspace, whose image carries both")


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

    # A carried fork that upstream has overtaken disappears from the next
    # flash with no message. doctor is where someone looks before forming a
    # theory, so it is where this belongs.
    import porthole_aports_manifest as man

    manifest = man.load(root, device)
    if not manifest:
        ch.add("profile: aports.conf", "warn",
               "this device lists no aports of its own",
               f"profiles/{device}/aports.conf -- what does this port carry "
               f"on top of stock?")
    else:
        trouble = man.problems(manifest)
        if trouble:
            ch.add("profile: aports.conf", "warn", trouble[0],
                   "porthole pkg owned --json")
        else:
            ch.add("profile: aports.conf", "ok",
                   f"{len(man.names(manifest, 'required'))} required, "
                   f"{len(man.names(manifest, 'optional'))} optional")

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

    check_kernel_series(ch, cfg)

    # Measured, not inherited. Until it exists every boot verdict is a guess.
    if not cfg.get("PORTHOLE_REBOOT_BUDGET_S"):
        ch.add("profile: timing", "warn",
               "PORTHOLE_REBOOT_BUDGET_S unset -- measure cold boot to first ssh",
               doc="brain/traps/wait-long-enough-before-calling-a-boot-failed.md")


# Keys whose value carries the kernel series as a `<major>.<minor>` token.
# Deliberately not "every key that mentions a version": a SoC name like
# msm8998 or gs201 has no dot and never matches, and DTB paths carry board
# names rather than releases.
SERIES_KEYS = ("PORTHOLE_KERNEL_PKG", "PORTHOLE_KCONFIG_FILE",
               "PORTHOLE_KERNEL_BRANCH")
_SERIES = re.compile(r"\d+\.\d+")


def check_kernel_series(ch: Checks, cfg) -> None:
    """The config disagreeing with ITSELF about which kernel this is.

    `drift()` compares one layer against another and was blind to this: on
    2026-08-29 every layer agreed and the profile was simply wrong internally.
    The 6.18 -> 7.2 move updated PORTHOLE_KERNEL_PKG and PORTHOLE_KCONFIG_FILE
    and left PORTHOLE_KERNEL_BRANCH naming the old branch two lines below the
    first of them, where it sat for a day.

    Nothing reads KERNEL_BRANCH -- the template calls it "the branch that is
    the product" and it is documentation -- which is exactly why it rotted: an
    unread key has no build to fail it. Warn rather than fail for the same
    reason. A stale branch name misdirects a person; it does not build the
    wrong kernel, and the keys that would are already blocking in
    porthole.GUARDED_KEYS.

    Judged on the RESOLVED value, not on the profile file, so a half-updated
    set of exports is caught by the same rule -- and each value is reported
    with the layer it came from, or the fix would send you to edit a file that
    is already correct.
    """
    seen = {}
    for key in SERIES_KEYS:
        m = _SERIES.search(cfg.get(key, "") or "")
        if m:
            seen[key] = m.group(0)
    if len(set(seen.values())) <= 1:
        if seen:
            ch.add("config: kernel series", "ok",
                   f"{next(iter(set(seen.values())))} across "
                   f"{len(seen)} key{'s' if len(seen) > 1 else ''}")
        return
    source = getattr(cfg, "source", lambda _k: "")
    detail = ", ".join(f"{k}={v}" + (f" ({source(k)})" if source(k) else "")
                       for k, v in seen.items())
    ch.add("config: kernel series", "warn",
           f"these do not name the same kernel: {detail}",
           doc=f"profiles/{cfg.get('PORTHOLE_DEVICE', '')}/device.env holds "
               f"the committed values; an `(environment)` above is an export "
               f"outranking it")


def check_identity(ch: Checks, cfg) -> None:
    # Judge the resolved target, not one input to it: somebody who exports
    # PHONE has a good identity even with PORTHOLE_USER unset.
    if cfg.source("PHONE") == "derived" and cfg.source("PORTHOLE_USER") == "default":
        ch.add("identity", "warn",
               f"nothing set a username; ssh will use {cfg['PORTHOLE_USER']!r}",
               doc="porthole init")
    else:
        ch.add("identity", "ok", f"{cfg['PHONE']} (via {cfg.source('PHONE')})")


def check_device(ch: Checks, ctx, cfg, elapsed: float) -> None:
    dev = ctx.device()
    # 30 s, not 0: cmd_doctor probed seconds ago and this row is a DISPLAY of
    # that verdict, not something about to act on the device. `state()`'s own
    # docstring draws exactly this line -- a cached BOOTED handed to something
    # that then flashes is what the default of 0 exists to prevent.
    #
    # `elapsed` is handed in rather than timed here: the cache makes this call
    # near-instant, and printing THAT duration would claim a round trip that
    # never happened. `elapsed` is the real one, timed by cmd_doctor around
    # the probe that actually warmed the cache.
    state = dev.state(max_age=30.0)
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
               doc="tools/ph-reboot.sh, or fastboot set_active + reboot")
    elif state == "INITRAMFS":
        ch.add("device: state", "warn",
               f"{detail} -- stopped in the pmOS initramfs debug shell",
               doc="tools/tsh.py 'dmesg | grep pmOS-rd'   # it will say why root did not mount")
    elif state == "FROZEN":
        ch.add("device: state", "warn",
               f"{detail} -- kernel alive, userspace gone",
               doc="brain/traps/frozen-is-not-hung.md; tools/ph-recover.sh")
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


def check_device_packages(ch: Checks, ctx, cfg, device_state: str) -> None:
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
    # `cmd_doctor` already probed and knows the device is not BOOTED -- a
    # second dial here would rediscover the exact same ABSENT/FROZEN/etc and
    # report it in different words, 25s timeout and all. `skip`, not `warn`:
    # this must never look like the "asked, and apk could not answer" case
    # below, which stays reachable whenever the device IS booted and the
    # query itself fails.
    if device_state != "BOOTED":
        ch.add("device: packages", "skip", f"the device is {device_state}")
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

    import porthole

    def timed(label, fn, budget_ms):
        start = time.monotonic()
        fn()
        ms = (time.monotonic() - start) * 1000
        ch.add(f"bench: {label}", "ok" if ms <= budget_ms else "warn",
               f"{ms:.0f}ms (budget {budget_ms}ms)",
               doc="" if ms <= budget_ms else "docs/PERFORMANCE.md")

    dev.run("true")                                    # warm the master
    timed("ssh round trip (warm)", lambda: dev.run("true"), 30)
    try:
        timed("fastboot devices", dev.in_fastboot, 250)
    except porthole.FastbootUnavailable as exc:
        # One probe exploding degrades the verdict, never the command -- the
        # rule a missing `ping` binary bought when it took doctor down.
        ch.add("bench: fastboot devices", "fail", str(exc),
               doc="`porthole doctor` names how to install fastboot")
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
    if value:
        # `unset` alone fixes ONE shell. Reported three times by agents that
        # each worked around it with `env -u PMB_SUDO` and moved on, which
        # fixes nothing and hides the check: on the host where this was found
        # the export came from the desktop session, so every new terminal and
        # every agent inherited it again.
        ch.add("host: PMB_SUDO", "fail",
               f"set to {value} -- a leftover; the privilege broker it named "
               f"is gone",
               fix="systemctl --user unset-environment PMB_SUDO"
                   "    # then `unset PMB_SUDO` in this shell."
                   " A build otherwise dies with exit 78 deep inside "
                   "pmbootstrap, naming nothing. The user manager is where it"
                   " was actually found: not in any rc file, not in"
                   " environment.d, so grepping dotfiles finds nothing and"
                   " every new terminal inherits it again")
    else:
        ch.add("host: PMB_SUDO", "ok", "unset -- nothing here uses it")
    _check_leftover_broker(ch, value)


# Where porthole's own installer used to put the broker. A historical fact
# about this project rather than a device fact, so naming it here is not the
# hardcoding rule: there is no config that could supply it, because the verb
# that wrote it was deleted with it.
BROKER_PATHS = ("/usr/local/libexec/porthole/ph-sudo",)


def _check_leftover_broker(ch: Checks, pmb_sudo: str) -> None:
    """The broker BINARY, which outlives the export and the design that had it.

    `sandbox/ph-sudo` and the `install`/`audit`/`uninstall` verbs were deleted
    on 2026-08-29 (docs/SANDBOX-PROVISIONING.md section 8). Deleting the
    installer did not uninstall anything, and nothing has looked since -- so a
    host provisioned before that date still carries a setuid-adjacent helper
    and, in all likelihood, the sudoers entry that makes it work. That entry
    is standing host privilege, which is the ONE property this whole subsystem
    exists to remove, and it was left in place by the change that claimed to
    remove it.

    A warning rather than a failure: it breaks nothing, `porthole` never calls
    it, and doctor must not fail a host over something it cannot check
    completely -- /etc/sudoers.d is unreadable without root, so the entry can
    only be guessed at from the file's presence.
    """
    found = [path for path in
             dict.fromkeys([*BROKER_PATHS, pmb_sudo] if pmb_sudo
                           else BROKER_PATHS)
             if path and os.path.exists(path)]
    if not found:
        ch.add("host: ph-sudo broker", "ok",
               "not installed -- no standing host privilege")
        return
    ch.add("host: ph-sudo broker", "warn",
           f"{found[0]} still installed -- the privilege broker was deleted "
           f"from porthole on 2026-08-29 and never uninstalled here",
           fix=f"sudo rm -f {' '.join(found)}"
               "  # then check for its sudoers entry: sudo ls /etc/sudoers.d"
               " -- that entry is standing root, which the workspace exists"
               " to do without")


def _check_pmos_password(ch: Checks, env) -> None:
    """TK_PMOS_PASSWORD unset, which stops `kernel` and `upgrade` dead.

    The exact mirror of _check_pmb_sudo: that one fails when a variable is
    SET, this one warns when one is UNSET. tools/ph-build.sh:820 requires it
    and dies with a bare shell parameter error naming no fix, and doctor
    reported 23 ok / 2 warn / 2 fail on a host where it was missing without
    mentioning it once.

    WARN and not FAIL: it gates two rungs of six. A host doing `mod` and
    `boot` work is not broken for lacking it, and a FAIL there teaches people
    to skim the row.

    The VALUE is never printed. It is a rootfs password; see
    tests/test_secrets.py.
    """
    # The same reader `porthole build` uses, so the two cannot disagree about
    # whether this host has it -- including about which of the two names it
    # was set under.
    try:
        from porthole_cmd_build import rootfs_password
    except Exception:  # noqa: BLE001 -- doctor must run when build cannot
        def rootfs_password(cfg):
            return (cfg.get("TK_PMOS_PASSWORD")
                    or cfg.get("PORTHOLE_PMOS_PASSWORD") or "").strip()
    if rootfs_password(env):
        ch.add("host: PORTHOLE_PMOS_PASSWORD", "ok", "set")
        return
    # The rungs come from build's own table, so the two cannot drift. This row
    # named `upgrade`, which does not run `pmbootstrap install` at all and has
    # never needed the password -- avoiding the mkfs is the entire reason it
    # is fast.
    try:
        from porthole_cmd_build import INSTALL_RUNGS as rungs
    except Exception:  # noqa: BLE001 -- doctor must run when build cannot
        rungs = ("kernel", "image")
    named = " and ".join(f"`porthole build {r}`" for r in rungs)
    ch.add("host: PORTHOLE_PMOS_PASSWORD", "warn",
           f"unset -- {named} need it and stop dead without it",
           fix="export PORTHOLE_PMOS_PASSWORD=<the rootfs user password>"
               "    # an environment variable on purpose: a flag would show "
               "it in ps")


def _device_key_row(ch: Checks, state, skip_reason: str = "") -> None:
    """The workspace's device key, reported by whether the phone accepts it.

    doctor printed `✓ device key /home/you/.porthole/device_key` for a key the
    device had never been told about, because it checked that the FILE exists.
    That is the difference between "the key is present" and "the key works",
    and only the second is what a build needs.

    The fix is printed and never applied: installing a key is a privileged
    write to the device, and doctor's contract is that it names fixes it will
    not run itself.

    `None` now means two different things: asked and could not tell, or
    deliberately not asked (`--no-device`, or a device already known to be
    something other than BOOTED). `check_device_packages` drew this same
    line as `skip ... the device is ABSENT` rather than `warn`, precisely so
    it cannot be confused with an actual failed probe -- this row now makes
    the same distinction, on `state["device_key_probed"]`.
    """
    key = state.get("device_key")
    if not key:
        ch.add("workspace: device key", "warn", "not created",
               doc="porthole sandbox up    creates one")
        return
    authorized = state.get("device_key_authorized")
    if authorized is True:
        ch.add("workspace: device key", "ok", f"{key} -- the device accepts it")
    elif authorized is False:
        ch.add("workspace: device key", "fail",
               f"{key} exists, and the device refuses it -- every workspace "
               f"push fails with `scp: Connection closed`",
               f"print the public half here, then add that ONE line to the "
               f"phone's ~/.ssh/authorized_keys:\n"
               f"          ssh-keygen -y -f {key}")
    elif not state.get("device_key_probed", True):
        ch.add("workspace: device key", "skip",
               f"{key} exists -- not asked ({skip_reason or 'not probed'})",
               doc="a key file is not a working key; re-run with the device "
                   "booted and reachable")
    else:
        ch.add("workspace: device key", "warn",
               f"{key} -- present, but the device could not be asked whether "
               f"it accepts it",
               doc="a key file is not a working key; re-run with the device "
                   "booted and reachable")


def _workspace_fastboot_row(ch: Checks, sandbox) -> None:
    """Does $FASTBOOT resolve INSIDE the container, where builds run?

    Checking it on the host is not the same question and answering the host's
    is what let this ship. config.env is mounted read-only into the workspace
    and load_config reads it in there too, so a FASTBOOT naming a host path --
    the normal case, since that is where platform-tools lives -- named a file
    the container does not have. Every `fastboot devices` in there exited 127
    with empty stdout, which is byte-for-byte a phone that is NOT in the
    bootloader.

    The cost of not checking: a `fast` build compiled, reached "ALL CHECKS
    PASSED - safe to flash", sent the phone to the bootloader, then spent
    181.2s insisting it never got there and told the user to do it by hand --
    with the phone in fastboot the whole time. A precondition that only
    surfaces after a full compile, at the flash step, is one doctor owes you
    up front.
    """
    probe = 'command -v "${FASTBOOT:-fastboot}"'
    try:
        proc = subprocess.run(
            ["podman", "exec", sandbox.CONTAINER, "sh", "-c", probe],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        # The CHECK could not run. Not a finding about FASTBOOT.
        ch.add("workspace: fastboot", "warn",
               f"could not ask the container ({exc})")
        return
    found = (proc.stdout or "").strip()
    if proc.returncode == 0 and found:
        ch.add("workspace: fastboot", "ok", found)
    else:
        ch.add("workspace: fastboot", "fail",
               "$FASTBOOT does not resolve inside the workspace -- every "
               "`fastboot devices` in there looks like a phone that is not "
               "in the bootloader",
               fix="porthole sandbox down && porthole sandbox up",
               doc="the workspace names its own fastboot; a host path "
                   "(platform-tools) does not exist in there")


def _ccache_rows(ch: Checks, ctx, deep: bool) -> None:
    """The one build-speed fact that is cheap to check and expensive to miss.

    `porthole build ccache` has always known when a cache was about to start
    evicting -- and nobody runs that verb before a build. doctor is what people
    run.

    Behind `--all` because it is not free: reading it is one `podman exec` per
    arch, measured at 1.9s against doctor's own 0.7s, and tripling the cost of
    the verb everybody runs is how a check gets skipped by everybody. The
    everyday run says the rows are there and how to see them.
    """
    import porthole_cmd_build as build

    if not deep:
        ch.add("workspace: ccache", "skip", "not read -- one podman exec per "
               "arch", doc="porthole doctor --all")
        return
    for arch, stats in build.ccache_stats_by_arch(ctx):
        level, why = build.ccache_pressure(stats)
        if level == "skip":
            continue
        ch.add("workspace: ccache {}".format(arch), level, why,
               fix=("porthole build ccache --max 25G"
                    if level == "fail" else ""),
               doc="porthole build ccache")


def check_workspace(ch: Checks, ctx, family: str, probe_device: bool = True,
                    skip_reason: str = "", deep: bool = False) -> None:
    """podman and the build workspace.

    podman is the ONE thing that still needs a package manager. Everything else
    the build needs -- pmbootstrap, the toolchain, fuse2fs, android-tools --
    lives in the image, which is why this is the only host prerequisite worth
    failing on.

    `skip_reason` names why the device key was not probed, for the caller
    that already knows -- it says `--no-device` or `the device is ABSENT`
    where `_device_key_row` only has the bool.
    """
    import porthole_cmd_sandbox as sandbox

    state = sandbox._container_state(ctx.root, ctx.cfg, probe_device)
    if not state["podman"]:
        ch.add("host: podman", "fail",
               "not found -- builds run in a rootless container",
               install_hint("podman", family))
        # The device key check reads an ssh key file and asks the PHONE, not
        # the container -- _container_state() already answered it above,
        # before it ever looks at podman. Returning here used to throw that
        # answer away too, so a host with no podman got no report on whether
        # its device key even worked, and a runner's bare PATH (podman
        # deliberately absent, tests/ci-local.sh) made
        # test_an_absent_device_is_probed_once_not_twice fail for a reason
        # that had nothing to do with the device.
        _device_key_row(ch, state, skip_reason)
        return
    ch.add("host: podman", "ok", state["podman"])
    if state["image_built"]:
        ch.add("workspace: image", "ok", state["image"])
    else:
        ch.add("workspace: image", "warn", "not built",
               doc="porthole sandbox up")
    if state["container_running"]:
        ch.add("workspace: container", "ok", sandbox.CONTAINER)
        _workspace_fastboot_row(ch, sandbox)
        _ccache_rows(ch, ctx, deep)
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

    _device_key_row(ch, state, skip_reason)

    # binfmt is host-global and needs root once. Named, never automated: it is
    # a person installing software on their own machine, not a privilege the
    # agent holds.
    _check_pmb_sudo(ch, ctx, state)

    # kernel and upgrade hard-require it (tools/ph-build.sh:820) and die with
    # a bare shell parameter error naming no fix. ctx.cfg falls back to
    # os.environ, same source _check_pmb_sudo reads.
    _check_pmos_password(ch, collections.ChainMap(ctx.cfg, os.environ))

    # The thing this project exists to replace, reported by the verb people
    # actually run. `porthole sandbox` has always shown it; nobody runs
    # `porthole sandbox` before a build, and a 167-hour root credential cache
    # left over from the pre-sandbox era outlived the design that needed it by
    # a week for exactly that reason.
    #
    # A blank read is honest, not a hole: the scan goes through `sudo -n`, and
    # a cache long enough to matter is precisely what makes `sudo -n` succeed.
    # If it fails there is no standing credential to report.
    for issue in sandbox._sudo_state()["issues"]:
        ch.add("host: root credential cache", "warn", issue,
               doc="sudo visudo    # remove the Defaults line; the workspace "
                   "has no sudoers entry and needs none")

    _check_gadget_steals_default_route(ch, ctx)
    _check_legacy_host_override(ch, ctx)

    binfmt = pathlib.Path("/proc/sys/fs/binfmt_misc/qemu-aarch64")
    if binfmt.exists():
        ch.add("host: binfmt aarch64", "ok", "registered")
    else:
        ch.add("host: binfmt aarch64", "warn",
               "not registered -- cross-arch package builds need it",
               doc="install qemu-user-static (host-global, needs root once)")


def _usb_net_ifaces():
    """Interface names whose device sits on the USB bus -- the gadget links.

    From sysfs, not from the routing table and not from the name. Checking
    "what routes to the device" only finds the gadget while the device is
    reachable over it, so it goes blind exactly when someone has switched to
    wifi to escape this bug -- and the dead profile is still there waiting to
    autoconnect. Names are no better: udev builds them from the USB path, so
    it is enp5s0f3u2 on one machine and enp0s20f0u4 on the next.
    """
    out = []
    net = pathlib.Path("/sys/class/net")
    if not net.is_dir():
        return out
    for iface in net.iterdir():
        try:
            if "usb" in os.path.realpath(iface / "device" / "subsystem"):
                out.append(iface.name)
                continue
            # The gadget hangs off a USB device several levels up, so the
            # interface's own subsystem is "net" and only the full path says
            # USB. Cheap and works for both shapes.
            if "/usb" in os.path.realpath(iface / "device"):
                out.append(iface.name)
        except OSError:
            continue
    return out


def _unsafe_gadget_profiles():
    """[(uuid, name, iface, [unsafe keys])] for every USB-link NM profile that
    could hand the host a default route or nameservers.

    Reports a profile whether or not it is currently up: an autoconnect
    profile that is down right now is simply waiting for the next enumeration.
    """
    found = []
    try:
        listing = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"],
            capture_output=True, text=True, timeout=5)
        if listing.returncode:
            return found
    except (OSError, subprocess.SubprocessError):
        return found
    usb = set(_usb_net_ifaces())
    for line in listing.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 4 or parts[2] != "802-3-ethernet":
            continue
        name, uuid = parts[0], parts[1]
        try:
            show = subprocess.run(["nmcli", "-t", "connection", "show", uuid],
                                  capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        settings = dict(l.split(":", 1) for l in show.stdout.splitlines()
                        if ":" in l)
        iface = settings.get("connection.interface-name", "").strip()
        if iface not in usb:
            continue
        # autoconnect=no is a complete answer: the profile can never apply
        # itself, so its route and DNS settings cannot matter.
        if settings.get("connection.autoconnect", "").strip() == "no":
            continue
        unsafe = [k for k in ("ipv4.never-default", "ipv6.never-default",
                              "ipv4.ignore-auto-dns", "ipv6.ignore-auto-dns")
                  if settings.get(k, "").strip() == "no"]
        if unsafe:
            found.append((uuid, name, iface, unsafe))
    return found


def _check_gadget_steals_default_route(ch: Checks, ctx) -> None:
    """The phone must not be allowed to become the host's default route or DNS.

    The USB gadget is a DHCP server. NetworkManager's default for a fresh wired
    profile is to accept everything it offers -- including a default route and
    nameservers -- so plugging the phone in silently reroutes the HOST's
    traffic into a device that has no upstream. The failure does not look like
    networking: web pages stop loading, `git push` hangs, and the agent in the
    terminal keeps working because its own traffic is on the link that still
    works. Reported by a user mid-session as "you set up an ethernet to the
    device that kept disconnecting my host".

    A warning, not a failure: some setups genuinely do route through the phone,
    and doctor must not fail a host over a deliberate choice. But the default
    is wrong for a bring-up and nothing else says so.
    """
    bad = _unsafe_gadget_profiles()
    if not bad:
        ch.add("host: device link", "ok",
               "no USB-link profile can take the host's default route or DNS")
        return
    uuid, name, iface, unsafe = bad[0]
    ch.add("host: device link", "warn",
           f"{name} ({iface}) may take the host's default route or DNS "
           f"({', '.join(unsafe)}) -- the phone is a DHCP server with no "
           f"upstream, so the host loses internet whenever it enumerates"
           + (f", and {len(bad) - 1} more like it" if len(bad) > 1 else ""),
           fix="nmcli connection modify " + uuid +
               " ipv4.never-default yes ipv6.never-default yes"
               " ipv4.ignore-auto-dns yes ipv6.ignore-auto-dns yes"
               " ipv4.route-metric 4000 ipv6.route-metric 4000"
               "    # or `connection.autoconnect no` if you reach the device"
               " over wifi and never want this link configured at all")


def _check_legacy_host_override(ch: Checks, ctx) -> None:
    """HOST and PHONE outrank PORTHOLE_HOST, and nothing says so.

    ph-lib.sh's compatibility surface takes TK_HOST, then PHONE, then
    PORTHOLE_HOST -- deliberately, so an old setup keeps working. The cost is
    that setting the DOCUMENTED variable does nothing when a legacy one is
    exported, and the tools keep talking to the old address with no message at
    all. Hit while moving a device from the USB gadget to wifi:
    `PORTHOLE_HOST=<wifi ip> porthole ...` kept timing out against the USB
    address, three times, before anyone thought to look at $HOST.
    """
    want = (ctx.cfg.get("PORTHOLE_HOST") or "").strip()
    for name in ("TK_HOST", "HOST", "PHONE"):
        got = (os.environ.get(name) or "").strip()
        if not got:
            continue
        addr = got.split("@")[-1]
        if want and addr != want:
            ch.add("host: address override", "warn",
                   f"${name}={got} outranks PORTHOLE_HOST={want}, so the tools "
                   f"talk to {addr}",
                   fix=f"unset {name}    # or set it to the same address."
                       " ph-lib.sh takes TK_HOST, then PHONE, then"
                       " PORTHOLE_HOST, and says nothing when they disagree")
            return
    ch.add("host: address override", "ok",
           "no legacy HOST/PHONE shadowing PORTHOLE_HOST")


def cmd_doctor(args, ctx) -> int:
    family = distro_family()
    ch = Checks()
    cfg = ctx.cfg

    check_host(ch, cfg, family)
    # ONE probe, before anything that would open its own connection.
    #
    # Two independent checks each dialled the same phone and neither knew the
    # other had just failed: `check_device` costs a probe, and the workspace's
    # device-key row costs a full ssh with ConnectTimeout=5. On an absent
    # device that is 7 s for one answer.
    #
    # `state()` writes its verdict to the cache under XDG_CACHE_HOME, so the
    # `max_age` read below is free; and it honours PORTHOLE_DEVICE_STATE, so a
    # test that declares the device absent no longer pays for the declaration.
    #
    # Timed here, not inside check_device: this is the only call that actually
    # dials the device, so this is the only honest place to measure it. The
    # later cache read is near-instant and must not be reported as if it were
    # this round trip.
    device_state = ""
    device_state_elapsed = 0.0
    if not args.no_device:
        start = time.monotonic()
        device_state = ctx.device().state()
        device_state_elapsed = (time.monotonic() - start) * 1000
    if args.no_device:
        key_skip_reason = "--no-device"
    elif device_state and device_state != "BOOTED":
        key_skip_reason = f"the device is {device_state}"
    else:
        key_skip_reason = ""
    check_workspace(ch, ctx, family,
                    probe_device=(device_state == "BOOTED"),
                    skip_reason=key_skip_reason, deep=args.all)
    check_drift(ch, cfg)
    check_profile(ch, cfg, ctx.root)
    check_identity(ch, cfg)
    if args.no_device:
        ch.add("device: state", "skip", "--no-device")
    else:
        check_device(ch, ctx, cfg, elapsed=device_state_elapsed)
        check_device_packages(ch, ctx, cfg, device_state)
    if args.tools or args.all:
        check_tools(ch, ctx.root)
    if args.bench and not args.no_device:
        bench(ch, ctx)

    payload = {"verdict": ch.worst(), "counts": ch.counts(),
               "distro_family": family, "checks": ch.rows}

    def render():
        # `out.status` rather than a colour table of this file's own: the
        # glyph and the colour for "ok" have to mean the same thing here, in
        # the build display and in every listing, or a reader learns a second
        # alphabet per verb.
        width = max(len(r["name"]) for r in ch.rows)
        for row in ch.rows:
            word = "FAIL" if row["status"] == "fail" else row["status"]
            ctx.out("  {}  {}  {}".format(
                ctx.out.status(row["status"], word),
                ctx.out.paint("{:<{}}".format(row["name"], width), "grey"),
                row["detail"]))
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
    "group": "start",
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
