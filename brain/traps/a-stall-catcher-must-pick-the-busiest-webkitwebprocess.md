---
id: a-stall-catcher-must-pick-the-busiest-webkitwebprocess
title: A stall catcher on the browser must pick the WebKitWebProcess with the most threads, and thread names are truncated from the front
scope: generic
subsystem: browser
severity: trap
confidence: proven
evidence: 2026-09-06 on taimen, epiphany 50.6 with one YouTube tab: `pgrep -f WebKitWebProcess` lists five WebKitWebProcess (15, 15, 27, 14 and 73 threads); the first one found by a /proc scan had no GStreamer threads at all. `cat /proc/<pid>/task/*/comm` shows `eadedCompositor`, `rruptDispatcher`, `uggerDispatcher` -- the LAST 15 characters of the WTF thread name. Two stall-catcher arms (stall1440, comp1440) printed `0 captures` / `no thread matching 'ThreadedCompo'` for these two reasons while the holes were happening; the third, corrected run caught the main thread in SkBlurEngine on the first try.
first-learned: 2026-09-06
---

**Symptom** -- a process- or thread-level instrument on the browser (a stall
catcher, `threadcpu.py`, `eu-stack -p`, a per-thread sampler) runs for the
whole window and reports a clean null: `0 captures`, an idle main thread, no
GStreamer threads, or `no thread matching 'ThreadedCompo'`. Meanwhile the
frame log shows two-second holes. The null reads as "the web process is not
the problem".

**Cause** -- two independent things, either enough for the null:

1. Epiphany runs SEVERAL `WebKitWebProcess` (one per site isolation group
   plus prewarmed and idle ones -- five with a single YouTube tab). A scan of
   `/proc` that takes the first match by `comm` gets an idle one. The page
   with the media pipeline is the one with by far the most threads (73 vs
   14-27 here).
2. Linux keeps 15 characters of a thread name, and WTF's thread names are
   truncated from the FRONT: `ThreadedCompositor` is `eadedCompositor`,
   `InterruptDispatcher` is `rruptDispatcher`. A prefix match on the WebKit
   name finds nothing; a substring match (`Compositor`) does.

**What to do** -- choose the target `WebKitWebProcess` by thread count
(`max(len(os.listdir(f"/proc/{pid}/task")))`), and match thread names by
substring against the last 15 characters. `tools/tk-stallcatch.py` does both;
`threadcpu.py` matches by cmdline substring and is safe. Before believing any
null from such an instrument, print which pid and which tid it attached to --
the run that finally caught the blur printed `pid=59014 tid=59043
(eadedCompositor) threads=52` first.
Related: [[uprobes-do-not-attach-to-an-already-mapped-library]],
[[youtube-video-freezes-are-a-software-css-blur-on-the-main-thread]].

<!--
Before you submit this, check it against the bar:

  - Would this have saved someone a session? If not, it is a note to yourself,
    not a note for the corpus.
  - Is `evidence:` something a stranger can re-check? A trap without a source
    is folklore, and folklore is what this corpus exists to replace.
  - Is `scope:` honest? Over-claiming portability is worse than scoping
    narrowly. If it only ever applied to one device, say so.
  - One idea per note. If the title needs an "and", it is two notes.

Link related notes with [[note-id]] -- liberally. A link to a note nobody has
written yet is a marker, not an error.
-->
