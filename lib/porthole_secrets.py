#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What must never be published, written as patterns instead of as a sentence.

docs/HANDOFF-contribution-rules.md section 4.5 decides the list. This is the
half of it that runs, and it exists because the prose half already failed: a
device serial reached a public branch, a public commit message, a public test
fixture and a public review comment, past a PR checklist that promised "no
personal paths, usernames or IPs (the tests enforce this)" and seven green CI
checks. The check it advertised iterated tools() only. It matched neither the
serial nor any of the four places the serial landed.

MATCH THE LABEL, NOT THE VALUE
    `git grep -IoE "\\b[0-9A-Za-z]{12,20}\\b"` returns 1889 hits in this repo --
    register dumps, apk checksums, commit hashes. A shape check for "a serial"
    is unshippable and always will be. But pasted probe output arrives with its
    label attached, every time: `serialno:`, a `fastboot devices` line, `imei:`.
    Those are what the rules below match, at a false-positive cost of zero.

EVERY RULE CARRIES BOTH CONTROLS
    brain/laws/every-test-needs-a-positive-control.md. A secrets scanner that
    matches nothing passes silently, which is the exact failure mode it exists
    to prevent -- so `control` must match and `allowed` must not, and
    tests/test_secrets.py asserts both directions for every rule. The pair is
    what stops a regex rotting into one that matches nothing and stays green.

THIS FILE IS THE ONLY EXEMPT PATH
    Its controls are matching strings by construction. Nothing else is exempt,
    docs/ included -- which is why the examples in section 4.5 are written as
    `<user>@<ipv4>` rather than in a real-looking form. Every exemption is a
    hole someone later files their leak through.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

SELF = "lib/porthole_secrets.py"

# A screenshot of a booted phone carries a wallpaper, notifications and an
# account name, and no regex will ever read one. Banning the formats the
# capture tools emit is exact, and costs nothing: zero are tracked today. SVG
# is deliberately absent -- a diagram is not a photograph of someone's device.
RASTER = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".pcap")

# What both path rules actually protect is the LOGIN, not the path or the
# address: section 4.5's principle is "ties an artifact to one person", and
# `172.16.42.1` is a documented default either way. So a stand-in name is fine
# and a real one is not, which needs a closed set of the stand-ins this tree
# uses. `pmos` is in it for the same reason the gadget IP is exempt -- it is a
# fixed account inside the pmbootstrap chroot, not anyone's desk.
#
# The set being closed is the safety property. Adding your own login to it is
# a visible, reviewable line in a diff, which is exactly what the sentence
# version of this rule never made anyone do.
PLACEHOLDER_NAMES = frozenset(
    "pmos user olduser someone you ci alice bob x".split())


class Rule:
    def __init__(self, name, pattern, why, control, allowed, flags=0, ok=()):
        self.name = name
        self.re = re.compile(pattern, flags)
        self.why = why
        self.control = control      # MUST match -- the leak this rule is for
        self.allowed = allowed      # MUST NOT match -- the thing it must not eat
        self.ok = frozenset(ok)     # literal values that are stand-ins


