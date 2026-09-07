#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (run it on the device, e.g. piped over ssh)
# env: TK_SOAK_LOG
# exits: 0 ok · non-zero on failure
# tk-soak.sh -- the on-device half of the D2 / 72 h stability soak.
# Run ON THE DEVICE as root, under systemd-run so it survives the ssh session.
#
# THE POINT, from PLAN-daily-driver-v2.md §1: "The single most valuable artefact
# is the last heartbeat before silence." Everything here exists to make sure
# that heartbeat survives whatever killed the phone.
#
# Two channels, deliberately different in how they fail:
#
#   /dev/kmsg   goes into the kernel ring buffer, so it lands in pstore/console
#               and survives a userspace death -- but NOT a watchdog reset,
#               which loses pstore on this device.
#   JSONL       fsync'd per line to disk, so it survives a reset -- but not a
#               filesystem that never got the write out. Hence both.
#
# A boot record is written first so a reboot is DISTINGUISHABLE from a hang:
# a soak log that simply restarts mid-run, with no boot line, means the file
# was truncated, not that the phone rebooted.
#
# ponytail: no rotation, no compression. 30 s cadence for 72 h is ~8600 lines.
# Add rotation when a soak actually runs long enough to care.
set -u

LOG=${TK_SOAK_LOG:-/var/log/tk-soak.jsonl}
INTERVAL=${1:-30}

# The boot record. bootreason distinguishes a watchdog reset from a clean
# reboot, and is the first thing to read when a soak has a gap in it.
BOOTID=$(cat /proc/sys/kernel/random/boot_id)
REASON=$(tr ' ' '\n' < /proc/cmdline | grep bootreason= | cut -d= -f2)
printf '{"ev":"boot","boot_id":"%s","bootreason":"%s","t":"%s"}\n' \
	"$BOOTID" "${REASON:-unknown}" "$(date -Iseconds)" >> "$LOG"
sync

while :; do
	UP=$(cut -d' ' -f1 /proc/uptime)
	LOAD=$(cut -d' ' -f1 /proc/loadavg)
	# MemAvailable, not MemFree: free memory on a zram box is meaningless.
	MEMAV=$(grep '^MemAvailable:' /proc/meminfo | tr -s ' ' | cut -d' ' -f2)
	SWAP=$(grep '^SwapFree:' /proc/meminfo | tr -s ' ' | cut -d' ' -f2)
	SOC=$(cat /sys/class/power_supply/bms/capacity 2>/dev/null)
	CUR=$(cat /sys/class/power_supply/bms/current_now 2>/dev/null)
	# Hottest zone, because a single zone name is not portable across boots.
	TMAX=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)
	# The failure classes the soak exists to catch, counted cumulatively.
	TRESET=$(dmesg 2>/dev/null | grep -c 'controller reset itself')
	HWFAIL=$(dmesg 2>/dev/null | grep -c 'hw init failed')
	OOMD=$(journalctl -u systemd-oomd --no-pager 2>/dev/null | grep -c 'Killed')
	DFPCT=$(df /var | tail -1 | tr -s ' ' | cut -d' ' -f5 | tr -d '%')

	printf '{"ev":"hb","up":%s,"load":%s,"mem_av":%s,"swap_free":%s,"soc":"%s","cur":"%s","tmax":"%s","touch_resets":%s,"hw_init_failed":%s,"oomd_kills":%s,"var_pct":%s,"t":"%s"}\n' \
		"$UP" "$LOAD" "${MEMAV:-0}" "${SWAP:-0}" "${SOC:-}" "${CUR:-}" "${TMAX:-}" \
		"${TRESET:-0}" "${HWFAIL:-0}" "${OOMD:-0}" "${DFPCT:-0}" "$(date -Iseconds)" >> "$LOG"
	sync   # the whole point is that the LAST line survives

	echo "tk-soak up=$UP load=$LOAD mem_av=$MEMAV tmax=$TMAX resets=$TRESET" > /dev/kmsg 2>/dev/null

	sleep "$INTERVAL"
done
