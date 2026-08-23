#!/usr/bin/env python3
# scope: generic
"""Decide whether a capture contains real microphone audio.

Implements the acceptance rule from docs/HANDOFF-audio.md section 4, which
exists because four separate "the mic works!" conclusions in one session were
all settling transients:

  a mic counts as working only if a 20s+ capture, taken without touching the
  mixer immediately beforehand, shows sustained nonzero AC in EVERY second.

RMS alone is not evidence. distinct_values == 1 means one repeated number.
"""
import sys
import wave


def stats(xs):
    dc = sum(xs) / len(xs)
    ac = (sum((x - dc) ** 2 for x in xs) / len(xs)) ** 0.5
    return dc, ac, len(set(xs))


def main(path, skip=2.5):
    with wave.open(path) as w:
        rate, n = w.getframerate(), w.getnframes()
        assert w.getsampwidth() == 2, "expected S16_LE"
        raw = w.readframes(n)
    s = memoryview(raw).cast("h")
    start = int(skip * rate) * w.getnchannels()
    tail = list(s[start:])
    if not tail:
        print("EMPTY after skip")
        return 1

    dc, ac, distinct = stats(tail)
    print(f"{path}: {n/rate:.1f}s @{rate} -- dc={dc:.2f} ac={ac:.2f} distinct={distinct}")

    good = 0
    secs = 0
    for i in range(0, len(tail) - rate, rate):
        _, sac, sd = stats(tail[i:i + rate])
        secs += 1
        ok = sac > 0.5 and sd > 4
        good += ok
        print(f"  s{secs:<3} ac={sac:8.2f} distinct={sd:<6} {'ok' if ok else 'FLAT'}")

    verdict = good == secs and secs >= 15
    print(f"VERDICT: {'MIC WORKS' if verdict else 'no signal'} ({good}/{secs} seconds live)")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 2.5))