RULES = [
    Rule("host-path",
         r"/(?:var/)?home/(?P<v>[a-z][a-z0-9_-]*)",
         "a home directory names a person and makes a tool work on one desk",
         control="TREE=/home/jdoe/src/linux",
         allowed="TREE=/home/<user>/src/linux",
         ok=PLACEHOLDER_NAMES),

    Rule("user-at-host",
         r"\b(?P<v>[a-z][a-z0-9_-]*)@\d{1,3}(?:\.\d{1,3}){3}\b",
         "a login and an address together are someone's actual machine",
         control="ssh jdoe@192.0.2.15",
         allowed="ssh <user>@172.16.42.1",
         ok=PLACEHOLDER_NAMES),

    # The leak that produced all of this. `porthole serial` (the UART verb) and
    # PORTHOLE_SERIAL_BAUD both survive it: the label must be followed by a
    # separator and a value before anything is reported.
    Rule("device-serial",
         r"(?i)\bserial(?:no|[ _-]?number)?\s*[:=]\s*(?P<v>\S+)",
         "a serial ties an artifact to one physical phone and proves nothing",
         control="(bootloader) serialno: 8XAX0LM9BA0Q7T",
         allowed="PORTHOLE_SERIAL_BAUD=115200"),

    Rule("subscriber-id",
         r"(?i)\b(?:imei|meid|iccid|imsi|equipment\s+id)\s*[:=]\s*(?P<v>\S+)",
         "carrier-linked and carrier-blockable; the vector is pasting "
         "`mmcli -m any` output in full",
         control="equipment id: 359072061234567",
         allowed="mmcli -m any | grep -iE 'state:|lock'"),

    # The one carve-out in the whole file, and it is free. A locally
    # administered address has bit 1 of its first octet set (low nibble in
    # 2367abef); a factory-burned OUI address never does, by definition, and
    # neither does a real AP's BSSID. Seven random MACs are already tracked in
    # taimen-has-no-factory-wlan-mac.md and usb-gadget-rerandomises-the-host-mac
    # .md, where the MAC IS the evidence -- a blanket ban deletes two findings
    # to protect nothing. This rule passes all seven with no allowlist.
    Rule("hardware-mac",
         r"\b[0-9a-f][014589cd](?::[0-9a-f]{2}){5}\b",
         "a factory MAC or a real BSSID; a BSSID resolves to a street address "
         "in commercial wifi geolocation databases",
         control="wlan0: authenticate with 04:18:d6:11:22:33",
         allowed="wlan0: RX AssocResp from 56:61:bd:56:26:94",
         flags=re.I),

    Rule("private-key",
         r"-----BEGIN(?:[ A-Z0-9]+)? PRIVATE KEY-----",
         "key material in a committed file, SECURITY.md's own first example",
         control="-----BEGIN OPENSSH PRIVATE KEY-----",
         allowed="ssh-keygen -y -f ${PORTHOLE_SSH_KEY:-~/.ssh/id_ed25519}"),

    Rule("token",
         r"\bgh[pousr]_[A-Za-z0-9]{36}\b",
         "a GitHub token; the prefixes are documented, so this is exact",
         control="ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
         allowed="ghp_short_and_obviously_not_one"),

    Rule("password-literal",
         r"(?i)\b\w*(?<!NO)pass(?:word|wd|phrase)\w*\s*[:=]\s*(?P<v>\S+)",
         "a literal credential, as opposed to the name of one",
         control="PORTHOLE_LAB_PASSWORD=hunter2correct",
         allowed="the sudoers line every playbook prints: NOPASSWD: ALL"),
]

# A value that is already a placeholder is the redaction working, not a leak.
# Section 4.5's convention is a stable angle-bracket pseudonym (`<ap-ch36>`),
# and a config reference (`$VAR`, `${VAR}`) is a name, never a secret.
def _placeholder(value: str) -> bool:
    v = value.strip().strip("\"'`")
    if not v or not any(c.isalnum() for c in v):
        return True                    # `serialno:` quoted in prose, not a value
    return v[0] in "<$%?+-"             # a name or a shell expansion, never a secret


def redact_login(target: str) -> str:
    """`<user>@<host>` with the login removed. The host is not the sensitive
    half -- 172.16.42.1 is a documented default -- and section 4.5 is explicit
    that what both path rules protect is the login.

    Exists because `porthole brief --json` publishes ssh_target, section 9 of
    AGENTS.md tells every agent to run that first, and its output is the single
    most likely thing to be pasted into a brain note or a PR. SECURITY.md
    already names this class: credential material leaking into JSON output.
    """
    user, sep, host = target.partition("@")
    return f"<user>{sep}{host}" if sep else target


