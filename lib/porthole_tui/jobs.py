# SPDX-License-Identifier: MIT
"""Commands that run without taking the screen away.

The curses console dropped out with endwin(), ran the command, and read a
keypress to come back. That works, and it means a twenty-minute build owns your
terminal for twenty minutes. Here a job streams into a drawer instead, so you
can read a note while it runs.

Two things it does NOT do, on purpose:

  - it does not decide whether a command is allowed to run. safety.py does
    that, at the call site, before spawn() is reached.
  - it does not run interactive commands. `porthole serial console` drives
    termios and pmbootstrap prompts; those get the real terminal via the app's
    suspend path. A pty here would be a second terminal emulator in a project
    that already has one.
"""
from __future__ import annotations

import asyncio
import collections
import os
import shlex
import signal
import sys
import time

MAX_LINES = 20000
MAX_LINE = 4096

# A build interrupted with SIGKILL first leaves a dirty tree. Escalate:
# (seconds after cancel, signal).
_ESCALATION = ((0.0, signal.SIGINT), (3.0, signal.SIGTERM),
               (10.0, signal.SIGKILL))


class Job:
    """One command: its output, its exit code, and how to stop it."""

    def __init__(self, command, argv, max_lines=MAX_LINES):
        self.command = command
        self.argv = list(argv)
        self.lines = collections.deque(maxlen=max_lines)
        self.rc = None
        self.started = time.monotonic()
        self.finished = None
        self.state = "queued"
        self._proc = None
        self._done = asyncio.Event()
        self._cancelled_at = None

    def duration(self):
        return (self.finished or time.monotonic()) - self.started

    def cancel(self):
        """Ask it to stop. The escalation runs on the task that owns it."""
        if self._cancelled_at is None:
            self._cancelled_at = time.monotonic()
            self._signal(signal.SIGINT)

    def _signal(self, sig):
        # Signal the whole process group, not just the immediate child.
        # `sh -c '...'` does not always tail-call-exec into its last command
        # (dash forks instead of exec'ing) -- and a shell that's blocked
        # waiting on a foreground child of its own can defer the signal
        # until that child exits on its own, which makes cancel a no-op in
        # practice. start_new_session=True at spawn puts the child in its
        # own group so this reaches it and any of its children directly.
        if self._proc is not None and self._proc.returncode is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), sig)
            except (ProcessLookupError, OSError):
                pass

    async def wait(self):
        await self._done.wait()
        return self.rc


class JobManager:
    """Spawns jobs and keeps their history for the session."""

    def __init__(self, root, max_lines=MAX_LINES):
        self.root = root
        self.max_lines = max_lines
        self.jobs = []

    @property
    def running(self):
        return [j for j in self.jobs if j.state == "running"]

    def spawn(self, command, on_line=None, on_exit=None):
        """Start a command. Returns immediately; output arrives via on_line.

        `command` is the full string, `porthole ...` included -- the same
        string safety.needs_confirmation() was given, so what was confirmed and
        what runs cannot drift apart.
        """
        argv = shlex.split(command)
        job = Job(command, argv, self.max_lines)
        self.jobs.append(job)
        asyncio.ensure_future(self._run(job, on_line, on_exit))
        return job

    async def _run(self, job, on_line, on_exit):
        job.state = "running"
        argv = list(job.argv)
        if argv and argv[0] == "porthole":
            argv = [sys.executable,
                    str(self.root / "bin" / "porthole")] + argv[1:]
        # exec has no shell, so a literal `~` would reach the tool unexpanded and
        # it would fail with "no such file". argspec deliberately leaves `~`
        # unquoted so the previewed command stays readable; this is where that
        # is paid for.
        argv = [os.path.expanduser(a) for a in argv]
        try:
            job._proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        except (OSError, ValueError) as exc:
            job.lines.append("could not run it: {}".format(exc))
            self._finish(job, 127, "done", on_exit)
            return

        rc = None
        try:
            reader = asyncio.ensure_future(self._pump(job, on_line))
            waiter = asyncio.ensure_future(job._proc.wait())
            step = 0
            while not waiter.done():
                await asyncio.wait({waiter}, timeout=0.25)
                if job._cancelled_at is None:
                    continue
                elapsed = time.monotonic() - job._cancelled_at
                while step + 1 < len(_ESCALATION) and elapsed >= _ESCALATION[step + 1][0]:
                    step += 1
                    job._signal(_ESCALATION[step][1])
            await reader
            rc = waiter.result()
        finally:
            self._finish(job, rc,
                         "cancelled" if job._cancelled_at is not None else "done",
                         on_exit)

    def _finish(self, job, rc, state, on_exit):
        job.rc = rc
        job.finished = time.monotonic()
        job.state = state
        job._done.set()
        if on_exit:
            on_exit(job)

    async def _pump(self, job, on_line):
        # Chunked, not readline(): asyncio's StreamReader raises once a single line
        # passes its 64KiB buffer with no newline, and NOTHING here caught that -- the
        # job's bookkeeping was skipped and job.wait() blocked forever while the
        # process had already exited. Progress output is exactly that shape: fastboot,
        # dd, wget and pmbootstrap all draw with carriage returns and never emit a
        # newline until the end. Splitting on \r as well as \n turns that into
        # successive lines, which is what a log drawer wants anyway.
        stream = job._proc.stdout
        buf = b""

        def emit(raw):
            text = raw.decode("utf-8", "replace")
            job.lines.append(text)
            if on_line:
                on_line(text)

        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                buf = (buf + chunk).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    emit(line)
                # No separator in sight. Flush rather than grow without bound.
                while len(buf) > MAX_LINE:
                    emit(buf[:MAX_LINE])
                    buf = buf[MAX_LINE:]
        except Exception as exc:  # noqa: BLE001
            # A read error must never skip the caller's finish bookkeeping.
            emit(b"porthole: lost the output stream: "
                 + str(exc).encode("utf-8", "replace"))
        finally:
            if buf:
                emit(buf)

    def cancel_all(self):
        for job in self.running:
            job.cancel()


def is_interactive(root, command):
    """Does this command expect to own the terminal?

    Read from the registry each verb already publishes, so a new interactive
    verb declares itself rather than being added to a list here that nobody
    remembers to update.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if len(parts) < 2 or parts[0] != "porthole":
        return False
    try:
        import porthole_cli
        for spec in porthole_cli.discover(root):
            if spec["verb"] == parts[1]:
                return bool(spec.get("interactive", False))
    except Exception:  # noqa: BLE001
        # Could not read the registry. Assume interactive: suspending the UI
        # unnecessarily is recoverable, piping a termios-driven command is not.
        return True
    return False
