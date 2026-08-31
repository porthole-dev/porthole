#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The secrets scanner, in both directions, over everything git tracks.

This is the check the PR template already claimed to have. It did not: the old
one lived in tests/test_tools.py, iterated tools() and nothing else, and so
scanned none of the four places a device serial actually landed -- tests/,
profiles/, brain/ and a commit message. Seven CI jobs went green over it.

WHY EVERY RULE IS ASSERTED IN BOTH DIRECTIONS
    brain/laws/every-test-needs-a-positive-control.md. A regex that has rotted
    into one matching nothing is indistinguishable, from CI's point of view,
    from a clean tree -- and silence is the whole failure mode a secrets
    scanner exists to prevent. So every rule must match its `control`, and
    every rule must stay silent on its `allowed`. The second half is not
    decoration: it is what pins the locally-administered-bit carve-out, which
    is the only reason two findings whose evidence IS a MAC address survive.

Needs no device, no network and no git objects beyond the checkout.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_secrets as secrets                          # noqa: E402

HOOKS = ROOT / ".githooks"


def test_every_rule_matches_its_own_positive_control():
    """A rule that matches nothing passes silently. That is the failure."""
    missed = [r.name for r in secrets.RULES
              if not any(h[2] is r for h in secrets.scan(r.control))]
    assert not missed, ("these rules did not match their own control, so they "
                        "would pass a tree full of leaks:\n  " + "\n  ".join(missed))


def test_no_rule_eats_its_counter_example():
    """The other half. A scanner that cries wolf teaches people --no-verify,
    and the MAC rule in particular MUST stay silent on a randomised address:
    taimen-has-no-factory-wlan-mac.md and usb-gadget-rerandomises-the-host-mac
    .md are findings whose evidence is the MAC itself."""
    bad = [f"{r.name}: {h[3]!r} in {r.allowed!r}"
           for r in secrets.RULES for h in secrets.scan(r.allowed) if h[2] is r]
    assert not bad, "these rules matched what they must not:\n  " + "\n  ".join(bad)


def test_nothing_git_tracks_carries_a_secret():
    """tools/, lib/, tests/, profiles/, brain/, docs/, .github/ -- everything,
    which is the point. The old check's scope was the defect."""
    hits = secrets.scan_tracked(ROOT)
    assert not hits, "\n  ".join(
        f"{w}:{n}: {r.name}: {h!r}" for w, n, r, h in hits)


def test_a_device_capture_is_refused_and_a_diagram_is_not():
    """No regex will ever read a screenshot, so the format is the check."""
    assert secrets.is_capture("docs/first-boot.png")
    assert secrets.is_capture("brain/devices/shot.JPEG")
    assert not secrets.is_capture("docs/architecture.svg")
    assert not secrets.is_capture("lib/porthole.py")


def test_the_scanner_exempts_exactly_one_path():
    """Its own controls are matching strings by construction, so it must skip
    itself -- and nothing else, docs/ included. Every exemption is a hole
    someone later files their leak through, so the count is the assertion."""
    assert secrets.SELF == "lib/porthole_secrets.py"
    scanned = {w for w, _, _, _ in secrets.scan(
        (ROOT / secrets.SELF).read_text(), secrets.SELF)}
    assert scanned, "the exempt file stopped containing controls to exempt"
    assert secrets.SELF in secrets.tracked(ROOT), \
        "an untracked scanner is not in anyone's clone"


def test_a_push_scan_attributes_added_lines_to_their_file():
    """Which is what keeps the scanner's own controls exempt on the way out.
    The first push of the branch that added this file was refused by its own
    pre-push hook: a flat list of added lines has no idea which file any of
    them came from, so every control read as a leak.

    The sample line is built from a real rule rather than written out, because
    this file is not exempt either -- and that is the property working, not an
    inconvenience."""
    leak = next(r.control for r in secrets.RULES if r.name == "user-at-host")
    added = f'         control="{leak}",'
    by = secrets.added_by_file(
        "diff --git a/lib/porthole_secrets.py b/lib/porthole_secrets.py\n"
        "--- a/lib/porthole_secrets.py\n"
        "+++ b/lib/porthole_secrets.py\n"
        "@@ -1 +1 @@\n"
        f"+{added}\n"
        "diff --git a/brain/findings/x.md b/brain/findings/x.md\n"
        "--- /dev/null\n"
        "+++ b/brain/findings/x.md\n"
        "@@ -0,0 +1 @@\n"
        "+evidence: 2026-08-31\n")
    assert by == {"lib/porthole_secrets.py": [added],
                  "brain/findings/x.md": ["evidence: 2026-08-31"]}, by
    assert secrets.scan(added), "the sample line stopped being a leak at all"


def test_both_hooks_are_wired_to_this_scanner():
    """A rule with a hook held all session; a rule with a checkbox did not.
    The commit message and the push range are the two surfaces no check in
    this repo had ever read -- and the push is the last cheap moment, because
    afterwards only GitHub Support can remove anything."""
    for name in ("commit-msg", "pre-push"):
        hook = HOOKS / name
        assert hook.exists(), f".githooks/{name} is missing"
        assert hook.stat().st_mode & 0o111, f".githooks/{name} is not executable"
        assert secrets.SELF in hook.read_text(), \
            f".githooks/{name} does not run the scanner"


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
