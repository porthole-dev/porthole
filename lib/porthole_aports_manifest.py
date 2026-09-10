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
