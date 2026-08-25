# SPDX-License-Identifier: MIT
"""Colour, as meaning rather than decoration.

Call sites say what a thing IS -- stale, blocked, runnable, a law -- and this
module decides how it looks. That indirection is the whole point: the palette
can change in one place, and nobody has to grep for every spot someone typed
"red". The curses console got this right and it is worth keeping.

The palette is the **kernel log severity palette**, because that is what a
porter's eyes are already trained on. Red is what dmesg makes red. Yellow is
what it makes yellow. Nothing here asks anyone to learn a second colour
vocabulary while debugging a device that will not boot.

One rule inherited from the old console and worth restating: colour is never
the only signal. A stale milestone is red AND carries `!`; a law is red AND
says "law". Terminals get reconfigured, people are colourblind, and a screen
that only works in one palette is a screen that fails quietly.
"""
from __future__ import annotations

from rich.text import Text

# Semantic roles. Textual resolves these against the active theme, so they
# follow a light terminal as well as a dark one.
CRIT = "bold #ff5f5f"       # a stale claim, a failure, a forbidden slot
WARN = "#d7af5f"            # blocked, drifted, needs a state you are not in
OK = "#5faf5f"              # a measured fact: done, live, runnable now
NOTE = "#5fafd7"            # something you can act on
DIM = "#6c6c6c"             # evidence and provenance
ACTIVE = "bold #af87d7"     # the selected device, the current phase
BASE = ""                   # the terminal's own foreground


def ink(text, style=BASE, width=None):
    """A cell. Truncates to `width` on a word boundary where it can.

    Cutting mid-word reads as a rendering bug, which is why the old console
    trimmed at spaces too.
    """
    text = "" if text is None else str(text)
    if width and len(text) > width:
        cut = text[:max(0, width - 1)].rsplit(" ", 1)[0]
        text = (cut + "…") if cut else text[:width - 1] + "…"
    return Text(text, style=style)


# ------------------------------------------------------------- vocabularies --

# A tool's `needs:` header. "-" and "any" run anywhere; everything else wants
# the device in a state you may not be in, and that is worth seeing before you
# run it rather than after it fails.
def needs(value):
    # The header field carries prose after the state -- "- (host only, no
    # device)", "BOOTED (over ssh)". Only the first token is the state, and a
    # column that renders "- (HOST…" is noise where a word would do.
    text = (value or "-").strip().split()[0].upper() if (value or "").strip() else "-"
    free = text in ("-", "", "NONE", "ANY")
    return ink(text, OK if free else WARN, 9)


# Note severity. `law` is the one that stops you wasting a week, so it gets the
# loudest role in the palette.
SEVERITY = {"law": CRIT, "trap": WARN, "technique": NOTE, "fact": DIM}


def severity(value):
    value = value or "fact"
    return ink(value, SEVERITY.get(value, DIM), 9)


# Device state, as the header and the devices pane report it.
def state(value):
    text = (value or "unknown").upper()
    if text in ("BOOTED", "SSH"):
        style = OK
    elif text in ("FASTBOOT", "RECOVERY", "FROZEN"):
        style = WARN
    elif text in ("ABSENT", "UNKNOWN"):
        style = DIM
    else:
        style = BASE
    return ink(text, style)


# A milestone's tag. Stale first and loudest: it means the port believes it is
# further along than it is, and that is the direction that gets someone
# flashing a device.
def milestone_tag(row):
    import porthole_milestones as ms
    if row.get("source") == "stale":
        return ink("! stale", CRIT, 9)
    if row.get("state") == ms.BLOCKED:
        return ink("· blocked", WARN, 9)
    if row.get("state") == ms.DONE:
        return ink("✓ done", OK, 9)
    return ink("", DIM, 9)


# The palette's namespace badges. A nine-character lowercase word in one of
# four foreground colours was the old console's only signal, and on an
# eight-colour terminal two of the four collapsed into each other.
BADGE = {
    "verb": (" VERB ", "reverse " + NOTE),
    "tool": (" TOOL ", "reverse " + OK),
    "note": (" NOTE ", "reverse " + WARN),
    "milestone": (" STEP ", "reverse " + ACTIVE),
}


def badge(kind):
    label, style = BADGE.get(kind, (" ???  ", DIM))
    return Text(label, style=style)
