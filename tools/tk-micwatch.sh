#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST, TK_UID
# exits: 0 ok · non-zero on failure
# tk-micwatch.sh -- sample whether anything is holding the microphone open.
#
# WHAT THIS SETTLES
#   The plan carried an "always-open idle microphone capture stream" as a
#   shared confound for suspend, cpuidle and every idle-draw number, budgeted
#   at hours, and blamed on callaudiod. On the 2026-08-02 image it DOES NOT
#   REPRODUCE: with phosh and callaudiod both running, source-outputs is empty,
#   sink-inputs is empty, both sources read SUSPENDED, and the only /dev/snd fd
#   on the system is pulseaudio holding controlC0 -- a mixer handle, not a PCM.
#
#   Nothing was repaired. A claimed blocker turned out not to exist. This tool
#   is what makes that a measurement instead of a snapshot, and what re-checks
#   it cheaply after any audio change.
#
# TWO TRAPS IT EXISTS TO AVOID (both produced wrong answers first)
#   * busybox `fuser` has no -v. The plan's command errors out and prints a
#     usage block that reads like "nothing is holding it".
#   * bare `pactl` over ssh can autospawn a SECOND pulseaudio and report an
#     empty, entirely truthful picture of a server nobody is using. So we pin
#     XDG_RUNTIME_DIR and assert exactly one pulseaudio is running.
#
# Usage: tk-micwatch.sh [seconds]      (default: sample for 10 min, then report)
#        tk-micwatch.sh daemon         (run on the device until stopped)
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"

HOST=${TK_HOST:-$PORTHOLE_HOST}
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
LOG=/tmp/tk-micwatch.log
UID_ON_PHONE=${TK_UID:-10000}

# One sampler body, used both by the daemon unit and the one-shot poll.
read -r -d '' PROBE <<PROBE_EOF
export XDG_RUNTIME_DIR=/run/user/$UID_ON_PHONE
np=\$(pgrep -c pulseaudio)
so=\$(pactl list short source-outputs 2>/dev/null | wc -l)
si=\$(pactl list short sink-inputs 2>/dev/null | wc -l)
run=\$(pactl list short sources 2>/dev/null | grep -c RUNNING)
srv=\$(pactl info 2>/dev/null | sed -n 's/^Server String: //p')
# The PCM itself, not pulse's view of it -- walk /proc rather than use fuser.
pcm=\$(for d in /proc/[0-9]*/fd; do for f in \$d/*; do
        case "\$(readlink "\$f" 2>/dev/null)" in /dev/snd/pcm*) echo x;; esac
      done; done 2>/dev/null | wc -l)
echo "\$(date +%H:%M:%S) pulses=\$np source_outputs=\$so sink_inputs=\$si running_sources=\$run pcm_fds=\$pcm srv=\$srv"
PROBE_EOF

case "${1:-600}" in
daemon)
	ssh "$PHONE" "export XDG_RUNTIME_DIR=/run/user/$UID_ON_PHONE
	  systemd-run --user --unit=tk-micwatch --collect /bin/sh -c \
	    'while :; do { $PROBE ; } >> $LOG; sleep 30; done'"
	echo ">> sampling to $LOG on the device; stop with:"
	echo "   ssh $PHONE 'XDG_RUNTIME_DIR=/run/user/$UID_ON_PHONE systemctl --user stop tk-micwatch'"
	;;
*)
	secs=$1
	echo ">> sampling for ${secs}s (30 s interval)"
	end=$((SECONDS + secs))
	bad=0 n=0
	while [ $SECONDS -lt $end ]; do
		line=$(ssh "$PHONE" "$PROBE" 2>/dev/null)
		echo "   $line"
		n=$((n + 1))
		# A second pulseaudio makes every other field meaningless, so it
		# counts as a bad sample rather than a clean one.
		case "$line" in
			*"pulses=1 "*"source_outputs=0 "*"pcm_fds=0"*) ;;
			*) bad=$((bad + 1)) ;;
		esac
		sleep 30
	done
	echo ">> $n samples, $bad with a capture stream (or an ambiguous server)"
	[ "$bad" -eq 0 ] && echo ">> CLEAN: nothing held the mic." || echo ">> A capture stream is back -- re-open plan item 5."
	exit $((bad > 0))
	;;
esac
