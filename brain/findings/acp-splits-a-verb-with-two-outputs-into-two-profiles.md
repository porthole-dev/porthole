---
id: acp-splits-a-verb-with-two-outputs-into-two-profiles
title: ACP gives a UCM verb with two outputs on one PCM two profiles, not two ports -- so callaudiod's speaker button cannot work
scope: soc:msm8998
subsystem: audio
severity: finding
confidence: proven
evidence: logs/2026-08-27-slow, pactl list cards on taimen, spa/plugins/alsa/acp/alsa-ucm.c devset_supports_device
refutes: make the Voice Call verb one profile with an Earpiece port and a Speaker port; ConflictingDevice or SupportedDevice will merge them; PlaybackPCM at verb level will merge them
first-learned: 2026-08-27
---
**The question** — the loudspeaker button in a call does nothing. The UCM verb
declares an Earpiece device and a Speaker device, both correct, and yet the
speaker toggle in the call UI is inert. Is the UCM wrong?

**The answer** — the UCM is fine. PipeWire's ACP (and PulseAudio 17+) will not
put two devices that share a `PlaybackPCM` into the same card profile:

    /* PlaybackPCM must not be the same as any selected device, except when both split */
    if (sink && sink2 && pa_streq(sink, sink2)) {
            if (!(dev->playback_split && d->playback_split))
                    return false;
    }
            -- spa/plugins/alsa/acp/alsa-ucm.c, devset_supports_device()

So a verb with two playback devices on one PCM becomes **two profiles with one
port each** -- `Voice Call (Earpiece)` and `Voice Call (Speaker)` -- and never
one profile with two ports. Upstream callaudiod moves the loudspeaker by
setting a sink PORT (`cad_pulse_enable_speaker` -> `set_output_port` ->
`pa_context_set_sink_port_by_index`), and the sink in either profile has
exactly one port, so on such a card its EnableSpeaker can never do anything.
The fix is a daemon that selects the sibling PROFILE: pmaports already carries
one, `pinephone-callaudiod`, whose README names the same cause ("Pulseaudio
v17 and Pipewire v1.2 changed the way audio profiles are generated, which
upstream callaudiod does not handle well").

On a board where the two outputs are genuinely one PCM -- taimen's earpiece and
loudspeaker are the same AFE port, chosen inside the amplifier by which
firmware configuration is loaded -- there is no UCM shape that avoids this.

**What this rules out** — measured on the device, all four with the profile
list before and after a `wireplumber` restart:

- **"Move `PlaybackPCM`/`PlaybackChannels` up to the verb and the devices will
  share one mapping."** No change: still two profiles, one port each.
- **"Declare `ConflictingDevice` on each so they become alternatives."** No
  change. Conflicting is what they already are implicitly.
- **"Declare `SupportedDevice` on each so ACP may combine them."** No change --
  the same-PCM test above runs regardless, and it is not reached through the
  supported-devices path.
- **"The port switch would work if only the ports existed."** Even granting the
  ports, a port switch does not re-open the PCM, and on this board the output
  is selected by a firmware download that only happens at `hw_params`. The
  profile switch is what makes the amplifier reload; six of six switches did.

**How it was established** — four UCM variants installed in turn on
`/usr/share/alsa/ucm2/Google/taimen/VoiceCall.conf`, wireplumber restarted
between each, `pactl list cards` compared (profiles, ports, and each port's
"Part of profile(s)" line). Then the mechanism read in ACP's own source. What
would overturn it: `SplitPCM` support in the UCM (two devices splitting the
channels of one PCM are exempt from the test) -- not applicable to a mono voice
PCM, but it is the one documented escape hatch.
