# SPDX-License-Identifier: MIT
"""What this phone can do, tested as two separate questions.

WHY
    `porthole brief` and `porthole next` were only ever as good as their
    probes, and three milestones -- display, suspend, radios -- had NO probe
    at all. They resolved to `unknown` and rested entirely on someone ticking
    a box, which is the exact situation porthole_milestones was written to end.

THE RULE THAT MAKES THIS WORTH BUILDING
    Availability is not function. taimen wifi was "present" for four sessions
    while refusing to associate to the one network that mattered; a matrix
    that ticks wifi because phy0 exists is a matrix that lies. So every
    capability has TWO probes and they are reported separately -- and where
    there is no probe the cell is `?`, which is not partial credit and never
    advances a milestone.

WHY THIS FORMAT AND NOT YAML
    It is two fields. profiles/<codename>/probes.conf already makes this
    argument for `porthole experiment` and it is the same argument: a
    dependency to parse two fields is how a toolbox that runs on a broken host
    stops running.

WHY THE PROBES ARE READ-ONLY
    This verb must be safe to run whenever. Every `works:` probe answers from
    state that already exists -- is wlan0 holding an IP, is hci0 UP -- and
    induces nothing. There is deliberately no --deep tier that suspends the
    device or re-associates a radio: a matrix that can leave the phone
    somewhere you did not find it breaks AGENTS.md section 9's closing rule.
    Where the honest answer is "nobody tested that", the cell says so.
"""
from __future__ import annotations

import collections

# Capabilities every phone has, with probes that hold on any mainline pmOS
# rootfs. A profile overrides any of these in place and may add its own: that
# the venus decoder is /dev/video7 on msm8998 is a device fact, not a
# universal one, and belongs in profiles/<codename>/capabilities.conf.
#
# `works:` is left absent wherever there is no read-only way to tell. That is
# not an omission to be filled in later with something approximate -- an
# invented `works` probe is worse than an honest `?`.
GENERIC = [
    ("wifi", {"present": "iw dev 2>/dev/null | grep -q Interface",
              "works": "ip -4 addr show 2>/dev/null | "
                       "grep -A2 -E '^[0-9]+: wl' | grep -q 'inet '"}),
    ("bluetooth", {"present": "test -d /sys/class/bluetooth/hci0",
                   "works": "hciconfig hci0 2>/dev/null | grep -q 'UP RUNNING'"}),
    ("modem", {"present": "test -d /sys/class/wwan || "
                          "mmcli -L 2>/dev/null | grep -q Modem",
               "works": "mmcli -m any 2>/dev/null | grep -q 'state: registered'"}),
    ("gps", {"present": "test -e /dev/gnss0 || "
                        "mmcli -m any --location-status 2>/dev/null | grep -q gps"}),
    ("nfc", {"present": "test -d /sys/class/nfc/nfc0"}),
    ("display", {"present": "test -e /sys/class/drm/card0",
                 "works": "grep -qx 'connected' "
                          "/sys/class/drm/card0-DSI-1/status 2>/dev/null"}),
    ("touchscreen", {"present": "grep -qi touch /proc/bus/input/devices"}),
    ("audio-out", {"present": "test -e /dev/snd/pcmC0D0p",
                   "works": "grep -q RUNNING /proc/asound/card0/pcm0p/sub0/status "
                            "2>/dev/null || "
                            "aplay -l 2>/dev/null | grep -q '^card '"}),
    ("audio-in", {"present": "test -e /dev/snd/pcmC0D0c",
                  "works": "arecord -l 2>/dev/null | grep -q '^card '"}),
    ("battery", {"present": "ls /sys/class/power_supply/*/capacity >/dev/null 2>&1",
                 "works": "cat /sys/class/power_supply/*/capacity 2>/dev/null | "
                          "grep -qE '^[0-9]+$'"}),
    ("charging", {"present": "ls /sys/class/power_supply/*/type >/dev/null 2>&1",
                  "works": "grep -qxE 'Charging|Full' "
                           "/sys/class/power_supply/*/status 2>/dev/null"}),
    ("suspend", {"present": "grep -q mem /sys/power/state"}),
    ("sensors", {"present": "ls -d /sys/bus/iio/devices/iio:device* "
                            ">/dev/null 2>&1",
                 "works": "cat /sys/bus/iio/devices/iio:device*/in_*_raw "
                          "2>/dev/null | grep -qE '^-?[0-9]+$'"}),
    ("camera", {"present": "ls /dev/video* >/dev/null 2>&1",
                "works": "v4l2-ctl --list-devices 2>/dev/null | grep -qi cam"}),
    ("video-decode", {"present": "v4l2-ctl --list-devices 2>/dev/null | "
                                 "grep -qiE 'venus|vidc|hantro|rkvdec'"}),
]

FIELDS = ("present", "works")


def parse(text: str):
    """`capabilities.conf` -> ordered {name: {present, works}}.

    Format, deliberately the shape probes.conf already uses:

        wifi
          present: iw dev | grep -q Interface
          works:   ip -4 addr show wlan0 | grep -q 'inet '

    A capability with only one field keeps only that field. The missing one
    then renders as `?` rather than as a failure, which is the whole point:
    "nobody wrote a probe" and "the probe said no" are different answers.
    """
    found = collections.OrderedDict()
    current = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            current = line.strip()
            found.setdefault(current, {})
            continue
        if current is None:
            continue
        field, sep, command = line.strip().partition(":")
        if not sep or field.strip() not in FIELDS or not command.strip():
            continue
        found[current][field.strip()] = command.strip()
    return found


def merge(generic, profile):
    """Built-in capabilities, with the profile's overriding IN PLACE.

    In place so the table does not reshuffle between devices: a reader
    comparing two phones' matrices is comparing rows, and rows that move for
    no reason make that harder than it needs to be. A capability the generic
    table does not have is appended.
    """
    merged = collections.OrderedDict((name, dict(fields))
                                     for name, fields in generic)
    for name, fields in (profile or {}).items():
        merged.setdefault(name, {})
        merged[name].update(fields)
    return list(merged.items())
