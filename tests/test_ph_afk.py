#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ph-afk.sh's bounded mask must not outlive its own expiry timer.

The bug: `on <duration>` masked suspend with a PERSISTENT `systemctl mask`
(a symlink in /etc/systemd/system, survives reboots) but armed its expiry
with a TRANSIENT `systemd-run --on-active=` timer (in /run, wiped by a
reboot). Any reboot before the timer fired left suspend masked forever,
silently -- confirmed on the live device after "days" without suspend.

The fix ties the mask's lifetime to the timer's: `on <duration>` now masks
with `systemctl mask --runtime` too, so both halves live in /run and a
reboot destroys them together. `on` with no duration keeps the deliberate
persistent mask. `off` must clear both forms, since either can be present.

None of this reaches a real device -- ph-afk.sh's whole job is ssh calls, so
a fake `ssh` on PATH (same trick as test_capture_userspace.py's
_fake_ssh_path) intercepts every tk_run call, logs the exact remote command
line, and answers is-enabled/is-active probes from env vars. The tests then
assert on the LOGGED COMMANDS -- which mask/unmask flags were actually sent
-- not on any real systemd state, and on the STATUS TEXT the tool prints for
each masked/timer combination, since a status line that fails to say "this
will not clear itself" is exactly the silence that cost the days.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "ph-afk.sh"

# The fake `ssh`: logs the remote command (always the last argv, whatever
# TK_SSH_OPTS prepended) and answers the two probes ph-afk.sh's own logic
# branches on. FAKE_MASKED_COUNT / FAKE_RUNTIME_COUNT are asked for directly
# rather than derived by emulating `grep -c` here, because the real pipeline
# never runs -- this script IS the whole remote side, there is no shell on
# the other end of it to run a pipe.
_FAKE_SSH = """#!/bin/sh
for a in "$@"; do cmd=$a; done
printf '%s\\n' "$cmd" >> "$FAKE_SSH_LOG"
case "$cmd" in
    *"grep -c '^masked-runtime"*)
        printf '%s\\n' "${FAKE_RUNTIME_COUNT:-0}" ;;
    *"grep -c '^masked"*)
        printf '%s\\n' "${FAKE_MASKED_COUNT:-0}" ;;
    *"is-active "*)
        printf '%s\\n' "${FAKE_IS_ACTIVE:-inactive}" ;;
esac
exit 0
"""


def _run(args, log, **fake_env):
    """Run ph-afk.sh with the fake ssh on PATH, logging every remote command
    it issues to `log`. Returns the CompletedProcess."""
    with tempfile.TemporaryDirectory() as tmp:
        bindir = pathlib.Path(tmp) / "bin"
        bindir.mkdir()
        (bindir / "ssh").write_text(_FAKE_SSH)
        (bindir / "ssh").chmod(0o755)
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                   PHONE="test@test", FAKE_SSH_LOG=str(log))
        env.update(fake_env)
        return subprocess.run(["bash", str(TOOL), *args],
                              capture_output=True, text=True, env=env)


def _log_text(log):
    return log.read_text() if log.exists() else ""


# ------------------------------------------------------- on <duration> ----

def test_on_with_duration_masks_runtime_not_persistent():
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log"
        proc = _run(["on", "30m"], log, FAKE_MASKED_COUNT="3")
        assert proc.returncode == 0, proc.stderr
        text = _log_text(log)
        assert "systemctl mask --runtime sleep.target suspend.target systemd-suspend.service" in text, \
            "bounded `on` must mask with --runtime so the mask lives in /run, next to its timer"
        assert "systemctl mask sleep.target suspend.target systemd-suspend.service" not in text, \
            "bounded `on` must never fall back to the persistent (/etc) mask"


def test_on_with_duration_arms_a_runtime_unmask():
    """The expiry timer's own payload command must ALSO say --runtime -- a
    plain `unmask` there would no-op against a /run mask and leave it stuck
    until reboot, defeating the point of an expiry."""
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log"
        proc = _run(["on", "30m"], log, FAKE_MASKED_COUNT="3")
        assert proc.returncode == 0, proc.stderr
        text = _log_text(log)
        assert "systemd-run" in text and "systemctl unmask --runtime" in text


def test_on_without_duration_masks_persistently():
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log"
        proc = _run(["on"], log, FAKE_MASKED_COUNT="3")
        assert proc.returncode == 0, proc.stderr
        text = _log_text(log)
        assert "systemctl mask sleep.target suspend.target systemd-suspend.service" in text, \
            "the documented indefinite mode must still use the persistent (/etc) mask"
        assert "systemctl mask --runtime sleep.target suspend.target systemd-suspend.service" not in text


# ------------------------------------------------------------------ off ----

def test_off_clears_both_mask_forms():
    """off cannot know which form is in place -- it must clear both, or a
    leftover /etc mask (from an old `on` with no duration, or from before
    this fix) survives an `off` that reports success."""
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log"
        proc = _run(["off"], log, FAKE_MASKED_COUNT="0")
        assert proc.returncode == 0, proc.stderr
        text = _log_text(log)
        assert "systemctl unmask --runtime sleep.target suspend.target systemd-suspend.service" in text
        assert "systemctl unmask sleep.target suspend.target systemd-suspend.service" in text


# --------------------------------------------------------------- status ----
# This is the report that would have saved the days: a mask with no expiry
# armed must say so loudly, and say whether it will clear on its own.

def test_status_reports_armed_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log"
        proc = _run(["status"], log, FAKE_MASKED_COUNT="3", FAKE_IS_ACTIVE="active")
        assert proc.returncode == 0, proc.stderr
        assert "expiry armed" in proc.stdout
        assert "will NOT unmask" not in proc.stdout


def test_status_flags_a_persistent_mask_with_no_timer_as_permanent():
    """The exact broken state from the field report: fully masked, timer
    gone. This must be the loud, unambiguous warning -- it will not clear
    itself, not even across a reboot."""
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log"
        proc = _run(["status"], log,
                    FAKE_MASKED_COUNT="3", FAKE_RUNTIME_COUNT="0", FAKE_IS_ACTIVE="inactive")
        assert proc.returncode == 0, proc.stderr
        assert "will NOT unmask itself" in proc.stdout, \
            f"status did not warn about the orphaned persistent mask:\n{proc.stdout}"


def test_status_flags_a_runtime_mask_with_no_timer_as_self_clearing():
    """Same "no timer armed" shape, but the mask itself is the /run form:
    the phone is still stuck AFK until `off` or a reboot, but a reboot fixes
    it for free -- the status text must say that, not the permanent warning
    above, or an operator would reboot to "fix" a phone that already fixed
    itself, or worse, treat a real orphan as self-healing."""
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log"
        proc = _run(["status"], log,
                    FAKE_MASKED_COUNT="3", FAKE_RUNTIME_COUNT="3", FAKE_IS_ACTIVE="inactive")
        assert proc.returncode == 0, proc.stderr
        assert "clears itself on the next reboot" in proc.stdout, \
            f"status did not distinguish a runtime orphan from a permanent one:\n{proc.stdout}"
        assert "will NOT unmask itself" not in proc.stdout


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
