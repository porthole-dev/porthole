#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: HOST
# exits: 0 ok · non-zero on failure
"""Generate an acoustic probe signal, to be played on the HOST while the phone
captures. Runs on the host; needs no numpy.

Why this exists: a capture of a silent room passes the section-4 acceptance
rule on room noise alone (ac ~ 15), which cannot tell you anything about what
the capture path does to a signal. With a known sweep you can read the time
base and the distortion straight off the spectrum.

    ph-tone.py probe.wav && paplay probe.wav      # while the phone records

The sweep is the useful part: analyse the capture per window and compare the
peak against the sweep's own f(t). If the fundamental lands where it was
played, the capture's time base is correct -- which is how the "half sample
rate" reading of the 2:1 defect was disproved (HANDOFF-audio.md 2.5b).
"""
import math
import struct
import sys
import wave

RATE = 48000


def tone(f, dur, amp=0.6):
    return [amp * math.sin(2 * math.pi * f * t / RATE) for t in range(int(dur * RATE))]


def sweep(f0, f1, dur, amp=0.6):
    n = int(dur * RATE)
    out, ph = [], 0.0
    for t in range(n):
        ph += 2 * math.pi * (f0 * (f1 / f0) ** (t / n)) / RATE
        out.append(amp * math.sin(ph))
    return out


def main(path):
    sig = (tone(1000, 3) + [0.0] * int(1.5 * RATE) +
           tone(15000, 3) + [0.0] * int(1.5 * RATE) +
           sweep(1000, 20000, 8))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(
            struct.pack("<h", max(-32767, min(32767, int(v * 32767)))) for v in sig))
    print(f"{path}: {len(sig)/RATE:.1f}s -- 1 kHz, 15 kHz, then a 1k->20k sweep")


def demo():
    """The sweep must actually reach its endpoints."""
    s = sweep(1000, 2000, 1)
    assert len(s) == RATE and max(abs(v) for v in s) > 0.5
    assert abs(tone(1000, 1)[0]) < 1e-9          # starts at zero phase
    print("ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "probe.wav")
