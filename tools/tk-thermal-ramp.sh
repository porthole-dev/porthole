#!/bin/sh
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed · 130 see source
# tk-thermal-ramp.sh -- the six-phase thermal ramp, with an abort that works.
# Run ON THE DEVICE as root, WITH A HUMAN PRESENT.
#
# WHY THIS HAS TO RUN BEFORE THE DT FIX, NOT AFTER
#   There is no throttling on this device at all. Twenty zones are described and
#   there is not one `#cooling-cells` or `cooling-maps` anywhere in the taimen DT
#   chain, so `cpu0-thermal`'s 75 C passive trip fires and binds to nothing.
#   Eleven zones -- including both GPU zones -- carry a single `type = "hot"`
#   trip, and `of_thermal_ops` has no `.hot` member, so those execute literally
#   nothing. The 110 C critical calls hw_protection_shutdown(), but
#   THERMAL_EMERGENCY_POWEROFF_DELAY_MS=0 makes it return immediately, leaving
#   orderly_poweroff() and a working PID 1 as the only protection there is.
#
#   So this was meant to measure the UNPROTECTED baseline. Cooling maps now
#   exist in the tree, so that pre-throttle baseline is no longer obtainable
#   with this script -- it would have to be taken again after reverting the DT.
#
# THE ABORT IS THE POINT
#   Nothing in the kernel will stop this. This script stops it: any zone over
#   ABORT_C ends the run and drops the load. Do not raise ABORT_C to "get more
#   data" -- downstream throttles from 70 C, and 85 C is the new GPU passive
#   trip, so even 80 C is already well past where the vendor would have
#   intervened.
#
#   The only thermal datum this project has ever taken is 48.3 C after a
#   20-second CPU-only burn. Everything past that is unexplored.
#
# Usage: tk-thermal-ramp.sh [seconds_per_phase]     (default 300 = 30 min total)
set -u

SECS=${1:-300}
# 85 is the new GPU passive trip (85000 mC): a run aborting AT the trip could
# never observe the throttle it exists to catch. Lower the ceiling so GPU
# throttling happens before abort, not instead of it.
ABORT_C=${ABORT_C:-80}
LOG=${LOG:-/var/log/tk-thermal-ramp.csv}
ABORT_MC=$((ABORT_C * 1000))

zones() { for z in /sys/class/thermal/thermal_zone*; do echo "$z"; done; }
cdevs() { for c in /sys/class/thermal/cooling_device*; do echo "$c"; done; }
hottest() { cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1; }

# Header: one column per zone, plus scaling_max_freq for both cluster
# policies and cur_state for every cooling device -- without these the CSV
# cannot show the one thing this run exists to verify: frequency dropping at
# the passive trip.
ZONE_COUNT=$(zones | wc -l)
{
	printf 'time,phase'
	for z in $(zones); do printf ',%s' "$(cat "$z/type" 2>/dev/null || basename "$z")"; done
	printf ',policy0_max,policy4_max'
	for c in $(cdevs); do printf ',%s' "$(basename "$c")_state"; done
	printf '\n'
} > "$LOG"

LOADPIDS=""
drop_load() {
	[ -n "$LOADPIDS" ] && kill $LOADPIDS 2>/dev/null
	LOADPIDS=""
	pkill -f "stress-ng" 2>/dev/null
	pkill -f "glmark2"   2>/dev/null
	pkill -f "iperf3"    2>/dev/null
}
trap 'echo; echo ">> stopping, dropping load"; drop_load; exit 130' INT TERM

sample() {   # sample PHASE -- one CSV row, and the abort check
	t=$(hottest)
	{
		printf '%s,%s' "$(date +%H:%M:%S)" "$1"
		for z in $(zones); do printf ',%s' "$(cat "$z/temp" 2>/dev/null)"; done
		printf ',%s,%s' \
			"$(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq 2>/dev/null)" \
			"$(cat /sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq 2>/dev/null)"
		for c in $(cdevs); do printf ',%s' "$(cat "$c/cur_state" 2>/dev/null)"; done
		printf '\n'
	} >> "$LOG"
	if [ -n "$t" ] && [ "$t" -ge "$ABORT_MC" ]; then
		echo ""
		echo ">> ABORT: a zone reached $((t / 1000)) C (limit ${ABORT_C} C)"
		echo ">> Hottest zones now:"
		for z in $(zones); do
			v=$(cat "$z/temp" 2>/dev/null)
			[ -n "$v" ] && [ "$v" -ge $((ABORT_MC - 5000)) ] && \
				echo "     $(cat "$z/type" 2>/dev/null): $((v / 1000)) C"
		done
		drop_load
		exit 1
	fi
}

phase() {   # phase NAME START_CMD
	echo ">> phase: $1 (${SECS}s)"
	[ -n "${2:-}" ] && { eval "$2" & LOADPIDS="$LOADPIDS $!"; }
	i=0
	while [ "$i" -lt "$SECS" ]; do
		sample "$1"
		sleep 5
		i=$((i + 5))
		printf '\r     %ss  hottest %s C   ' "$i" "$(( $(hottest) / 1000 ))"
	done
	echo ""
}

echo ">> logging every 5 s to $LOG, abort at ${ABORT_C} C"
echo ">> run this on a TABLE first, then repeat on a folded towel -- the towel"
echo ">> arm is the one that finds the real ceiling, and it is the dangerous one."
echo ""

phase idle-screen-off ""
phase screen-on-idle  ""
phase gpu             "glmark2-es2 --run-forever >/dev/null 2>&1"
phase gpu-plus-cpu    "stress-ng --cpu 8 --timeout ${SECS}s >/dev/null 2>&1"
phase plus-network    "iperf3 -c ping.online.net -t ${SECS} >/dev/null 2>&1"
phase plus-charging   ""      # plug the charger in when this phase starts

drop_load
echo ">> done. $(wc -l < "$LOG") samples in $LOG"
# Only the zone-temp columns (3 .. 2+ZONE_COUNT) -- the freq/cdev columns
# added alongside them are a different unit and would corrupt this max.
echo ">> peak: $(( $(cut -d, -f"3-$((2 + ZONE_COUNT))" "$LOG" | tr ',' '\n' | grep -E '^[0-9]+$' | sort -n | tail -1) / 1000 )) C"
