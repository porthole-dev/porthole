#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The buildroot mutex.

One workspace has ONE pmbootstrap buildroot chroot per architecture, and
`abuild` cleans `$srcdir` before it unpacks. So a second pmbootstrap operation
started while the first is compiling DELETES the first one's source tree
mid-flight, and pmbootstrap has no lock of its own.

It has now cost two sessions:

  taimen 2026-08-30   a webkit build died 37 minutes in with
                      `clang++: no such file or directory: TextMetrics.idl`,
                      because a second `pmbootstrap build` had replaced the
                      staged source. The error named the compiler.
  redfin 2026-08-30   a kernel build died with
                      `cc1: fatal error: ../fs/configfs/file.c: No such file`
                      because a `pmbootstrap checksum` run in parallel removed
                      /pmb/chroot_native/home/pmos/build. The error named the
                      kernel.

Both failures accuse the toolchain, and neither mentions the collision. That
is what makes the trap expensive: the obvious reading is a broken compiler.

This lives in its own module rather than inside one verb because CHECKSUM is
what destroyed the redfin build. A mutex that only the build verb takes is not
a mutex; every pmbootstrap caller in the tree has to take the same one.

Same idiom as the device mutex (`tools/tk-device.sh`): flock plus a `.holder`
sidecar naming who has it, and exit 75 so "busy" is distinguishable from "your
package is broken". flock and not a hand-rolled lockfile because the kernel
drops it when the holder dies -- a crashed build must not wedge everyone until
a human notices.

