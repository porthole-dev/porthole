#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The shared test runner: same output as the loop it replaces, run in parallel.

WHY THIS EXISTS
    The suite took 73s, and 63s of that was two files. test_tui_screens.py
    alone was 45s -- 80 tests, each building a Textual app -- run one after
    another by a hand-copied serial loop. The tests are independent and the
    machine has 16 cores; the only thing making it serial was the loop.

    Parallelising ACROSS files cannot fix it: the wall clock then floors at the
    slowest single file, which was measured at 45.7s. The concurrency has to be
    inside the file, which is what this is.

OUTPUT IS BYTE-COMPATIBLE, DELIBERATELY
    `Makefile`'s `floor` target and `tests/ci-local.sh` both read only the last
    line a suite prints (`tail -1`). A format change there is a silent parse
    failure, which is the exact failure mode this repo already paid for once
    (28 findings reached CI behind a linter that printed "skipping" and exited
    0). So the summary line and the FAIL/ERROR shapes are unchanged.

THE ESCAPE HATCH
    PORTHOLE_TEST_JOBS=1 runs serially, in order, in this process. Parallel
    failures interleave and a traceback from a worker is a reconstruction; when
    one is hard to read, run it serially and get the real thing.
"""
from __future__ import annotations

import atexit
import multiprocessing
import os
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor


def _own_tmp() -> None:
    """Give the whole suite ONE temp root, and remove it when the run ends.

    There are ~100 `tempfile.mkdtemp()` call sites across tests/ and almost
    none of them clean up. Measured 2026-09-09: **20531 abandoned directories
    in /tmp holding 572 MB**, one fresh batch per `make test` since the suite
    was written. On the reference host /tmp is a 20 GiB tmpfs -- so that is
    RAM, and it is the same tmpfs the suite itself needs to write into. It
    filled, and `test_ph_build.sh` then reported 29 failures whose errors named
    no cause (`echo: write error: Disk quota exceeded`). A test suite that
    leaks is one that eventually fails itself and blames something else.

    Fixed HERE rather than at the hundred call sites, because `mkdtemp()`
    honours TMPDIR: one root covers every existing call and every future one,
    with no way for a new test to forget. Both `tempfile.tempdir` and the
    environment variable are set -- the first for this process (tempfile
    caches its answer after the first call, so assigning the env var alone can
    be too late), the second so subprocesses land in the same place.

    Imported before the suites'' own module-level mkdtemp calls, which is what
    makes those covered too: `import _runner` sits above them.
    """
    keep = os.environ.get("PORTHOLE_KEEP_TMP")
    root = tempfile.mkdtemp(prefix="porthole-run-")
    tempfile.tempdir = root
    os.environ["TMPDIR"] = root
    if keep:
        # A failing test''s scratch directory is often the thing you want to
        # look at. Opting out prints where it is rather than silently hoarding.
        print("PORTHOLE_KEEP_TMP: temp root kept at {}".format(root))
        return
    owner = os.getpid()

    def _sweep():
        # Only the process that MADE it removes it. The pool below forks, and
        # a worker running this on its own exit would delete the root out from
        # under its siblings mid-run.
        if os.getpid() == owner:
            shutil.rmtree(root, ignore_errors=True)

    atexit.register(_sweep)


_own_tmp()


def jobs() -> int:
    """How many workers. 0/unset means one per core; 1 means serial."""
    try:
        n = int(os.environ.get("PORTHOLE_TEST_JOBS", "0"))
    except ValueError:
        n = 0
    return n if n > 0 else (os.cpu_count() or 1)


def _invoke(name: str):
    """Run ONE test, by name, in the worker. Returns (name, status, message).

    By name rather than by function object: the pool would otherwise have to
    ship a callable defined in __main__, and how that is shipped depends on
    the start method. Looking the name up in the child's own __main__ works
    the same way whichever method is in use.

    `Skip` is looked up the same way rather than passed in, for the same
    reason -- it is the CALLER's class, defined in the caller's __main__, and
    a fork already has it. A suite with no Skip class simply has none, and
    every exception stays an error.
    """
    main = sys.modules["__main__"]
    skip = getattr(main, "Skip", None)
    fn = getattr(main, name)
    try:
        fn()
    except AssertionError as exc:
        return name, "fail", "FAIL {}:\n  {}".format(name, exc)
    except Exception as exc:  # noqa: BLE001
        if skip is not None and isinstance(exc, skip):
            return name, "skip", "  skip {}: {}".format(name, exc)
        return name, "fail", "ERROR {}: {}: {}".format(
            name, type(exc).__name__, exc)
    return name, "pass", None


def run(namespace, suffix: str = "") -> int:
    """The repo's standard runner. Pass `globals()`.

    `suffix`, if given, is folded onto the summary line rather than printed
    separately -- two `tail -1` consumers (`Makefile`'s `floor` target and
    `tests/ci-local.sh`'s smoke check) read only the LAST line a suite prints,
    and before this runner existed that line already carried both the tally
    and a suite-specific count (e.g. "18/18 passed (150 tools checked)"). A
    suffix printed on its own line would still be true, but invisible to
    those two callers -- so it goes on this line instead, byte-compatible
    with what those suites printed before.
    """
    tests = [n for n, f in sorted(namespace.items())
             if n.startswith("test_") and callable(f)]

    workers = jobs()
    if workers == 1 or len(tests) < 2:
        results = [_invoke(n) for n in tests]
    else:
        # `fork` is pinned, not left to the default. Python 3.14 changed the
        # Linux default to `forkserver`, which re-imports __main__ in the
        # child -- and __main__ here is the test module, so it would re-execute
        # the whole file per worker. Under fork the child already has the
        # parent's __main__, which is what _invoke looks up.
        ctx = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(max_workers=min(workers, len(tests)),
                                 mp_context=ctx) as pool:
            # map keeps input order, so failures print in test order no matter
            # which worker finished first.
            results = list(pool.map(_invoke, tests))

    failures = [msg for _n, st, msg in results if st == "fail"]
    skips = [msg for _n, st, msg in results if st == "skip"]
    for msg in failures:
        print(msg)
    # Lowercase, and only when there are any. Uppercase SKIP is reserved for
    # "this whole file skipped"; a per-test skip wearing that word would read
    # as the wrong kind of skip to anything that greps for it.
    for msg in skips:
        print(msg)
    tail = ", {} skipped".format(len(skips)) if skips else ""
    # Skip count first, suffix last: the tally and the skip count are both
    # about which tests ran, the suffix is a different axis (what the suite
    # found), so it reads as the trailing annotation on the line.
    head = "{}/{} passed{}".format(
        len(tests) - len(failures) - len(skips), len(tests), tail)
    print("{} {}".format(head, suffix) if suffix else head)
    return 1 if failures else 0
