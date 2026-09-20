# SPDX-License-Identifier: MIT
"""What this port carries on top of stock, and what breaks without each.

WHY THIS FILE EXISTS
    apk compares pkgver BEFORE pkgrel. A fork carried at 26.1.6-r14 outranks
    upstream's 26.1.6-r0 and is outranked the moment upstream ships 26.2.0-r0
    -- silently, with the patches simply absent from the next flash. That has
    happened here once already, to phoc, and cost nine hours misdiagnosed as
    a WebKit bug.

    Guarding against it needs two things nothing recorded: WHICH aports this
    port actually owns, and WHERE each was forked from. This is that list.

WHY `why:` IS MANDATORY
    A list of names answers "is it still winning" but not "does it matter",
    and the second question is the one someone arriving at this port asks.
    A fork whose reason nobody can state is a fork to drop, not to carry.
"""
from __future__ import annotations

import pathlib

import porthole

FIELDS = ("upstream", "tier", "why", "forked", "commit")
TIERS = ("required", "optional")


def parse(text: str):
    """`aports.conf` -> ordered {aport: {field: value}}."""
    return porthole.parse_blocks(text, FIELDS)


def names(man, tier: str | None = None) -> list:
    """Block names, in file order, optionally filtered to one tier.

    A block with no `tier:` is `required`. The default is the safe one: a
    fork someone forgot to classify should be checked, not skipped.
    """
    if tier is None:
        return list(man)
    return [n for n, f in man.items() if f.get("tier", "required") == tier]


def problems(man) -> list:
    """Human-readable complaints about a manifest, worst first."""
    found = []
    for name, fields in man.items():
        why = fields.get("why", "")
        if not why or why.startswith("TODO"):
            found.append(f"{name}: no `why:` -- say what breaks without it")
        tier = fields.get("tier", "required")
        if tier not in TIERS:
            found.append(f"{name}: tier `{tier}` is not one of "
                         f"{', '.join(TIERS)}")
    return found


def path_for(root, device: str) -> pathlib.Path:
    return pathlib.Path(root) / "profiles" / device / "aports.conf"


def load(root, device: str):
    """The device's manifest, or an empty one when it has none."""
    path = path_for(root, device)
    try:
        return parse(path.read_text())
    except OSError:
        return parse("")


# Deliberately not a valid `why:`. problems() rejects it, so a fork recorded
# automatically stays visibly unfinished until a human says what it is for --
# which is the one field the manifest exists to carry.
UNEXPLAINED = "TODO: say what breaks without this fork"


def entry_text(name: str, upstream_rel: str, pkgver: str, pkgrel: str,
               commit: str) -> str:
    """The manifest block for a freshly created fork."""
    return (f"\n{name}\n"
            f"  upstream: {upstream_rel}\n"
            f"  tier:     required\n"
            f"  why:      {UNEXPLAINED}\n"
            f"  forked:   {pkgver}-r{pkgrel}\n"
            f"  commit:   {commit}\n")


def append(root, device: str, text: str) -> pathlib.Path:
    path = path_for(root, device)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
    return path


def verdict(ours_ver: str, ours_rel: str, up_ver: str, up_rel: str) -> str:
    """Would apk prefer upstream's build over ours? -> safe | loses | at-risk

    apk compares pkgver BEFORE pkgrel, so a high pkgrel protects a fork only
    while the pkgver is identical. That is the whole trap: temp/phoc at
    0.56.0-r60 was outranked by stock 0.57.0-r0 and nothing said so.

    Deliberately NOT a general Alpine version comparator. Ordering two
    different pkgvers correctly means reimplementing apk's rules (suffixes,
    _git, _p, letters) and a subtly wrong copy would be worse than none --
    it would report `safe` for a fork that is about to vanish. When the
    pkgvers differ, the honest answer is `at-risk`: look at it.
    """
    if ours_ver != up_ver:
        return "at-risk"
    try:
        return "safe" if int(ours_rel) > int(up_rel) else "loses"
    except (TypeError, ValueError):
        return "at-risk"


# How old a comparison may be before `safe` stops meaning anything.
#
# WHY THIS EXISTS. On 2026-09-20 `porthole pkg drift` reported
#
#     mesa   26.2.2-r51   upstream 26.2.2-r1   SAFE
#
# and it was right about the data it had. The data was eleven days old: the
# aports_upstream clone's origin/master sat at 2026-09-09, where Alpine's mesa
# was still 26.2.2. Alpine had since moved to 26.2.3, the phone had taken stock
# 26.2.3-r0, and fifty-one releases of a5xx patches were not installed. The
# display corruption they fix came back and read as a new bug.
#
# drift PRINTED the sync date the whole time. Printing was not enough, because
# a verdict and a footnote do not carry the same weight -- the verdict said
# SAFE and that is what was believed. So staleness has to change the verdict
# rather than sit beside it.
#
# Three days, not one: a fetch is a network round trip nobody wants on every
# invocation, and an aports tree that moved yesterday rarely invalidates a
# comparison. Eleven days does.
STALE_AFTER_DAYS = 3


def age_in_days(synced: str, today=None) -> int:
    """Whole days between an ISO date (`%cs`) and today. -1 when unknown."""
    import datetime

    try:
        when = datetime.date.fromisoformat((synced or "").strip())
    except ValueError:
        return -1
    now = today or datetime.date.today()
    return (now - when).days


def stale_verdict(verdict_now: str, age_days: int,
                  limit: int = STALE_AFTER_DAYS) -> str:
    """Downgrade a clean verdict that was computed against old data. Pure.

    Only `safe` is downgraded. `loses` and `at-risk` already say "look at
    this", and an old comparison that found a problem has still found a real
    problem -- upstream only moves forward. `unknown`/`unresolved` are
    untouched because they are already not claims.
    """
    if verdict_now != "safe":
        return verdict_now
    if age_days < 0:
        return "stale"
    return "stale" if age_days > limit else "safe"
