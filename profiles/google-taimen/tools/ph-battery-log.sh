#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: on-device (push with `porthole push`, start with systemd-run)
# env: SECS (default 3600), PERIOD (default 10), LOG (default /var/log/ph-battery.csv)
# exits: 0 ok · 130 interrupted
# Log real battery drain, and the state needed to explain it.
#
# WHY THIS EXISTS: every power number in this port's docs carries "BP-00 M7
# stands -- an absolute standby figure cannot be taken over the cable", because
# the only instrument was pmi8998-charger/current_now, which is USBIN: system
# draw plus charging, at the mercy of the state of charge. Unplugged, the fuel
# gauge's own bms/current_now is the real thing.
#
# bms/current_now WORKS, despite older notes saying it is a constant 8789 uA.
# Verified 2026-09-19 on kernel #60: six samples 3 s apart read 102539..106933
# while charging, i.e. it tracks and has noise. Re-verify the SIGN when
# discharging before trusting a drain figure -- a gauge that reports magnitude
# only will look identical to one that reports signed current until you unplug.
#
# The skin/cooling columns are here because the thermal ladder now caps the big
# cluster from 38 C, and "why is it slow" and "why is it flat" have to be
# answerable from one file. bl_power/brightness are here because screen state
# dominates drain and an arm without it recorded cannot be compared to another.
set -u
SECS=${SECS:-3600}
PERIOD=${PERIOD:-10}
LOG=${LOG:-/var/log/ph-battery.csv}

SKIN=""
for z in /sys/class/thermal/thermal_zone*; do
	[ "$(cat "$z/type" 2>/dev/null)" = "skin-thermal" ] && SKIN="$z/temp" && break
done
DIE=$(ls /sys/bus/iio/devices/*/in_temp_die_temp_input 2>/dev/null | head -1)
BL=$(ls -d /sys/class/backlight/* 2>/dev/null | head -1)
B=/sys/class/power_supply/bms
C=/sys/class/power_supply/pmi8998-charger

r() { cat "$1" 2>/dev/null || echo ""; }

trap 'echo ">> interrupted at $(date +%T)"; exit 130' INT TERM

# vmin/vlow are the RPM low-power counters. They have read 0 for the life of
# this port; if a suspend experiment ever moves them, that is the headline, and
# it is only visible as a delta across a run.
echo "# start $(date -Is) uptime=$(cut -d' ' -f1 /proc/uptime)" > "$LOG"
sudo -n grep -H . /sys/kernel/debug/qcom_stats/* 2>/dev/null | sed 's/^/# /' >> "$LOG"
echo "t,iso,status,online,bms_uA,bms_uV,capacity,usbin_uA,skin_mC,die_mC,cd0,cd1,cd2,policy4_max,bl_power,brightness" >> "$LOG"

i=0
while [ "$i" -lt "$SECS" ]; do
	echo "$i,$(date -Is),$(r $B/status),$(r $C/online),$(r $B/current_now),$(r $B/voltage_now),$(r $B/capacity),$(r $C/current_now),$(r "$SKIN"),$(r "$DIE"),$(r /sys/class/thermal/cooling_device0/cur_state),$(r /sys/class/thermal/cooling_device1/cur_state),$(r /sys/class/thermal/cooling_device2/cur_state),$(r /sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq),$(r $BL/bl_power),$(r $BL/brightness)" >> "$LOG"
	sleep "$PERIOD"
	i=$((i + PERIOD))
done
echo "# end $(date -Is)" >> "$LOG"
sudo -n grep -H . /sys/kernel/debug/qcom_stats/* 2>/dev/null | sed 's/^/# /' >> "$LOG"
echo ">> $(grep -c '^[0-9]' "$LOG") samples in $LOG"
