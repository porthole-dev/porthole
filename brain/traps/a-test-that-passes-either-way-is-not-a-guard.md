---
id: a-test-that-passes-either-way-is-not-a-guard
title: A green suite hides tests that pass whether or not the defect is present
scope: generic
subsystem: testing
severity: trap
confidence: proven
evidence: "2026-09-07, one eleven-task branch (PR #71, 29 commits): EIGHT tests were found to pass with the defect they guard fully present, six of them predating the branch. Each was found by a reviewer asking what would have to break for the assertion to fail; NONE was found by running the suite, which was green throughout. Instances: (1) tests/test_buildroot.py declared test_a_foreign_build_is_named_after_the_package_not_the_arch_value BELOW its `if __name__ == \"__main__\"` block, so main()'s globals() sweep never bound it -- the file reported 19/19 for the repo's entire history and was really 19 of 20; an AST scan of all 42 suites found it the only instance. (2) A latency budget of 8s guarding a regression measured at 5.5s: with the guard defeated the run took 5.59s and the test stayed green. (3) The same test's docstring claimed the 8s absolute ceiling covered its control verb; the control was measured and then checked against no budget at all. (4) tests/test_cli_rules.py asserted `\"new:\" in out` to prove argparse group headings existed -- the pre-change flat help also contained it, in --soc's help text `new: seed from the closest sibling`, so the test was green before and after the work it verified. (5) The hint-column test's two example commands were 15 and 31 characters against a HINT_COLUMN of 34, so neither reached the boundary where `\"{:<N}\".format()` stops inserting a separator and a command glues to its note -- the soc hint rendered as one token ending 'inherit google-taimenthe values worth copying'. (6) tests/test_doctor.py::test_no_device_does_not_open_a_connection_to_the_device called _container_state in-process, so Path.home() was the real HOME; _device_key_authorized returns early at `if not phone or not key.exists()`, so on a clean HOME the guard was inert -- 0 ssh calls even with probe_device forced True -- and make smoke is documented as 'empty HOME' while make floor runs in a container. (7) `[\"build\", \"--json\"]` added to test_json_output_actually_parses passed only because run()'s isolated env has no kernel tree and takes the no-tree preview path; on a host with a tree the verb streams a real build and the assertion fails. (8) The replacement for (6)'s sibling test asserted wall-clock elapsed without setting PORTHOLE_HOST, so PHONE derived to the default gadget address 172.16.42.1 -- where a phone actually answers, a probe with a fake key returns Permission denied in milliseconds and the test passes with the defect present."
first-learned: 2026-09-07
---

**A suite tells you which tests passed. It cannot tell you which ones would
have passed anyway.** Eight times on one branch a test was found to be green
with its own defect fully present. Six predated the work. None was found by
running anything.

Four things this kills, and they are the reason the trap survives: a green
suite does **not** mean the assertions check what they claim; running the tests
is **not** how you find a test that does not work; a test written alongside its
fix does **not** necessarily cover that fix (two of the eight below were
written that way, one of them inside the fix for a finding about exactly this);
and this is **not** a review-quality problem you can out-care — it is a
default that careful people produce.

The question that finds them is not *does it pass*. It is:

> **What would have to break for this assertion to fail, and is that the thing
> it claims to guard?**

Ask it of every assertion you write and every one you review. It takes
seconds and it is the only reliable detector, because every one of the eight
below looked perfectly reasonable in its diff.

## The eight shapes

Each is a real instance. The shape is the reusable part.

**1. Never collected at all.** A `def test_*` placed below
`if __name__ == "__main__": sys.exit(main())`. The runner sweeps `globals()`
at call time, and the function is defined after the sweep runs, so it is never
bound. The file reported `19/19` for the repo's entire history while holding
twenty tests. *Detector:* count `def test_` in the file against the number the
suite prints.

**2. A threshold on the wrong side of the defect.** A latency budget set at 8s
to catch a regression that costs 5.5s. It can never fire. The budget was
widened from 3s to 8s to survive parallel scheduling jitter, and the widening
silently ate the entire signal. *Detector:* compare the threshold to the
measured magnitude of the defect, not to the healthy baseline.

**3. A comment asserting a protection the code does not implement.** The same
test's docstring said its absolute ceiling covered the control verb. The
control was timed and then compared against nothing. A reader trusts the
docstring and stops checking. *Detector:* for every claim in a docstring, find
the line that implements it.

**4. A substring that ordinary prose satisfies.** `assert "new:" in out`,
proving argparse rendered a group heading. The old flat help contained `new:`
too — inside a flag's help text. Green before and after the change it existed
to verify. *Detector:* anchor structural assertions to structure (a line that
IS the heading), never to a substring a sentence could contain.

**5. A boundary test whose examples never reach the boundary.** Both example
commands sat under the column width being tested, so nothing exercised the case
where `"{:<N}".format()` stops inserting a separator and a command runs into
its own note. *Detector:* if a test names a threshold, one case must sit on it
and one past it.

**6. An assertion that only fires on the author's machine.** A guard for "this
must not open ssh" that reached ssh only when `~/.porthole/device_key` already
existed. On a developer's host it worked; on a clean HOME it took an earlier
return and asserted nothing — and `make smoke` is explicitly "empty HOME",
`make floor` a container. The guard was inert on precisely the surfaces CI
uses. *Detector:* list what the assertion depends on in the environment (HOME,
PATH, a device, a checkout) and ask whether CI has it.

**7. An assertion true only inside the test harness.** `build --json` was
asserted parseable; it parsed only because the harness's isolated environment
has no kernel tree, so the verb took a preview path instead of streaming a
real build. *Detector:* run the assertion's subject by hand, outside the
harness, on a configured host.

**8. A timing signal that depends on routing.** Replacing (6)'s sibling with a
wall-clock check, without pinning the address — so the default gadget IP was
used, where an attached phone returns `Permission denied` in milliseconds and
the elapsed check passes with the defect present. *Detector:* prefer a
deterministic discriminator when one exists. Here one did: the row already read
`skip ... not asked` versus `warn ... could not be asked`.

## The cheapest real check

**Break it on purpose and watch it go red.** Delete the line the test guards,
or defeat the flag, run the test, see the failure, put it back. Every one of
the eight would have been caught by that in under a minute.

In this repo, restore with `git checkout -- <file>` and confirm
`git status --short` is clean afterwards — never `git stash`, whose stack is
shared with the main checkout and with other agents' worktrees.

## Why this is a property of the repo, not of one bad week

Six of the eight predated the branch that found them, and they span years of
authorship, several subsystems, and both shell and Python. The suite has been
green over them the whole time. Two more were introduced *by the work fixing
the first six* — one of them inside the fix for a finding about exactly this
class — which is the clearest evidence that reasonable people writing careful
tests produce these by default, and only the question at the top of this note
catches them.

`tests/test_conventions.py` is where mechanical versions of these detectors
belong. It already carries one: a rule that no suite may hand-roll its own
test loop. A second, asserting no `def test_` appears after a file's
`if __name__` block, is six lines and would have closed shape (1) permanently.
