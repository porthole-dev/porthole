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
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
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


def test_nothing_answering_is_absent():
    assert device().state() == "ABSENT"


def test_every_combination_resolves_the_same_as_the_serial_order():
    """Exhaustive, because the concurrent version resolves by precedence and
    the serial one resolved by order of asking. Those must agree everywhere."""
    for fb in (False, True):
        for bid in ("", "abc"):
            for png in (False, True):
                got = device(fastboot=fb, boot_id=bid, ping=png).state()
                want = ("FASTBOOT" if fb else
                        "BOOTED" if bid else
                        "FROZEN" if png else "ABSENT")
                assert got == want, f"fb={fb} boot_id={bid!r} ping={png}: {got}"


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


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
