---
id: read-the-vendor-before-inventing-a-mechanism
title: Read the vendor implementation before inventing a mechanism
scope: generic
subsystem: process
severity: law
confidence: proven
evidence: taimen 2026-08-26. Twice in one session. (1) Call-audio ports and topologies were stated plainly in ref/vendor-audio/mixer_paths_tavil_taimen.xml under "voicemmode1-call"; several calls were spent inferring them. (2) A voice PCM front-end was given an hrtimer modelled on sound/drivers/dummy.c, while downstream's msm-pcm-voice-v2.c -- already open in the same session -- defines snd_pcm_ops with no .pointer and no .copy at all, because nothing ever transfers through that PCM. The invented timer called snd_pcm_period_elapsed() from atomic context: "BUG: scheduling while atomic: swapper/7/0", hard lockup, watchdog reset, on a device carrying a live call.
first-learned: 2026-08-26
---

**Symptom** — you are several iterations deep into a mechanism you designed,
each one fixing the last one's side effect, and the failures are getting
stranger rather than smaller. Nothing you are testing is written down anywhere;
it is all inference from how the subsystem "should" work.

**Cause** — the vendor already shipped a working implementation of exactly this,
and it was not consulted. Not consulting it is rarely a decision; it happens
because a plausible mechanism came to mind first, and a plausible mechanism is
much cheaper to imagine than a real one is to read.

The trap has a tell: **you opened the vendor source earlier and skimmed it.**
Having looked once feels like having read it, so the second time the question
comes up you reason from memory instead of going back.

**What to do** — before designing any mechanism on a port, spend the ten
minutes:

- the vendor's driver for the same part (`ref/downstream-*`),
- the vendor's configuration for the same board (mixer paths, platform info,
  firmware blobs — these state ports, topologies and gains as fact),
- and *then* the mainline analogue.

Read the whole relevant function, not the declaration list. Downstream's PCM
ops table answered a question its `.ops` line alone did not: the callbacks that
are ABSENT were the answer.

When they disagree with your model, the vendor is describing hardware that
exists and your model is describing hardware you imagined.

Inference is not banned — it is what you do when the vendor genuinely has no
answer, and then you say out loud that this part is invented and what would
falsify it. What is banned is inventing first and checking never.

See also [[instrument-guilty-until-proven-innocent]] and
[[a-null-from-an-unexecuted-path-is-not-a-refutation]].