See brain/traps/two-pmbootstrap-builds-destroy-each-other.md.
"""
from __future__ import annotations

import contextlib
import os
import pathlib
import time

from porthole_cli import Bail, EX_LOCK

LOCK_NAME = ".porthole-buildroot.lock"


def _holder_path(workdir) -> pathlib.Path:
    return pathlib.Path(workdir) / (LOCK_NAME + ".holder")


def lock_holder(workdir) -> str:
    """What is currently building here, or "" if nothing is."""
    try:
        return _holder_path(workdir).read_text().strip()
    except OSError:
        return ""


def is_free(workdir) -> bool:
    """A cheap probe, for refusing early rather than after a detach."""
    import fcntl

    path = pathlib.Path(workdir) / LOCK_NAME
    try:
        with open(path, "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        return True  # cannot tell; the real lock below is the authority
    return True


def holder_pid(workdir):
    """The pid named by the `.holder` sidecar, or None."""
    for word in lock_holder(workdir).split():
        if word.startswith("pid="):
            try:
                return int(word[4:])
            except ValueError:
                return None
    return None


def abandoned(workdir, alive=None) -> str:
    """The holder of a lock whose owner is gone, or "".

    `flock` is released by the kernel when its holder dies. That is the right
    behaviour for a crashed build -- nobody is wedged -- and exactly the wrong
    signal here, because the build itself does NOT die with the porthole run
    that started it: `podman exec` runs it server-side. So the mutex reads
    free while the buildroot is in use, which is
    brain/traps/two-pmbootstrap-builds-destroy-each-other with its safety
    catch filed off.

    The sidecar is what is left over: it leaks when its writer is killed
    before the `finally` runs, and it names the pid that died. Free, no
    podman, and true right now on this machine. It is a HINT and never a
    refusal on its own -- a leaked sidecar from a build that really is over
    must not wedge the next one, which is the whole reason the lock is an
    flock and not a lockfile.
    """
    pid = holder_pid(workdir)
    if pid is None:
        return ""
    check = _pid_alive if alive is None else alive
    return "" if check(pid) else lock_holder(workdir)


def _pid_alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def clear_holder(workdir) -> None:
    """Drop a sidecar whose build is provably over."""
    try:
        _holder_path(workdir).unlink()
    except OSError:
        pass


@contextlib.contextmanager
def hold(workdir, what: str, wait: float = 0.0, probe=None):
    """Exclusive use of the buildroot for the length of one build.

    `probe` answers "is a pmbootstrap running in the workspace?" and is the
    only guard that binds a build nobody locked -- one started through
    `sandbox shell --command`, or one whose porthole run was killed while the
    workspace kept building. It lives HERE rather than in a caller because
    `checksum` is what destroyed the redfin kernel build: a mutex that only
    the build verb takes is not a mutex, and the same is true of a probe that
    only the build verb makes.

    It is asked only when the flock is free but a sidecar says it should not
    be -- so the common case, an idle buildroot with no leftovers, costs
    nothing at all.
    """
    import fcntl

    path = pathlib.Path(workdir) / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    stale = abandoned(workdir)
    if stale:
        running = (probe() or "") if probe else ""
        if running:
            raise Bail(
                f"the buildroot is busy: {running} is building, and the run "
                f"that locked it ({stale.strip()}) is gone",
                EX_LOCK,
                "the build outlived its tracker, so the flock was released "
                "while the buildroot stayed in use. Starting now would delete "
                "its source tree "
                "(brain/traps/two-pmbootstrap-builds-destroy-each-other). "
                "Follow it with `porthole pkg watch` and wait for it.")
        # Nothing is running: the sidecar is litter from a build that really
        # did end. Clearing it is what keeps a dead holder from reading as a
        # live one forever.
        clear_holder(workdir)
    handle = open(path, "a")
    deadline = time.time() + wait
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() >= deadline:
                who = lock_holder(workdir) or "another build"
                handle.close()
                raise Bail(
                    f"the buildroot is busy: {who}", EX_LOCK,
                    "starting now would delete its source tree mid-build "
                    "(brain/traps/two-pmbootstrap-builds-destroy-each-other). "
                    "Wait for it, or pass --wait SECONDS to queue.") from None
            time.sleep(1.0)
    try:
        _holder_path(workdir).write_text(
            f"{what} pid={os.getpid()} since={time.strftime('%H:%M:%S')}\n")
    except OSError:
        pass
    try:
        yield
    finally:
        try:
            _holder_path(workdir).unlink()
        except OSError:
            pass
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def staged_build_name(workdir):
    """`(pkgname, started)` for the build staged in a buildroot, or `(None, None)`.

    pmbootstrap copies the APKBUILD it is about to build into
    `<workdir>/chroot_buildroot_<arch>/home/pmos/build/APKBUILD`, so one small
    file read names the package and its mtime says when the build began.

    This exists because the log cannot answer it. The naming banner
    (`=> edge/<pkg>: Building package`) is written once at the start, and two
    hours into a 37 MB log it is far outside the tail anything reads; the lines
    that ARE in the tail are ninja's, which name object files and not packages.

    It names, and never decides liveness. The staged file survives the build
    that wrote it, so on its own it is the LAST build, not a running one --
    the log's mtime is what says something is building now. Keeping those two
    facts in separate places is deliberate: conflating them is how a finished
    build sat at "99% reattached" all night.
    """
    newest = None
    try:
        roots = sorted(pathlib.Path(workdir).glob(
            "chroot_buildroot_*/home/pmos/build/APKBUILD"))
    except OSError:
        return None, None
    for path in roots:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest[1]:
            newest = (path, mtime)
    if newest is None:
        return None, None
    try:
        text = newest[0].read_text(errors="replace")
    except OSError:
        return None, None
    for line in text.splitlines():
        if line.startswith("pkgname="):
            name = line.split("=", 1)[1].strip().strip('"').strip("'")
            if name:
                return name, newest[1]
    return None, None


def foreign_build(ps_output: str) -> str:
    """A pmbootstrap build running that did NOT come through this verb, or "".

    The flock only binds callers who take it. `porthole sandbox shell
    --command 'pmbootstrap build ...'` is a bare command runner and takes
    nothing, and that is exactly how the webkit build that got destroyed was
    started -- so a lock alone still lets `porthole pkg build` walk into one.

    Reads `ps -eo pid=,args=` and filters HERE rather than asking pgrep to
    match a pattern. A `pgrep -f "pmbootstrap.*build"` run through `sh -c`
    puts that pattern into its own command line and matches ITSELF, so a
    guard written that way reports "busy" forever and never starts -- observed
    in a real session, waiting on a buildroot that had been free for minutes.
    A ps line cannot contain the filter, because the filter never reaches it.
    """
    for line in ps_output.splitlines():
        if "pmbootstrap" in line and " build" in line and "pgrep" not in line:
            # Name the package if it is on the command line; a pid alone sends
            # the reader back to ps to find out what they are waiting for.
            words = line.split()
            for i, word in enumerate(words):
                if word == "build" and i + 1 < len(words):
                    # Options that take a SEPARATE value: their value is not
                    # the package. `--arch aarch64 webkit2gtk-6.0` used to be
                    # reported as "aarch64" (2026-09-06).
                    takes_value = {"--arch", "-a", "--src", "--pkgrel"}
                    tail, skip = [], False
                    for w in words[i + 1:]:
                        if skip:
                            skip = False
                            continue
                        if w in takes_value:
                            skip = True
                            continue
                        if not w.startswith("-"):
                            tail.append(w)
                    if tail:
                        return tail[0]
            return "another pmbootstrap build"
    return ""


def running_build(ctx, in_container: bool) -> str:
    """Ask the container what is running. Never raises: refusing to build is
    a safety measure, and it must not itself become a way to fail."""
    import subprocess

    if not in_container:
        return ""
    import porthole_cmd_sandbox as sandbox

    try:
        done = subprocess.run(
            ["podman", "exec", sandbox.CONTAINER, "ps", "-eo", "pid=,args="],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return foreign_build(done.stdout)
