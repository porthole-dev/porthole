#!/bin/bash
# scope: generic
# needs: any (probes state; handles BOOTED and FASTBOOT)
# env: FASTBOOT, HOST, PHONE, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST, TK_RESCUE_PORT
# exits: 0 ok · 1 failed
# tk-supervise.sh -- keep the phone alive during unattended work.
#
# Runs on the HOST, in the background, for the whole session. It watches for the
# four states tk-recover.sh distinguishes and fixes the three that are fixable
# without hands:
#
#   BOOTED    ssh answers                    -> nothing to do
#   FROZEN    ping answers, ssh does not     -> PID-1 freeze; poke the :2323
#             rescue channel for a reboot, which is PAM-free and therefore
#             still works when logind is wedged
#   FASTBOOT  18d1:4ee0                      -> `fastboot reboot`
#   ABSENT    nothing on USB                 -> needs a human. EXIT non-zero so
#             the agent driving this session is re-invoked and can say so.
#
# It never flashes. Reflashing unattended is how you turn a recoverable phone
# into a brick, and the one incident this session that needed hands (a suspend
# hard-hang, 2026-08-02) would not have been helped by it.
#
# ponytail: no state machine, no config file. A loop and four cases.
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"

HOST=${TK_HOST:-$PORTHOLE_HOST}
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
PORT=${TK_RESCUE_PORT:-2323}
INTERVAL=${1:-30}
LOG=${2:-/tmp/tk-supervise.log}
FAILS=0

say() { echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }

# See tk-recover.sh: a changed host key must not read as "ssh is dead", or
# this supervisor reboots a healthy phone on the FROZEN branch.
ssh_ok()  { timeout 8 ssh "${TK_SSH_OPTS[@]}" \
	-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
	-o LogLevel=ERROR "$PHONE" true 2>/dev/null; }
ping_ok() { timeout 4 ping -c1 -W2 "$HOST" >/dev/null 2>&1; }
fb_ok()   { [ -n "$(timeout 5 fastboot devices 2>/dev/null)" ]; }

# Write a pidfile rather than relying on pgrep: `pgrep -f tk-supervise.sh`
# matches the very shell that runs the check, so it reports RUNNING when
# nothing is. That false positive cost real time on 2026-08-02 -- twice.
#   is it up?   kill -0 "$(cat /run/tk-supervise.pid 2>/dev/null)" 2>/dev/null
echo $$ > /run/tk-supervise.pid 2>/dev/null || echo $$ > /tmp/tk-supervise.pid
say "supervising $HOST every ${INTERVAL}s (pid $$)"
while :; do
	if ssh_ok; then
		[ "$FAILS" -gt 0 ] && say "recovered: ssh answering again"
		FAILS=0
		sleep "$INTERVAL"; continue
	fi

	FAILS=$((FAILS + 1))
	say "ssh not answering (strike $FAILS)"
	# Two strikes before acting: a reboot we asked for looks identical to a
	# failure for ~35 s, and acting on the first miss just fights it.
	[ "$FAILS" -lt 2 ] && { sleep "$INTERVAL"; continue; }

	if fb_ok; then
		say "state FASTBOOT -> fastboot reboot"
		timeout 15 fastboot reboot >/dev/null 2>&1
		sleep 45; FAILS=0; continue
	fi

	if ping_ok; then
		# ping alive + ssh dead is the PID-1 freeze signature, and the rescue
		# channel is the only way in, precisely because it skips PAM.
		say "state FROZEN (ping ok, ssh dead) -> reboot via rescue :$PORT"
		echo 'sudo -n systemctl reboot -f || sudo -n reboot -f' \
			| timeout 10 nc "$HOST" "$PORT" >>"$LOG" 2>&1
		sleep 60; FAILS=0; continue
	fi

	if [ "$FAILS" -ge 6 ]; then
		say "state ABSENT for ~$((FAILS * INTERVAL))s -- needs a human (long-press power)"
		exit 1
	fi
	sleep "$INTERVAL"
done
