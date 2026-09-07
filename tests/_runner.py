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
    `make console` greps this output to assert the console suites did NOT skip.
    A format change there would turn that assertion into a silent pass, which is
    the exact failure mode this repo already paid for once (28 findings reached
    CI behind a linter that printed "skipping" and exited 0). So the summary
    line and the FAIL/ERROR shapes are unchanged.

THE ESCAPE HATCH
    PORTHOLE_TEST_JOBS=1 runs serially, in order, in this process. Parallel
    failures interleave and a traceback from a worker is a reconstruction; when
    one is hard to read, run it serially and get the real thing.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor


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


def run(namespace) -> int:
    """The repo's standard runner. Pass `globals()`."""
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
    # Lowercase, and only when there are any. `make console` greps the suite
    # output for uppercase SKIP to assert the console suites did NOT skip
    # wholesale; a per-test skip wearing that word would turn that assertion
    # into a silent pass.
    for msg in skips:
        print(msg)
    tail = ", {} skipped".format(len(skips)) if skips else ""
    print("{}/{} passed{}".format(
        len(tests) - len(failures) - len(skips), len(tests), tail))
    return 1 if failures else 0
