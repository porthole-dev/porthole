#!/bin/bash
# scope: generic
# needs: BOOTED
# env: HOST, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST, TK_RESCUE_PORT
# exits: 0 ok · non-zero on failure
# lib-exempt: detecting the PAM stall REQUIRES a raw ssh with a fixed timeout; tk_boot_id retries, which is exactly what would mask the signature
# stallwatch.sh -- detect the taimen PID-1 freeze from the HOST.
#
# THE SIGNATURE THIS EXISTS FOR
#   PID 1 takes a SIGSEGV and freezes. ICMP still answers and running daemons
#   keep serving, so every naive "is it up?" check passes. What actually breaks
#   is opening a NEW session: ssh hangs in PAM (pam_systemd -> logind -> bus ->
#   frozen PID 1). So the freeze is precisely:
#
#       ping SUCCEEDS  AND  ssh TIMES OUT
#
#   Neither half alone means anything. A dead phone fails both; a healthy phone
#   passes both. Only the split says "frozen".
#
#   This is also why you must never conclude a suspend test from the network:
#   a watchdog reset takes ~30 s and comes back looking identical to a resume.
#   On any suspect event this grabs uptime + bootreason via the rescue channel,
#   which is what tells a freeze from a reset.
#
# Usage: stallwatch.sh [interval_seconds] [logfile]
set -u

HOST=${TK_HOST:-$PORTHOLE_HOST}
RESCUE_PORT=${TK_RESCUE_PORT:-2323}
INTERVAL=${1:-10}
LOG=${2:-stallwatch-$(date +%Y%m%d-%H%M%S).log}

ts() { date '+%Y-%m-%d %H:%M:%S'; }
say() { echo "$(ts) $*" | tee -a "$LOG"; }

# Ask the PAM-free rescue channel, which works even when ssh cannot open a
# session. Falls back to reporting that the channel is down.
rescue() {
	local cmd=$1
	if command -v nc >/dev/null 2>&1; then
		printf '%s\nexit\n' "$cmd" | timeout 8 nc "$HOST" "$RESCUE_PORT" 2>/dev/null
	else
		echo "<no nc on host>"
	fi
}

say "stallwatch: host=$HOST interval=${INTERVAL}s log=$LOG"
say "watching for: ping OK + ssh TIMEOUT (the PID-1 freeze signature)"

prev_state=init
while :; do
	if timeout 3 ping -c1 -W2 "$HOST" >/dev/null 2>&1; then
		ping_ok=1
	else
		ping_ok=0
	fi

	# BatchMode so it fails instead of prompting; short timeout so a hung PAM
	# shows up as a timeout rather than blocking this loop forever.
	if timeout 12 ssh -o BatchMode=yes -o ConnectTimeout=5 \
		-o StrictHostKeyChecking=no "$PORTHOLE_USER@$HOST" true >/dev/null 2>&1; then
		ssh_ok=1
	else
		ssh_ok=0
	fi

	if [ $ping_ok -eq 1 ] && [ $ssh_ok -eq 1 ]; then
		state=healthy
	elif [ $ping_ok -eq 1 ] && [ $ssh_ok -eq 0 ]; then
		state=FROZEN
	elif [ $ping_ok -eq 0 ]; then
		state=down
	fi

	if [ "$state" != "$prev_state" ]; then
		say "STATE: $prev_state -> $state (ping=$ping_ok ssh=$ssh_ok)"
		if [ "$state" = FROZEN ]; then
			say "  --- PID-1 FREEZE SUSPECTED; querying rescue channel :$RESCUE_PORT"
			say "  uptime:     $(rescue 'uptime')"
			say "  bootreason: $(rescue "tr ' ' '\\n' < /proc/cmdline | grep bootreason")"
			say "  pid1 state: $(rescue 'cat /proc/1/stat | cut -d\" \" -f3')"
			say "  pid1 stack: $(rescue 'cat /proc/1/stack 2>/dev/null | head -5')"
			say "  load:       $(rescue 'cat /proc/loadavg')"
		fi
		if [ "$state" = healthy ] && [ "$prev_state" = down ]; then
			# Came back on its own => almost certainly a watchdog reset, NOT a
			# resume and NOT a recovery. Record the evidence that distinguishes
			# them instead of guessing.
			say "  --- CAME BACK; this is a RESET until proven otherwise"
			say "  uptime:     $(timeout 8 ssh -o BatchMode=yes -o ConnectTimeout=5 "$PORTHOLE_USER@$HOST" 'uptime' 2>&1)"
			say "  bootreason: $(timeout 8 ssh -o BatchMode=yes -o ConnectTimeout=5 "$PORTHOLE_USER@$HOST" "tr ' ' '\\n' < /proc/cmdline | grep bootreason" 2>&1)"
		fi
		prev_state=$state
	fi

	sleep "$INTERVAL"
done
