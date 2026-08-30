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


@contextlib.contextmanager
def hold(workdir, what: str, wait: float = 0.0):
    """Exclusive use of the buildroot for the length of one build."""
    import fcntl

    path = pathlib.Path(workdir) / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
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
                    tail = [w for w in words[i + 1:] if not w.startswith("-")]
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
