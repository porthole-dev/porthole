---
id: a-module-reload-does-not-reset-this-cards-audio-state
title: A module reload re-registers the card and leaves capture broken — audio needs a reboot
scope: device:google-taimen
subsystem: audio
severity: trap
confidence: proven
evidence: taimen docs/HANDOFF-audio.md §2.0 ("modprobe -r/modprobe is also not equivalent to a reboot for this card: capture then fails at hw_params until you reboot"), §12.4, docs/PROMPT-resume-audio.md, and tools/ph-gap.py, whose sweep reboots per trial for exactly this reason
first-learned: 2026-08-20
---

**Symptom** — you rebuild an audio module, `modprobe -r` and `modprobe` it, and
the card comes back. `aplay -l` lists it, the mixer controls are there, nothing
logs an error. Then capture fails at `hw_params`, and stays failing until you
reboot.

**Why it is expensive** — the reload *looks like it worked*. The card
re-registers, so every cheap check you would run to confirm the reload
succeeded passes. The failure appears one step later, at the point where you are
measuring the change you just made, and it is indistinguishable from "the change
broke capture". A sweep run this way produces a column of failures attributable
to the patch under test and to nothing else.

**What to do** — reboot between trials. Sweep module parameters through
`/etc/modprobe.d/` rather than `modprobe -r`/`modprobe` arguments, which is
exactly what `tools/ph-gap.py sweep` does and why it reboots per value:

```sh
tools/ph-gap.py sweep snd_soc_wcd934x slim_watermark 0 1 2 3
```

A related instance of the same property: **a failed stream leaves the PCM wedged
until a reboot** (`docs/PROMPT-resume-audio.md`). One aborted capture poisons
every later one in the same boot, and again nothing says so.

So on this card, treat a reboot as the unit of audio iteration.

That does **not** mean climbing to a more expensive rung. `porthole build mod`
still does the useful half correctly: it builds the module and replaces every
installed copy under `/lib/modules`, in whatever compression the rootfs uses. It
is only the `insmod` at the end that you cannot trust here. So the loop is
`build mod`, then reboot — the module you installed is the one that loads:

```sh
porthole build mod sound/soc/codecs/foo.ko foo --yes    # installs; the load is moot
tools/ph-reboot.sh                                       # this is what makes it live
```

`porthole build fast` would also work and costs ~6 minutes instead of ~40
seconds plus a boot. Reach for it when the CONFIG changed, not merely because
the reload was untrustworthy.

## Scope, stated honestly

Measured on taimen against `snd_soc_wcd934x`. The mechanism is very likely a
property of the codec driver's re-registration rather than of this board, so it
plausibly extends to other wcd934x devices — but that has **not** been checked,
and the note is scoped to what was measured. If you confirm it elsewhere, widen
the scope and say where.

What has *not* been established is which teardown step is incomplete. The
useful next question is whether the ADSP-side port or the SLIMbus channel
survives the module's exit — the driver's `remove()` is the place to read, and
[[read-the-vendor-before-inventing-a-mechanism]] applies: downstream's teardown
order is written down and ours is not.

Related: [[a-ucm-device-switch-cycles-the-whole-verb]],
[[shipped-configuration-is-not-running-configuration]],
[[prove-which-kernel-answered]].
