#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device with passwordless sudo; CONFIG_UPROBE_EVENTS. Offsets come
#        from TK_WKPHASE_OFFSETS, which tools/ph-wkoffsets.sh computes on the
#        HOST from the -dbg package for the build that is installed.
# env: TK_WKPHASE_OFFSETS (required for `arm`), PORTHOLE_WKPHASE_LIB
# exits: 0 ok · 64 usage or no offsets
# ph-wkphase.sh arm|measure [SECONDS]|off -- where the WebKit MAIN THREAD's
# per-frame time goes: style, layout, compositing, intersection observers.
#
# ph-webframe.sh answers the same question for the COMPOSITOR thread. This is
# the other half, and it exists because the 2026-09-05 scroll campaign found the
# main thread busy for contiguous runs of up to 215 ms with no hot spot in a
# flat profile -- nothing above 2%, spread over thousands of functions. A flat
# profile can only say "everything, a little". Uprobes on the phase entry points
# can say which phase.
#
# THREE VERBS, NOT ONE, AND THAT IS THE WHOLE POINT: a uprobe does not attach to
# a library the process has ALREADY mapped, so probes armed against a running
# browser never fire and the empty result reads exactly like "this function is
# never called". See brain/traps/uprobes-do-not-attach-to-an-already-mapped-
# library. So:
#
#   eval "$(tools/ph-wkoffsets.sh)"     # on the host; prints the export
#   ph-wkphase.sh arm                   # BEFORE the browser exists
#   <launch the browser, load the page, let it settle>
#   ph-wkphase.sh measure 12            # then drive the gesture
#   ph-wkphase.sh off
#
# It reads tracing/trace rather than perf: with the probes armed before launch,
# ftrace filled its buffer while `perf record -a -e wk:<name>` on the very same
# probes captured nothing on this kernel.
set -u
L=${PORTHOLE_WKPHASE_LIB:-/usr/lib/libwebkitgtk-6.0.so.4.16.10}
T=/sys/kernel/tracing
CMD=${1:-}

case "$CMD" in
arm)
	OFF=${TK_WKPHASE_OFFSETS:-}
	[ -n "$OFF" ] || { echo "TK_WKPHASE_OFFSETS is required; run tools/ph-wkoffsets.sh on the host" >&2; exit 64; }
	sudo -n sh -c "echo > $T/uprobe_events; echo 32768 > $T/buffer_size_kb"
	for spec in $OFF; do
		n=${spec%%=*}; a=${spec#*=}
		sudo -n sh -c "printf 'p:wk/%s %s:%s\nr:wk/%s_ret %s:%s\n' '$n' '$L' '$a' '$n' '$L' '$a' >> $T/uprobe_events" \
			|| echo "could not probe $n at $a" >&2
	done
	sudo -n sh -c "echo 1 > $T/events/wk/enable"
	echo "armed $(sudo -n sh -c "wc -l < $T/uprobe_events") probes -- now START the browser"
	;;
measure)
	sudo -n sh -c "echo > $T/trace"
	sleep "${2:-12}"
	# Reading a root-only trace file, not writing one -- the redirect
	# running as the caller (not root) is the point, not a bug.
	# shellcheck disable=SC2024
	sudo -n sh -c "cat $T/trace" > /tmp/wk.trace
	python3 - /tmp/wk.trace <<'PY'
import re, sys, collections, statistics as st
ev = []
for line in open(sys.argv[1]):
    m = re.match(r'\s*\S+-(\d+)\s+\[\d+\]\s+\S+\s+([\d.]+):\s+(\w+):', line)
    if m:
        ev.append((float(m.group(2)), m.group(3), m.group(1)))
if not ev:
    raise SystemExit("no probe hits. Either the page was idle, or the probes were "
                     "armed after the browser started and never attached.")
ev.sort()
# Page::updateRendering re-enters by design (it keeps a stack of remaining
# steps), so count only the OUTERMOST span or one 1.2 s call reads as fifteen.
depth = collections.defaultdict(int)
start, spans = {}, collections.defaultdict(list)
for t, name, tid in ev:
    base = name[:-4] if name.endswith('_ret') else name
    key = (tid, base)
    if name.endswith('_ret'):
        depth[key] -= 1
        if depth[key] == 0 and key in start:
            spans[base].append((t - start.pop(key)) * 1000)
    else:
        if depth[key] == 0:
            start[key] = t
        depth[key] += 1
window = ev[-1][0] - ev[0][0]
print("window %.1f s of probe activity" % window)
print("%-16s %5s %8s %8s %8s %9s" % ("phase", "calls", "p50 ms", "p90 ms", "max ms", "total ms"))
for n in sorted(spans, key=lambda x: -sum(spans[x])):
    v = sorted(spans[n])
    print("%-16s %5d %8.1f %8.1f %8.1f %9.0f" % (
        n, len(v), st.median(v), v[int(len(v) * .9)], v[-1], sum(v)))
PY
	;;
off)
	# Idempotent on purpose: `off` is what a cleanup trap calls, and a trap
	# that fails noisily when there was nothing to clean up trains people to
	# stop calling it. events/wk/ does not exist until `arm` has run once.
	sudo -n sh -c "[ -e $T/events/wk/enable ] && echo 0 > $T/events/wk/enable; echo > $T/uprobe_events" 2>/dev/null || true
	;;
*)
	echo "usage: ph-wkphase.sh arm|measure [SECONDS]|off" >&2; exit 64 ;;
esac
