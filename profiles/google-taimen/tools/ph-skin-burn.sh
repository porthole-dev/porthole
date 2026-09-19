#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: on-device (run it on the device; push it with `porthole push`)
# env: SKIN_ABORT (mC, default 50000), DIE_ABORT (mC, default 85000),
#      MAXSECS (default 480), LOG (default /var/log/ph-skin-burn.csv)
# exits: 0 ok · 1 no skin zone · 130 interrupted
# Drive skin temperature up under CPU load and record which rungs of the
# vendor thermal ladder engage, in order, and what each one does.
#
# WHY THIS AND NOT ph-thermal-ramp.sh: that one is a six-phase, 30-minute,
# human-present survey of an UNPROTECTED device, and it aborts on the hottest
# zone. This is the opposite question -- the ladder is armed, and what has to
# be recorded is the skin sensor that drives it, the three cooling devices it
# binds, and the frequency each one lands on. Those columns are the whole
# point and ph-thermal-ramp.sh does not have them.
#
# WHAT A "SHORT" RUN MEANS HERE: the ladder is self-limiting. Capping the big
# cluster at the 38 C and 40 C rungs removes the heat that would drive skin to
# 45 C, so on a table this plateaus around 42.5 C and the upper four rungs are
# never reached -- measured, see
# brain/findings/the-skin-ladder-caps-the-die-22c-and-is-self-limiting.md.
# That is the ladder working, not a short test. To exercise the upper rungs
# you need an insulated arm and a human present; to verify only their TARGETS,
# write cur_state directly instead, which needs no heat at all.
#
# The abort is the point. SKIN_ABORT defaults to 50000 -- below the 52 C top
# rung and 6 C under the vendor's own 56 C shutdown (SKIN-SHUTDOWN2). Do not
# raise it to "get more data"; pair this with `ph-thermal.sh guard` on the
# host, which watches the die while this watches the skin.
set -u
SKIN_ABORT=${SKIN_ABORT:-50000}
DIE_ABORT=${DIE_ABORT:-85000}
MAXSECS=${MAXSECS:-480}
LOG=${LOG:-/var/log/ph-skin-burn.csv}

# By type, never by index: thermal_zone numbering is registration order and it
# moves whenever a sensor driver is added or a probe is deferred.
SKIN=""
for z in /sys/class/thermal/thermal_zone*; do
	[ "$(cat "$z/type" 2>/dev/null)" = "skin-thermal" ] && SKIN="$z/temp" && break
done
[ -n "$SKIN" ] || { echo "no skin-thermal zone -- is CONFIG_QCOM_SPMI_ADC_TM5 set?" >&2; exit 1; }
DIE=$(ls /sys/bus/iio/devices/*/in_temp_die_temp_input 2>/dev/null | head -1)
CD0=/sys/class/thermal/cooling_device0/cur_state
CD1=/sys/class/thermal/cooling_device1/cur_state
CD2=/sys/class/thermal/cooling_device2/cur_state
P0=/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq
P4=/sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq
GM=/sys/class/devfreq/5000000.gpu/max_freq
USB=/sys/class/power_supply/pmi8998-charger/current_now

command -v stress-ng >/dev/null || { echo "stress-ng missing: apk add stress-ng" >&2; exit 1; }

drop() { pkill -f stress-ng 2>/dev/null; }
trap 'echo ">> interrupted"; drop; exit 130' INT TERM

echo "t,skin_mC,die_mC,cd0,cd1,cd2,policy0_max,policy4_max,gpu_max,usbin_uA" > "$LOG"
stress-ng --cpu 8 --timeout "${MAXSECS}s" >/dev/null 2>&1 &
BURN=$!

i=0
reason="max seconds"
while [ "$i" -lt "$MAXSECS" ]; do
	s=$(cat "$SKIN"); d=$(cat "$DIE")
	echo "$i,$s,$d,$(cat $CD0),$(cat $CD1),$(cat $CD2),$(cat $P0),$(cat $P4),$(cat $GM),$(cat $USB)" >> "$LOG"
	[ "$s" -ge "$SKIN_ABORT" ] && { reason="skin hit $s mC"; break; }
	[ "$d" -ge "$DIE_ABORT" ]  && { reason="die hit $d mC";  break; }
	sleep 2
	i=$((i + 2))
done
drop; wait $BURN 2>/dev/null

# Cooldown in the same columns. The release side matters: the hysteresis is the
# vendor's 1 C, so a rung holds until skin drops a full degree under its trip,
# and a cooling device that has not moved 60 s after the load stopped is
# CORRECT, not stuck.
j=0
while [ "$j" -lt 60 ]; do
	echo "cool+$j,$(cat "$SKIN"),$(cat "$DIE"),$(cat $CD0),$(cat $CD1),$(cat $CD2),$(cat $P0),$(cat $P4),$(cat $GM),$(cat $USB)" >> "$LOG"
	sleep 5; j=$((j + 5))
done
echo ">> stopped: $reason; $(wc -l < "$LOG") rows in $LOG"
