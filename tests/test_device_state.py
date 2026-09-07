#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Device state: the precedence, the cache boundary, and the stall retry.

`Device.state()` is the most safety-relevant function in the toolkit: every
tool that touches the device asks it first, and a wrong answer means acting on
a phone that is in a different state than you believe.

It was made concurrent because `porthole brief` -- the command AGENTS.md tells
every agent to run first -- spent 6.3s of its 6.4s waiting on three serial
probes against a device that was not plugged in. Concurrency must not have
changed a single verdict, so every combination is pinned here.
"""
import json
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

import porthole  # noqa: E402


def device(**probes):
    """A Device whose three probes return what the test says."""
    cfg = porthole.load_config(root=ROOT, env={"PORTHOLE_DEVICE": "google-taimen",
                                               "TK_DEVICE_STATE": ""})
    dev = porthole.Device(cfg)
    dev.in_fastboot = lambda: probes.get("fastboot", False)
    dev._boot_id = lambda retry="always": probes.get("boot_id", "")
    dev.boot_id = lambda: probes.get("boot_id", "")
    dev._pings = lambda: probes.get("ping", False)
    dev._in_initramfs = lambda: probes.get("initramfs", False)
    dev._remember_state = lambda verdict: None
    return dev


# ------------------------------------------------------------- precedence --

def test_fastboot_wins_over_everything():
    """Hardware forces it: a device in the bootloader has no USB network, so
    anything else answering at the same time is answering about something
    else. Running the probes concurrently must not change this."""
    dev = device(fastboot=True, boot_id="abc", ping=True)
    assert dev.state() == "FASTBOOT"


def test_boot_id_means_booted():
    assert device(boot_id="abc", ping=True).state() == "BOOTED"


def test_ping_without_ssh_is_frozen():
    """FROZEN is kernel alive, userspace gone -- a real and distinct state."""
    assert device(ping=True).state() == "FROZEN"


def test_the_initramfs_shell_is_not_frozen():
    """Both ping and neither answers ssh, but one of them has a shell on :23
    that will say why the root did not mount. Collapsing them sends whoever
    reads the verdict looking for a cable instead of for tools/tsh.py."""
    assert device(ping=True, initramfs=True).state() == "INITRAMFS"


def test_booted_still_wins_over_the_initramfs_shell():
    """A booted device may run its own telnetd; ssh answering is the stronger
    statement and must not be overridden by a port probe."""
    assert device(boot_id="abc", ping=True, initramfs=True).state() == "BOOTED"


def test_nothing_answering_is_absent():
    assert device().state() == "ABSENT"


def test_every_combination_resolves_the_same_as_the_serial_order():
    """Exhaustive, because the concurrent version resolves by precedence and
    the serial one resolved by order of asking. Those must agree everywhere."""
    for fb in (False, True):
        for bid in ("", "abc"):
            for ird in (False, True):
                for png in (False, True):
                    got = device(fastboot=fb, boot_id=bid,
                                 initramfs=ird, ping=png).state()
                    want = ("FASTBOOT" if fb else
                            "BOOTED" if bid else
                            "INITRAMFS" if ird else
                            "FROZEN" if png else "ABSENT")
                    assert got == want, (
                        f"fb={fb} boot_id={bid!r} initramfs={ird} ping={png}: {got}")


def test_a_forced_state_still_short_circuits_everything():
    """TK_DEVICE_STATE is how a human says "I know what this is"."""
    cfg = porthole.load_config(root=ROOT, env={"PORTHOLE_DEVICE": "google-taimen",
                                               "TK_DEVICE_STATE": "BOOTED"})
    dev = porthole.Device(cfg)
    dev.in_fastboot = lambda: (_ for _ in ()).throw(AssertionError("probed anyway"))
    assert dev.state() == "BOOTED"


def test_a_probe_that_raises_counts_as_a_no():
    """A missing `ping` binary took down `porthole doctor` once. One probe
    exploding must degrade the verdict, never the command."""
    dev = device(boot_id="abc")
    dev._pings = lambda: (_ for _ in ()).throw(FileNotFoundError("no ping"))
    assert dev.state() == "BOOTED"
    dev2 = device()
    dev2.in_fastboot = lambda: (_ for _ in ()).throw(OSError("no fastboot"))
    assert dev2.state() == "ABSENT"


# ------------------------------------------------------------------ cache --

def test_acting_callers_never_get_a_cached_verdict():
    """The default is max_age=0. A cached "BOOTED" handed to something that
    then flashes is exactly the stale reading brain/laws forbids."""
    with tempfile.TemporaryDirectory() as tmp:
        import os
        os.environ["XDG_CACHE_HOME"] = tmp
        dev = device(boot_id="abc")
        dev._remember_state = porthole.Device._remember_state.__get__(dev)
        assert dev.state() == "BOOTED"
        # The device goes away; a caller that will ACT must see that.
        gone = device()
        gone._remember_state = lambda v: None
        assert gone.state() == "ABSENT", "an acting caller got a stale verdict"


def test_a_display_caller_may_reuse_a_recent_verdict():
    with tempfile.TemporaryDirectory() as tmp:
        import os
        os.environ["XDG_CACHE_HOME"] = tmp
        dev = device(boot_id="abc")
        dev._remember_state = porthole.Device._remember_state.__get__(dev)
        assert dev.state() == "BOOTED"
        gone = device()
        gone._remember_state = lambda v: None
        assert gone.state(max_age=60) == "BOOTED", "the cache was not used"
        assert gone.state(max_age=0) == "ABSENT", "max_age=0 must always probe"


def test_an_expired_verdict_is_not_used():
    with tempfile.TemporaryDirectory() as tmp:
        import os
        os.environ["XDG_CACHE_HOME"] = tmp
        dev = device(boot_id="abc")
        dev._remember_state = porthole.Device._remember_state.__get__(dev)
        dev.state()
        path = dev._state_cache()
        blob = json.loads(path.read_text())
        blob["at"] = time.time() - 3600
        path.write_text(json.dumps(blob))
        gone = device()
        gone._remember_state = lambda v: None
        assert gone.state(max_age=30) == "ABSENT"


def test_a_corrupt_cache_is_ignored_rather_than_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        import os
        os.environ["XDG_CACHE_HOME"] = tmp
        dev = device(boot_id="abc")
        path = dev._state_cache()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert dev.state(max_age=60) == "BOOTED"


# ------------------------------------------------------------------ retry --

def test_the_stall_retry_still_fires_when_something_stalls():
    """The retry is load-bearing: taimen 2026-08-19, sshd taking ~7.9s to
    answer, a 6s timeout ate a baseline and a 20-cycle test reported BOOT_ID
    CHANGED against a phone that had not rebooted."""
    cfg = porthole.load_config(root=ROOT, env={"PORTHOLE_DEVICE": "google-taimen"})
    dev = porthole.Device(cfg)
    calls = []

    def slow(cmd, timeout=None):
        calls.append(timeout)
        time.sleep(0.02)
        return "" if len(calls) == 1 else "boot-id-here"

    dev.run = slow
    # Patch the clock so attempt one looks like it consumed its timeout.
    real = time.monotonic
    seq = iter([0.0, 11.0, 11.0, 22.0])
    time.monotonic = lambda: next(seq, 22.0)
    try:
        got = dev._boot_id(retry="stall-only")
    finally:
        time.monotonic = real
    assert got == "boot-id-here", "the stall retry did not fire"
    assert len(calls) == 2


def test_a_fast_failure_is_not_retried():
    """A refused connection comes back in milliseconds and is not the PAM
    stall. Asking twice bought two seconds of a user staring at a prompt."""
    cfg = porthole.load_config(root=ROOT, env={"PORTHOLE_DEVICE": "google-taimen"})
    dev = porthole.Device(cfg)
    calls = []
    dev.run = lambda cmd, timeout=None: calls.append(1) or ""
    assert dev._boot_id(retry="stall-only") == ""
    assert len(calls) == 1, "a fast failure was retried anyway"


def test_the_default_still_retries_unconditionally():
    """boot_id() is also used for BASELINES, where an empty answer must never
    be mistaken for a change. That caller keeps both attempts."""
    cfg = porthole.load_config(root=ROOT, env={"PORTHOLE_DEVICE": "google-taimen"})
    dev = porthole.Device(cfg)
    calls = []
    dev.run = lambda cmd, timeout=None: calls.append(1) or ""
    assert dev.boot_id() == ""
    assert len(calls) == 2, "the baseline retry was weakened"


def test_cached_state_returns_the_verdict_and_its_age():
    import json
    import tempfile
    import time
    import porthole

    home = pathlib.Path(tempfile.mkdtemp(prefix="porthole-cache-"))
    os.environ["XDG_CACHE_HOME"] = str(home)
    dev = porthole.Device({"HOST": "10.0.0.1"})
    path = dev._state_cache()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"state": "BOOTED", "at": time.time() - 62, "host": "10.0.0.1"}))

    got = dev.cached_state(300)
    assert got is not None, "a 62s-old cache is inside a 300s window"
    state, age = got
    assert state == "BOOTED", state
    assert 60 <= age <= 70, age


def test_cached_state_is_none_past_the_window():
    import json
    import tempfile
    import time
    import porthole

    home = pathlib.Path(tempfile.mkdtemp(prefix="porthole-cache-"))
    os.environ["XDG_CACHE_HOME"] = str(home)
    dev = porthole.Device({"HOST": "10.0.0.2"})
    path = dev._state_cache()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"state": "BOOTED", "at": time.time() - 900, "host": "10.0.0.2"}))

    assert dev.cached_state(300) is None


def test_reachable_verdict_from_a_fresh_cache_is_done_and_names_the_age():
    import porthole_milestones as ms

    verdict = ms.reachable_verdict(forced="", cached=("BOOTED", 62.0))
    assert verdict.state == ms.DONE, verdict
    assert "62s ago" in verdict.evidence, verdict.evidence
    # The whole defect: it must NOT tell you to run the command that answered.
    assert "porthole doctor" not in verdict.evidence, verdict.evidence


def test_reachable_verdict_with_no_cache_names_doctor():
    import porthole_milestones as ms

    verdict = ms.reachable_verdict(forced="", cached=None)
    assert verdict.state == ms.BLOCKED, verdict
    assert "porthole doctor" in verdict.evidence, verdict.evidence


def test_reachable_verdict_reports_a_cached_absent_device_as_blocked():
    import porthole_milestones as ms

    verdict = ms.reachable_verdict(forced="", cached=("ABSENT", 30.0))
    assert verdict.state == ms.BLOCKED, verdict
    assert "ABSENT" in verdict.evidence, verdict.evidence


def test_a_forced_state_still_outranks_the_cache():
    """TK_DEVICE_STATE is an assertion, and it keeps winning.

    A human who exported it is claiming something; the change here is only
    that the ABSENCE of that claim is no longer read as ignorance.
    """
    import porthole_milestones as ms

    verdict = ms.reachable_verdict(forced="BOOTED", cached=("ABSENT", 5.0))
    assert verdict.state == ms.DONE, verdict
    assert "asserted" in verdict.evidence, verdict.evidence


def main():
    return _runner.run(globals())


# ------------------------------------------- a tool that could not run --
#
# The shell side learned this the hard way (ph_need_fastboot, tools/ph-lib.sh).
# These pin the Python twin: `wait_fastboot` polls until its deadline, so a
# $FASTBOOT that cannot run made it insist for a full budget that a phone
# sitting in the bootloader had never arrived.


def _real_probe(dev, path):
    """`dev` with the REAL in_fastboot, pointed at `path`."""
    dev.fastboot = path
    dev.in_fastboot = porthole.Device.in_fastboot.__get__(dev)
    return dev


def test_a_fastboot_that_cannot_run_refuses_instead_of_saying_no():
    dev = _real_probe(device(), "/nonexistent/fastboot")
    try:
        dev.in_fastboot()
    except porthole.FastbootUnavailable as exc:
        assert "/nonexistent/fastboot" in str(exc), exc
        assert "not the phone" in str(exc), exc
    else:
        raise AssertionError("answered 'no' for a tool that never ran")


def test_wait_fastboot_refuses_at_once_rather_than_polling_its_budget():
    """The 181.2s failure, in one assertion."""
    dev = _real_probe(device(), "/nonexistent/fastboot")
    dev.poll = 0.01
    start = time.monotonic()
    try:
        dev.wait_fastboot(time.monotonic() + 5)
    except porthole.FastbootUnavailable:
        pass
    else:
        raise AssertionError("polled its whole budget instead of refusing")
    spent = time.monotonic() - start
    assert spent < 1.0, f"took {spent:.1f}s -- it polled"


def test_a_fastboot_that_RAN_and_found_nothing_is_still_a_real_no():
    """The positive control: the guard must not swallow a genuine answer."""
    dev = _real_probe(device(), "/bin/true")
    assert dev.in_fastboot() is False


if __name__ == "__main__":
    sys.exit(main())
