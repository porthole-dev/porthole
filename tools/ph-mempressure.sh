#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (run as root; scp it over, or pipe with `ssh ... sh -s`)
# env: PORTHOLE_SCOPE_GLOB, PORTHOLE_INTERVAL
# exits: 0 ok · non-zero on failure
# ph-mempressure.sh -- one line per second of the memory-ceiling vital signs.
#
# WHY THIS EXISTS
#   brain/findings/epiphany-is-a-memory-ceiling-not-a-gpu-fault closed the
#   question but left its mitigation (MemoryHigh= on the app scope) APPLIED AND
#   UNVERIFIED, because verifying it needs a real graphical session driving a
#   real page -- which no headless tool can produce. It named the number to
#   compare: `memory full avg10` before and after, on the same page.
#
#   This prints that number, next to the four others needed to read it:
#
#     psi_full   whole-system reclaim stall. THE verdict field. Every runnable
#                task blocked in reclaim. This is what the user feels.
#     memavail   how close the 3.7 GB device is to the wall.
#     scope      the browser cgroup's memory.current / memory.swap.current --
#                where the growth actually lives, and whether it is being
#                pushed to zram instead of shed.
#     gem        DRM resident bytes. msm GEM is shmem-backed, so this is the
#                same memory again, seen from the GPU side. It is the number
#                MemoryHigh= is supposed to bring down.
#     gpu/tmax   the heat side: an A540 pinned at max with hot silicon is the
#                signature of compositing a working set that does not fit.
#
# NOT /sys/kernel/debug/dri/0/gpu -- that one wedges the GPU (ph-sysstate.sh
# carries the same warning). `gem` is a different file and is safe to read.
#
# Usage, on the device as root:   sh ph-mempressure.sh
#        one-shot single sample:  sh ph-mempressure.sh 1
# From the host, both phases into one file for a before/after diff:
#   TK_AGENT=<you> tools/ph-device.sh --need-booted bash -c '. tools/ph-lib.sh
#     scp "${TK_SSH_OPTS[@]}" tools/ph-mempressure.sh "$PHONE":/tmp/ >/dev/null
#     ssh "${TK_SSH_OPTS[@]}" "$PHONE" "sudo sh /tmp/ph-mempressure.sh"'
set -u

# The scope name carries the launching PID, so it changes every time the
# browser restarts -- glob it EVERY iteration rather than resolving once, or a
# restart mid-measurement silently turns every scope field into a dash.
GLOB=${PORTHOLE_SCOPE_GLOB:-'/sys/fs/cgroup/user.slice/user-*.slice/user@*.service/app.slice/app-*Epiphany*.scope'}
INTERVAL=${PORTHOLE_INTERVAL:-1}
COUNT=${1:-0}          # 0 = forever

DECSUB=$(ls -d /sys/devices/platform/soc@*/*video-codec*/*video-decoder \
	/sys/devices/platform/*video-codec*/*video-decoder 2>/dev/null | head -1)

# ponytail: fixed at the Epiphany scope by default. PORTHOLE_SCOPE_GLOB retargets it
# at any app scope; a second browser would just be a different glob.
field() { [ -r "$1" ] && cat "$1" 2>/dev/null || echo -; }

n=0
while :; do
	up=$(cut -d. -f1 /proc/uptime)

	# PSI: "full" is the verdict -- every runnable task stalled in reclaim.
	# "some" alone can be one unlucky task and means much less.
	# shellcheck disable=SC2046  # the split into $1/$2 is the point
	set -- $(awk '/^some/{s=$2} /^full/{f=$2} END{print s, f}' \
		/proc/pressure/memory 2>/dev/null)
	psi_some=${1#avg10=}; psi_full=${2#avg10=}

	memavail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
	swap=$(awk '/^SwapFree/{f=$2} /^SwapTotal/{t=$2} END{print int((t-f)/1024)}' \
		/proc/meminfo)

	scope=$(eval ls -d $GLOB 2>/dev/null | head -1)
	if [ -n "$scope" ]; then
		cur=$(( $(field "$scope/memory.current") / 1048576 ))
		sw=$(( $(field "$scope/memory.swap.current") / 1048576 ))
		high=$(field "$scope/memory.high")
		[ "$high" != max ] && high=$(( high / 1048576 ))M
	else
		cur=-; sw=-; high=-
	fi

	# Resident, not Total: Total counts purged objects whose pages are gone.
	gem=$(awk '/^Resident/{gsub(/,/,"",$4); print int($4/1048576)}' \
		/sys/kernel/debug/dri/0/gem 2>/dev/null)
	gem=${gem:--}

	gpu=$(awk '{print int($1/1000000)}' \
		/sys/class/devfreq/*.gpu/cur_freq 2>/dev/null | head -1)
	gpu=${gpu:--}

	# hwdec=yes means the Venus DECODER is powered up, i.e. video is going
	# through hardware. "no" during playback means software decode, which on this
	# phone is the difference between a warm SoC and a hot one. Venus has no AV1
	# support at all and YouTube serves AV1 above 1080p, so "no" is a real
	# possible answer, not necessarily a broken probe.
	#
	# The decoder SUBDEVICE's runtime_status, not a scan of /proc/*/fd: the fd
	# scan forks readlink per descriptor and measured 97% of a core at 1 Hz --
	# an instrument that hot changes the temperature it is reporting. This is one
	# file read, 0 ms for ten of them. Validated against a live decode:
	# suspended -> active -> suspended.
	#
	# It only reads true while power/control is "auto". If something has pinned
	# the subdevice on for debugging, this pins to "yes" and means nothing --
	# hence the guard rather than a bare cat.
	if [ -n "$DECSUB" ] && [ "$(cat "$DECSUB/power/control" 2>/dev/null)" = auto ]; then
		[ "$(cat "$DECSUB/power/runtime_status" 2>/dev/null)" = active ] \
			&& dec=yes || dec=no
	elif [ -n "$DECSUB" ]; then
		dec=pinned
	else
		dec='?'
	fi

	# Hottest zone of all of them: which zone is hottest varies with the load,
	# so tracking one named zone reports the wrong peak half the time.
	tmax=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null |
		sort -n | tail -1)
	tmax=$(( ${tmax:-0} / 1000 ))

	printf 't=%ss psi_some=%s psi_full=%s memavail=%sM swapused=%sM scope=%sM scope_swap=%sM high=%s gem=%sM gpu=%sMHz tmax=%sC hwdec=%s\n' \
		"$up" "$psi_some" "$psi_full" "$memavail" "$swap" \
		"$cur" "$sw" "$high" "$gem" "$gpu" "$tmax" "$dec"

	n=$((n + 1))
	[ "$COUNT" -ne 0 ] && [ "$n" -ge "$COUNT" ] && break
	sleep "$INTERVAL"
done
