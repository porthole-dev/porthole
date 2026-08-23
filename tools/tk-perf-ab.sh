#!/bin/sh
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
# tk-perf-ab.sh -- interleaved A/B of a tuning knob against real frame timings.
# Run ON THE DEVICE as root.
#
# WHY INTERLEAVED, AND WHY NOT AVERAGE FPS
#   This session is a thermal and a scheduler system, so it drifts: the same
#   gesture measured twice ten minutes apart differs by more than most of the
#   knobs being tested. A-then-B therefore attributes drift to the knob. This
#   runs A B A B A B and reports each arm's median-of-rounds, so drift shows up
#   as spread within an arm instead of as a result.
#
#   And the number reported is the frame-interval distribution from
#   tk-gesture-bench.py, not average FPS. A session that renders 58 frames in a
#   second but stalls 100 ms mid-scroll reads as broken to a human and as fine
#   to an average. p95 and max are the numbers that match what a user feels.
#
# Usage:
#   tk-perf-ab.sh ratelimit SCENE [REPEATS] [B_VALUE_US]   default B = 1000
#   tk-perf-ab.sh dmalatency SCENE [REPEATS]               B = 0 us via /dev/cpu_dma_latency
#   tk-perf-ab.sh uclamp SCENE [REPEATS] [B_UCLAMP_MIN]     A=0, default B = 256
#     Uses the already-shipped /usr/libexec/taimen-uclamp-session (per-task
#     sched_setattr on phoc+phosh, see that script's header for the mechanism)
#     with UCLAMP_MIN substituted per arm via sed into a throwaway copy --
#     reuses the exact verified syscall path instead of reimplementing it.
#     restore() re-runs the unmodified installed script so the phone is left
#     at the shipped value (256), not at whatever arm ran last.
#
# ponytail: three rounds, two arms, no config file. Add rounds if the spread
# within an arm is wider than the gap between arms -- that is the only signal
# that says the answer is not yet resolvable.
set -u

BENCH=${BENCH:-/tmp/tk-gesture-bench.py}
KNOB=${1:?knob: ratelimit|dmalatency|uclamp}
SCENE=${2:-grid-fling}
REPS=${3:-4}
BVAL=${4:-1000}
ROUNDS=${ROUNDS:-3}

[ "$KNOB" = uclamp ] && [ "$4" = "" ] && BVAL=256

P0=/sys/devices/system/cpu/cpufreq/policy0/schedutil/rate_limit_us
P4=/sys/devices/system/cpu/cpufreq/policy4/schedutil/rate_limit_us
ORIG0=$(cat $P0 2>/dev/null); ORIG4=$(cat $P4 2>/dev/null)
LATPID=
UCLAMP_SRC=/usr/libexec/taimen-uclamp-session
UCLAMP_TMP=/tmp/tk-perf-ab-uclamp.py

apply_uclamp() {
	sed "s/^UCLAMP_MIN = .*/UCLAMP_MIN = $1  # tk-perf-ab.sh override/" \
		"$UCLAMP_SRC" > "$UCLAMP_TMP"
	python3 "$UCLAMP_TMP"
}

restore() {
	case "$KNOB" in
	ratelimit) [ -n "$ORIG0" ] && echo "$ORIG0" > $P0; [ -n "$ORIG4" ] && echo "$ORIG4" > $P4 ;;
	uclamp) python3 "$UCLAMP_SRC" >/dev/null 2>&1 ;;  # back to shipped 256, unmodified
	esac
	[ -n "$LATPID" ] && kill "$LATPID" 2>/dev/null
	echo ">> restored (rate_limit_us = $(cat $P0 2>/dev/null)/$(cat $P4 2>/dev/null))"
}
trap restore EXIT INT TERM

set_arm() {
	case "$KNOB:$1" in
	ratelimit:A) echo "$ORIG0" > $P0; echo "$ORIG4" > $P4 ;;
	ratelimit:B) echo "$BVAL"  > $P0; echo "$BVAL"  > $P4 ;;
	dmalatency:A) [ -n "$LATPID" ] && { kill "$LATPID" 2>/dev/null; LATPID=; } ;;
	# Holding /dev/cpu_dma_latency open with a 0 writes a 0 us PM-QoS floor,
	# which blocks the deeper cpuidle states. The constraint lives only as long
	# as the fd is open, hence the background holder rather than a one-shot write.
	dmalatency:B) python3 -c "
import os,struct,time
fd=os.open('/dev/cpu_dma_latency', os.O_WRONLY)
os.write(fd, struct.pack('i',0))
time.sleep(100000)" & LATPID=$! ;;
	uclamp:A) apply_uclamp 0 ;;
	uclamp:B) apply_uclamp "$BVAL" ;;
	esac
	sleep 2
}

echo "knob=$KNOB scene=$SCENE repeats=$REPS rounds=$ROUNDS"
case "$KNOB" in
ratelimit) echo "A = baseline, B = rate_limit_us=$BVAL" ;;
dmalatency) echo "A = baseline, B = cpu_dma_latency=0us" ;;
uclamp) echo "A = uclamp_min=0, B = uclamp_min=$BVAL" ;;
esac
echo
r=1
while [ "$r" -le "$ROUNDS" ]; do
	for arm in A B; do
		set_arm "$arm"
		# head -5, not tail -3: measure() prints one "on <app>" witness line,
		# then report() prints its useful summary as ITS first 4 lines
		# (name+desc, frames/fps, frame-ms percentiles incl. max, dropped%+jank
		# count) -- 5 lines total -- before an optional per-jank-event
		# breakdown that can run to 8 more lines. `tail -3` grabbed the END of
		# that breakdown on any round with >=3 jank events, silently dropping
		# the dropped%/max-ms lines the whole comparison depends on. (A first
		# fix landed as `head -4`, one line short -- verified wrong against a
		# live capture 2026-08-08: it still cut the "dropped=.../jank>33ms="
		# line. head -5 is correct.) NOT MEASURED/FEW FRAMES reports are 1-2
		# lines, so head -5 still prints them whole.
		out=$(python3 "$BENCH" "$SCENE" "$REPS" 2>&1 | head -5)
		echo "round $r arm $arm: $(echo "$out" | tr '\n' ' | ')"
	done
	r=$((r + 1))
done
echo
echo ">> compare the p95/max columns ACROSS ROUNDS, not one pair. If the spread"
echo ">> within an arm is as wide as the gap between arms, the knob did nothing"
echo ">> measurable and the honest answer is 'no effect', not the better round."
