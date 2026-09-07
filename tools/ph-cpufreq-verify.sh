#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
# ph-cpufreq-verify.sh -- does a requested CPU frequency actually get delivered?
# Run ON THE DEVICE, as root.
#
# WHY THIS EXISTS
#   `cpufreq is working` has been claimed on this port from the existence of
#   /sys/devices/system/cpu/cpufreq/policyN and from scaling_cur_freq. Neither
#   is evidence: both are the driver reading back the corner it last WROTE. On
#   msm8998 the OSM can accept every request and leave the PLL exactly where the
#   bootloader parked it, and everything in sysfs still looks perfect.
#
# WHY WORK-PER-SECOND, AND NOT A CYCLE COUNTER
#   There is no hardware PMU on this kernel: no armv8_pmuv3, and
#   perf_event_open(PERF_TYPE_HARDWARE) returns ENOENT even at
#   perf_event_paranoid=2. The OSM's own cycle counter reads 0 -> 0 (see
#   ph-osm-probe.py). A fixed integer loop, pinned to one CPU and timed, is the
#   only clock readout this device has left.
#
# WHY IT IS INTERNALLY CONTROLLED
#   The loop is run twice per cluster on the SAME boot: once with the policy
#   pinned to its lowest frequency, once pinned to its highest. Comparing the
#   two is what makes the result mean something -- a single number compared
#   against a baseline remembered from another boot cannot distinguish "the OSM
#   delivers" from "the OSM ignored both requests and the bootloader clock never
#   moved". Equal times ARE the null result, and they are unambiguous.
#
# Usage: ph-cpufreq-verify.sh [runs]      (default 3, best-of is reported)
set -u

RUNS=${1:-3}
CPUFREQ=/sys/devices/system/cpu/cpufreq

[ -d "$CPUFREQ" ] || { echo "no $CPUFREQ -- cpufreq never registered"; exit 1; }
set -- "$CPUFREQ"/policy*
[ -d "$1" ] || { echo "$CPUFREQ exists but has no policyN -- the OSM never came up"; exit 1; }

# Restore whatever the phone was running before, whatever happens below: leaving
# a cluster pinned to 300 MHz is a slow, confusing gift to the next agent.
SAVED=
restore() {
    echo "$SAVED" | while IFS=' ' read -r p gov mn mx; do
        [ -n "${p:-}" ] || continue
        echo "$mn" > "$p/scaling_min_freq" 2>/dev/null
        echo "$mx" > "$p/scaling_max_freq" 2>/dev/null
        echo "$gov" > "$p/scaling_governor" 2>/dev/null
    done
    echo "### restored"
}
trap restore EXIT INT TERM

# best-of-N wall seconds for a fixed integer loop pinned to $1
loop_time() {
    _cpu=$1 _best=
    _i=0
    while [ "$_i" -lt "$RUNS" ]; do
        _t=$(/usr/bin/time -f '%e' taskset -c "$_cpu" \
                awk 'BEGIN{x=0;for(i=0;i<1000000;i++)x+=i}' 2>&1)
        case "$_best" in
            "") _best=$_t ;;
            *) [ "$(echo "$_t $_best" | awk '{print ($1<$2)}')" = 1 ] && _best=$_t ;;
        esac
        _i=$((_i + 1))
    done
    echo "$_best"
}

echo "### kernel   $(uname -r)  uptime $(cut -d' ' -f1 /proc/uptime)s"
echo "### osm_setup_stop=$(cat /sys/module/qcom_cpufreq_hw/parameters/osm_setup_stop 2>/dev/null)" \
     "cprh_init_stop=$(cat /sys/module/cpr3/parameters/cprh_init_stop 2>/dev/null)"

for p in "$CPUFREQ"/policy*; do
    cpu=$(cut -d' ' -f1 "$p/related_cpus")
    lo=$(cat "$p/cpuinfo_min_freq"); hi=$(cat "$p/cpuinfo_max_freq")
    SAVED="$SAVED
$p $(cat "$p/scaling_governor") $(cat "$p/scaling_min_freq") $(cat "$p/scaling_max_freq")"

    echo
    echo "== ${p##*/} (cpu$cpu)  ${lo} .. ${hi} kHz"
    # transition_latency is the control for the shipped rate_limit_us=2000
    # tmpfiles write: with latency 0 schedutil's own default would be 1000 us,
    # so 2000 can only have come from userspace.
    echo "   cpuinfo_transition_latency $(cat "$p/cpuinfo_transition_latency") ns" \
         " rate_limit_us $(cat "$p/schedutil/rate_limit_us" 2>/dev/null || echo n/a)"

    echo performance > "$p/scaling_governor"

    echo "$lo" > "$p/scaling_min_freq"; echo "$lo" > "$p/scaling_max_freq"
    t_lo=$(loop_time "$cpu"); c_lo=$(cat "$p/scaling_cur_freq")

    echo "$hi" > "$p/scaling_max_freq"; echo "$hi" > "$p/scaling_min_freq"
    t_hi=$(loop_time "$cpu"); c_hi=$(cat "$p/scaling_cur_freq")

    echo "   pinned LOW   requested ${lo} kHz  reported ${c_lo} kHz  loop ${t_lo}s"
    echo "   pinned HIGH  requested ${hi} kHz  reported ${c_hi} kHz  loop ${t_hi}s"
    echo "$t_lo $t_hi $lo $hi" | awk '{
        printf "   VERDICT      speedup %.2fx for a %.2fx frequency request", $1/$2, $4/$3
        if ($1/$2 < 1.1) print "  -> NOT DELIVERED"
        else if ($1/$2 < 0.9 * $4/$3) print "  -> PARTIAL"
        else print "  -> DELIVERED"
    }'
done
