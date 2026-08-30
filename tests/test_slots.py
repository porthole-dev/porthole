#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole slots probe` -- slot policy read from the device, never guessed.

The redfin report stopped exactly here: the slot-policy milestone hinted at
`porthole new-device <codename> --from-fastboot`, a device-CREATION verb, for
a profile that already existed. Its author did the right thing and left every
slot value unset rather than inventing one, which is the behaviour this verb
has to preserve: guessing an active slot is how a port bricks a phone.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

import porthole_cmd_slots as slots  # noqa: E402

GETVAR = """\
(bootloader) slot-count: 2
(bootloader) current-slot: a
(bootloader) slot-successful:a: no
(bootloader) slot-successful:b: yes
(bootloader) slot-unbootable:a: no
(bootloader) slot-unbootable:b: no
(bootloader) slot-retry-count:a: 6
all: Done!!
"""


def test_getvar_lines_are_parsed():
    found = slots.parse_getvar(GETVAR)
    assert found["slot-count"] == "2"
    assert found["current-slot"] == "a"
    assert found["slot-successful:b"] == "yes"


def test_an_ab_device_is_recognised():
    policy = slots.slot_policy(slots.parse_getvar(GETVAR))
    assert policy["PORTHOLE_HAS_AB_SLOTS"] == "1"
    assert policy["PORTHOLE_ACTIVE_SLOT"] == "a"


def test_a_single_slot_device_is_recognised():
    policy = slots.slot_policy({"slot-count": "1"})
    assert policy["PORTHOLE_HAS_AB_SLOTS"] == "0"
    assert "PORTHOLE_ACTIVE_SLOT" not in policy


def test_a_value_the_device_did_not_report_is_left_unset():
    """The whole point. A field fastboot did not answer stays absent, and the
    caller is told it is absent -- guessing an active slot bricks phones."""
    policy = slots.slot_policy({"slot-count": "2"})
    assert "PORTHOLE_ACTIVE_SLOT" not in policy
    assert policy["PORTHOLE_HAS_AB_SLOTS"] == "1"


def test_an_unbootable_slot_is_recorded_as_forbidden():
    policy = slots.slot_policy(slots.parse_getvar(
        GETVAR.replace("slot-unbootable:b: no", "slot-unbootable:b: yes")))
    assert policy["PORTHOLE_SLOT_FORBIDDEN"] == "b"


def test_garbage_is_not_mistaken_for_an_answer():
    assert slots.parse_getvar("") == {}
    assert slots.slot_policy({}) == {}


def test_updating_a_profile_preserves_the_comment_that_explains_the_value():
    """`PORTHOLE_SLOT_FORBIDDEN="a"   # slot a has no good image` -- the
    comment is why the value is what it is. Rewriting the file wholesale
    would drop it."""
    before = ('PORTHOLE_ACTIVE_SLOT="b"\n'
              'PORTHOLE_SLOT_FORBIDDEN="a"   # no good image on a\n')
    after = slots.update_env(before, {"PORTHOLE_ACTIVE_SLOT": "a"})
    assert 'PORTHOLE_ACTIVE_SLOT="a"' in after
    assert "# no good image on a" in after


def test_a_new_key_is_appended_rather_than_lost():
    after = slots.update_env("PORTHOLE_ACTIVE_SLOT=\"b\"\n",
                             {"PORTHOLE_HAS_AB_SLOTS": "1"})
    assert 'PORTHOLE_HAS_AB_SLOTS="1"' in after


def test_no_device_is_reported_immediately_rather_than_after_a_timeout():
    """`fastboot getvar all` BLOCKS with no device attached, so probing it
    first made the verb appear to hang for a minute before admitting there
    was no phone. `fastboot devices` answers instantly, and the taimen profile
    records it as the only check that discriminates a real fastboot device."""
    import inspect

    source = inspect.getsource(slots._probe)
    assert source.index('"devices"') < source.index('"getvar"'), \
        "getvar is reached before the devices check"


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