def scan(text: str, where: str = "-"):
    """Every rule against one blob of text. Returns [(where, line, rule, hit)]."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for rule in RULES:
            for m in rule.re.finditer(line):
                value = m.groupdict().get("v")
                if value is not None and (value in rule.ok or _placeholder(value)):
                    continue
                hits.append((where, n, rule, m.group(0)))
    return hits


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


# Only reachable from the no-git fallback below, where none of them exists
# anyway; belt and braces, so the fallback can never start scanning a
# developer's build output or their gitignored logs/.
SKIP_DIRS = {".git", "__pycache__", "site", "site-src", ".run"}


def tracked(root):
    """What git tracks -- or, where there is no git, what is on disk.

    tests/ci-local.sh runs the suite from a `git archive` extraction: the
    tracked tree, with no .git in it at all. That is a fresh clone, which is
    the single place this check most needs to work, so "there is no git here"
    must never quietly become "the check did not run".
    """
    try:
        return [r for r in _git(root, "ls-files", "-z").split("\0") if r]
    except (subprocess.CalledProcessError, OSError):
        return [str(p.relative_to(root)) for p in sorted(root.rglob("*"))
                if p.is_file()
                and not SKIP_DIRS & set(p.relative_to(root).parts)]


def scan_tracked(root):
    """Everything tracked -- tools/, lib/, tests/, profiles/, brain/, docs/,
    .github/. The defect this replaces scanned tools() and nothing else."""
    hits = []
    for rel in tracked(root):
        if rel == SELF:
            continue
        path = root / rel
        if is_capture(rel):
            hits.append((rel, 0, CAPTURE, path.suffix))
            continue
        try:
            hits += scan(path.read_text(errors="replace"), rel)
        except (OSError, UnicodeDecodeError):
            continue
    return hits


class _Capture:
    name = "device-capture"
    why = ("a screenshot of a booted phone carries a wallpaper, notifications "
           "and an account name, and no regex will ever read one")


CAPTURE = _Capture()


def is_capture(rel: str) -> bool:
    """Raster only. A diagram is not a photograph of someone's device."""
    return pathlib.PurePath(rel).suffix.lower() in RASTER

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def scan_push(root, stdin):
    """The last cheap moment. After the push, only GitHub Support can help.

    Both surfaces the serial used: the added lines of the range, and the commit
    messages in it -- no check in this repo had ever read a commit message."""
    hits = []
    for line in stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local, _, remote = parts
        if set(local) == {"0"}:            # a branch deletion publishes nothing
            continue
        if set(remote) == {"0"}:           # a new branch: everything not on main
            try:
                remote = _git(root, "merge-base", local, "origin/main").strip()
            except subprocess.CalledProcessError:
                remote = EMPTY_TREE
        rng = f"{remote}..{local}"
        for rel, lines in added_by_file(
                _git(root, "diff", "--unified=0", rng)).items():
            if rel == SELF:
                continue
            hits += scan("\n".join(lines), f"{rel} (added)")
        hits += scan(_git(root, "log", "--format=%B", rng), f"{rng} (messages)")
    return hits


def added_by_file(diff):
    """The added lines of a diff, grouped by the file they land in.

    Pure, because getting it wrong is expensive in both directions and this is
    the only place a push is decided. Grouping is what lets SELF stay exempt
    here as well -- a control string is an added line like any other, and the
    first push of this branch was refused by its own hook over exactly that.
    It also means a finding names a path instead of an offset into one
    concatenated blob."""
    files, cur = {}, None
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            cur = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("+") and cur:
            files.setdefault(cur, []).append(ln[1:])
    return files


def report(hits, out=sys.stderr):
    for where, line, rule, hit in hits:
        at = f"{where}:{line}" if line else where
        print(f"{at}: {rule.name}: {hit!r}\n    {rule.why}", file=out)
    if hits:
        print(f"\n{len(hits)} finding(s). docs/HANDOFF-contribution-rules.md "
              "section 4.5 says what to do: redact to a stable placeholder "
              "(`<ap-ch36>`), do not delete the evidence.", file=out)
    return 1 if hits else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", action="store_true",
                    help="read pre-push refs on stdin; scan the range")
    ap.add_argument("files", nargs="*",
                    help="scan these as free text (a commit message); "
                         "default is everything git tracks")
    args = ap.parse_args(argv)
    try:
        root = pathlib.Path(_git(pathlib.Path.cwd(), "rev-parse",
                                 "--show-toplevel").strip())
    except (subprocess.CalledProcessError, OSError):
        root = pathlib.Path(__file__).resolve().parent.parent
    if args.push:
        # Here git really is required: there is no range without it, and
        # brain/laws/exit-codes-are-an-api.md is clear that "could not run" is
        # not "passed". A push hook that exits 0 on a broken git is worthless.
        try:
            return report(scan_push(root, sys.stdin))
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"cannot read the push range: {exc}", file=sys.stderr)
            return 69                  # EX_UNAVAILABLE, never 0
    if args.files:
        hits = []
        for f in args.files:
            hits += scan(pathlib.Path(f).read_text(errors="replace"), f)
        return report(hits)
    return report(scan_tracked(root))


if __name__ == "__main__":
    sys.exit(main())
