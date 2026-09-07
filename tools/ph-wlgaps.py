#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host or device: it only reads a log)
# env: -
# exits: 0 ok · 1 nothing to read
"""Where a client's late frames actually are, from a WAYLAND_DEBUG=1 log.

ph-gesture-bench.py's client column answers "how many" and deliberately drops
every interval over 250 ms as idle -- so its `max` is pinned just under 250 and
says nothing, and its "presented every N vsyncs" prints only the four commonest
N, which hides a handful of very long stalls behind a crowd of short ones. Both
choices are right for a summary line and wrong for finding a stall.

This prints the whole distribution and, more usefully, WHEN each long gap
happened, in seconds from the first commit -- so a stall at the start of every
drag can be told apart from one scattered through it. That distinction is the
difference between a wake-up cost and a steady-state cost, and no summary
statistic can make it.

  ph-wlgaps.py LOG [WORST]      WORST: how many long gaps to name (default 12)

Read it against the gesture the arm ran: ph-scrollarm.sh drags 8 times with a
1.0 s pause between, so a gap of ~60 vsyncs every ~1.5 s is the pause itself,
not a stall.
"""
import collections
import re
import sys

COMMIT = re.compile(r"\[([\d:.]+)\].* -> (wl_surface#\d+)\.commit\(\)")
PRESENTED = re.compile(r"\[([\d:.]+)\].*wp_presentation_feedback#\d+"
                       r"\.presented\(([^)]*)\)")


def seconds(stamp):
    if ":" in stamp:
        h, m, sec = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    return float(stamp) / 1000.0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    worst = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    commits, presents = collections.defaultdict(list), []
    with open(sys.argv[1], errors="replace") as f:
        for line in f:
            m = COMMIT.search(line)
            if m:
                commits[m.group(2)].append(seconds(m.group(1)))
                continue
            m = PRESENTED.search(line)
            if m:
                a = [x.strip() for x in m.group(2).split(",")]
                # tv_sec_hi, tv_sec_lo, tv_nsec, refresh, seq_hi, seq_lo, flags
                if len(a) >= 7 and a[5].isdigit():
                    presents.append((seconds(m.group(1)), int(a[5])))
    if not presents:
        print("no wp_presentation_feedback.presented in %s -- was the client "
              "launched with WAYLAND_DEBUG=1, and does the compositor "
              "implement wp_presentation?" % sys.argv[1], file=sys.stderr)
        return 1
    surface, stamps = max(commits.items(), key=lambda kv: len(kv[1]))
    t0 = min(stamps[0], presents[0][0])

    steps = [(b[0] - t0, b[1] - a[1]) for a, b in zip(presents, presents[1:])
             if b[1] > a[1]]
    hist = collections.Counter(n for _, n in steps)
    total = sum(hist.values())
    print("%s  %d commits  %d presentations over %.1f s"
          % (surface, len(stamps), len(presents), presents[-1][0] - t0))
    print("presented every N vsyncs, in full:")
    for n in sorted(hist):
        share = 100.0 * hist[n] / total
        print("  %3d vsync%s  %5d  %5.1f%%  %s"
              % (n, " " if n == 1 else "s", hist[n], share,
                 "#" * min(60, int(share))))
    late = sum(c for n, c in hist.items() if n > 1)
    print("on time %.1f%%   late %d of %d   worst %d vsyncs (%.0f ms)"
          % (100.0 * hist.get(1, 0) / total, late, total,
             max(hist), max(hist) * 1000.0 / 60.0))

    # Who stalled. A gap in PRESENTATION is not yet a gap in the client: the
    # client may have committed all the way through it and the compositor sat
    # on the frames. Counting the client's own commits inside each gap is what
    # tells those apart, and it is the only question worth asking first.
    rel = sorted(t - t0 for t in stamps)
    print("\nthe %d longest gaps -- and whether the CLIENT was committing "
          "during them:" % worst)
    print("  %8s  %6s  %8s  %8s  %s"
          % ("at (s)", "vsyncs", "ms", "commits", "who was stalled"))
    import bisect
    for at, n in sorted(steps, key=lambda s: -s[1])[:worst]:
        span = n / 60.0
        lo, hi = at - span, at
        during = bisect.bisect_right(rel, hi) - bisect.bisect_left(rel, lo)
        # One commit is the one that ended the gap. More than a couple means
        # the client was drawing the whole time and nothing reached the panel.
        who = ("compositor: client committed %d times" % during if during > 2
               else "client: it committed nothing")
        print("  %8.2f  %6d  %8.0f  %8d  %s" % (at, n, span * 1000.0, during, who))
    return 0


if __name__ == "__main__":
    sys.exit(main())
