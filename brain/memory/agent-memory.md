---
id: agent-memory
title: What is worth remembering across sessions on a bring-up
scope: generic
subsystem: method
severity: technique
confidence: proven
evidence: the taimen agent memory, maintained 2026-07-24 to 2026-08-23
first-learned: 2026-08-04
---

A bring-up runs for months across many sessions, most of which start with no
memory of the previous one. Persistent memory is how that stops being fatal —
but only if it holds the right things.

## What belongs in memory

**Facts that are not derivable from the repo.** If `git log` or the code says
it, memory saying it too is duplication that will go stale.

- **Standing instructions from the human.** What they want confirmed before you
  do it, what they never want done unasked. On taimen: confirm before flashing
  or before a thermal ramp; never re-arm the auto-resume cron; never
  `set_active` the bad slot.
- **Corrections you were given**, with the *why*. "No Claude trailers on
  upstream-bound commits, because it makes review harder" survives a policy
  change; "no Claude trailers" alone becomes wrong the moment the human changes
  their mind, and you will not know.
- **Hard-won root causes**, in one line each, with the date. Not the whole
  investigation — that belongs in a handoff — but enough that you do not
  re-open a closed question.
- **Refuted claims.** *"The docs saying netconsole cannot transmit from NMI
  context are wrong; it does, over both usb0 and wlan0."* A refutation of a
  document that still exists is high-value memory, because the document will
  keep asserting itself at you.
- **Project goals and constraints** with absolute dates.

## What does not belong

Anything the repo already records: code structure, past fixes, git history,
build commands. Anything that only mattered to one conversation.

## The rules that keep it usable

**One fact per entry.** A memory holding five things cannot be corrected,
superseded or deleted independently.

**Absolute dates, always.** A memory written "yesterday" is unreadable a month
later.

**Verify before you act on it.** A memory naming a file, function or flag is a
claim about a tree that has moved on. Check it still exists. Memories reflect
what was true when written, not what is true now.

**Delete what turns out to be wrong.** A stale memory is worse than no memory:
it is confidently asserted context that nobody thinks to doubt.

**Keep an index.** One line per memory, loaded every session, so the agent knows
what exists without reading everything.

## The bootstrap for a new device

Seed memory with the standing instructions and the safety gates *before* the
first session that touches hardware. Those are exactly the things nobody
remembers to write down until after they have been violated once.

Related: [[handoff-format]], [[evidence-discipline]], [[agent-protocol]].
