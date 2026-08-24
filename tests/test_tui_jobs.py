#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The job runner: streaming, ring-buffered, cancellable.

asyncio only -- no textual, so these run wherever python 3.8 does. Fixtures are
`sh -c`, not porthole verbs, so the tests need no device and no config.
"""
import asyncio
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from porthole_tui import jobs  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_lines_arrive_before_the_process_exits():
    async def go():
        mgr = jobs.JobManager(ROOT)
        seen = []
        job = mgr.spawn("sh -c 'echo a; sleep 0.5; echo b'", on_line=seen.append)
        await asyncio.sleep(0.25)
        mid = list(seen)
        await job.wait()
        return mid, seen, job

    mid, seen, job = run(go())
    assert mid == ["a"], "streaming, not buffered: got {!r}".format(mid)
    assert seen == ["a", "b"], seen
    assert job.rc == 0


def test_stderr_is_interleaved_not_lost():
    async def go():
        mgr = jobs.JobManager(ROOT)
        job = mgr.spawn("sh -c 'echo out; echo err >&2'")
        await job.wait()
        return job

    job = run(go())
    assert set(job.lines) == {"out", "err"}, list(job.lines)


def test_a_nonzero_exit_is_recorded_not_raised():
    async def go():
        mgr = jobs.JobManager(ROOT)
        job = mgr.spawn("sh -c 'exit 76'")
        await job.wait()
        return job

    job = run(go())
    assert job.rc == 76
    assert job.state == "done"


def test_cancel_stops_it_and_says_so():
    async def go():
        mgr = jobs.JobManager(ROOT)
        job = mgr.spawn("sh -c 'sleep 30'")
        await asyncio.sleep(0.25)
        job.cancel()
        await job.wait()
        return job

    started = time.monotonic()
    job = run(go())
    assert time.monotonic() - started < 15, "cancel must not wait out the sleep"
    assert job.state == "cancelled"


def test_the_ring_buffer_is_capped():
    async def go():
        mgr = jobs.JobManager(ROOT, max_lines=50)
        job = mgr.spawn(
            "sh -c 'i=0; while [ $i -lt 400 ]; do echo $i; i=$((i+1)); done'")
        await job.wait()
        return job

    job = run(go())
    assert len(job.lines) == 50, len(job.lines)
    assert list(job.lines)[-1] == "399", \
        "the cap must drop the OLDEST, not the newest"


def test_history_keeps_finished_jobs():
    async def go():
        mgr = jobs.JobManager(ROOT)
        await mgr.spawn("sh -c 'echo one'").wait()
        await mgr.spawn("sh -c 'echo two'").wait()
        return mgr

    mgr = run(go())
    assert len(mgr.jobs) == 2
    assert mgr.running == []


def test_a_command_that_cannot_start_is_a_recorded_failure():
    async def go():
        mgr = jobs.JobManager(ROOT)
        job = mgr.spawn("definitely-not-a-real-binary-xyz")
        await job.wait()
        return job

    job = run(go())
    assert job.rc == 127
    assert any("could not run it" in line for line in job.lines), list(job.lines)


def test_duration_is_recorded():
    async def go():
        mgr = jobs.JobManager(ROOT)
        job = mgr.spawn("sh -c 'sleep 0.2'")
        await job.wait()
        return job

    job = run(go())
    assert 0.1 < job.duration() < 5.0, job.duration()


def test_a_tilde_path_is_expanded_before_exec():
    # exec has no shell. Without expansion the tool receives a literal "~" and fails.
    import os

    async def go():
        mgr = jobs.JobManager(ROOT)
        marker = os.path.expanduser("~")
        job = mgr.spawn("sh -c 'echo $0' ~/porthole-tilde-probe")
        await job.wait()
        return job, marker

    job, marker = run(go())
    assert job.lines, list(job.lines)
    assert list(job.lines)[0].startswith(marker), \
        "argv[1] should be expanded, got {!r}".format(list(job.lines)[0])
    assert "~" not in list(job.lines)[0]


def test_progress_output_with_no_newline_does_not_hang_the_job():
    """fastboot, dd, wget and pmbootstrap all draw progress with carriage returns and
    emit no newline until the end. readline() raised past its 64KiB buffer, the finish
    bookkeeping was skipped, and job.wait() blocked forever while the process had already
    exited -- with JobManager.running still reporting it as live."""
    async def go():
        mgr = jobs.JobManager(ROOT)
        job = mgr.spawn("sh -c 'i=0; while [ $i -lt 9000 ]; do "
                        "printf \"progress %d of 9000\\r\" $i; i=$((i+1)); done; echo done'")
        return await asyncio.wait_for(job.wait(), timeout=20), job

    rc, job = run(go())
    assert rc == 0, rc
    assert job.state == "done"
    assert list(job.lines)[-1] == "done", list(job.lines)[-3:]
    assert len(job.lines) > 100, "carriage returns must become separate lines"


def test_a_single_enormous_line_is_chunked_not_fatal():
    async def go():
        mgr = jobs.JobManager(ROOT)
        job = mgr.spawn("sh -c 'printf \"%0100000d\" 7'")
        return await asyncio.wait_for(job.wait(), timeout=20), job

    rc, job = run(go())
    assert rc == 0
    assert job.state == "done"
    assert job.lines, "output must not be lost"


def test_is_interactive_assumes_interactive_when_the_registry_is_unreadable():
    # Piping a termios-driven command is unrecoverable; an unnecessary suspend is not.
    import porthole_cli
    original = porthole_cli.discover
    porthole_cli.discover = lambda root: (_ for _ in ()).throw(RuntimeError("registry"))
    try:
        assert jobs.is_interactive(ROOT, "porthole serial console") is True
    finally:
        porthole_cli.discover = original


def test_is_interactive_is_false_for_a_non_porthole_command():
    assert jobs.is_interactive(ROOT, "sh -c true") is False


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL {}:\n  {}".format(name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("ERROR {}: {}: {}".format(name, type(exc).__name__, exc))
    print("{}/{} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
