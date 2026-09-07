#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST
# exits: 0 ok · 1 failed
# ph-hang-matrix.sh -- the 2x2 attribution matrix for defect #3.
# Run on the HOST. Drives the phone, watches it from outside, records verdicts.
#
# WHAT IT IS FOR
#   Defect #3 is an unattributed hard hang under load -- seen with Firefox +
#   speedtest, and separately on toggling mobile data. The plan's rule is that
#   this matrix owns it and NO plan may claim the defect before the matrix runs.
#   Four cells separate radio from GPU:
#
#       wifi   WiFi only, no GPU
#       lte    LTE only,  no GPU
#       gpu    GPU only,  no network
#       both   LTE + GPU
#
#   A cell is "no hang" ONLY after 3 x 15 min clean. One clean run means nothing.
#
# TWO TRAPS, BOTH ENCODED BELOW BECAUSE BOTH INVALIDATE A RESULT SILENTLY
#   1. The LTE arms must use SO_BINDTODEVICE on qmapmux0.0. Binding only the
#      SOURCE ADDRESS still egresses via wlan0, so an "LTE" cell that does that
#      is really a second WiFi cell and the matrix proves nothing. ph-lte-load.py
#      sets the socket option properly.
#   2. GPU arms use glmark2-es2-wayland, NEVER -es2-drm. The DRM variant takes
#      the display away from the compositor and wedges the a540, which then gets
#      recorded as a hang that the test itself caused.
#
#   Also: do NOT run this and the acceptance soak at the same time. They compete
#   for the same failure and neither result survives the overlap.
#
# HANG DETECTION IS HOST-SIDE ON PURPOSE
#   A hard hang takes the device's own logging with it. The verdict comes from
#   ping + ssh from here, using the same split ph-supervise.sh uses: ping alive
#   with ssh dead is a PID-1 freeze, both dead is a SoC hang, and a watchdog
#   reset looks like a recovery -- so uptime is checked on the far side of every
#   event to tell a reset from a survival.
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/ph-lib.sh"

HOST=${TK_HOST:-$PORTHOLE_HOST}
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
MINUTES=${MINUTES:-15}
REPEATS=${REPEATS:-3}
CELLS=${CELLS:-"wifi lte gpu both"}
LOG=${LOG:-./hang-matrix-$(date +%Y%m%d-%H%M).log}

say() { echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }
sshq() { timeout 25 ssh "${TK_SSH_OPTS[@]}" "$PHONE" "$@" 2>/dev/null; }
alive() { timeout 10 ssh "${TK_SSH_OPTS[@]}" "$PHONE" true 2>/dev/null; }
pingable() { timeout 4 ping -c1 -W2 "$HOST" >/dev/null 2>&1; }
uptime_s() { sshq "cut -d' ' -f1 /proc/uptime" | cut -d. -f1; }

start_load() {
	case "$1" in
	wifi) sshq "systemd-run --unit=tk-load --collect \
	        iperf3 -c ping.online.net -t $((MINUTES*60+60))" ;;
	lte)  sshq "systemd-run --unit=tk-load --collect \
	        python3 /tmp/ph-lte-load.py $((MINUTES*60+60))" ;;
	gpu)  sshq "systemd-run --unit=tk-load --collect --setenv=XDG_RUNTIME_DIR=/run/user/10000 \
	        --setenv=WAYLAND_DISPLAY=wayland-0 --uid=10000 \
	        glmark2-es2-wayland --run-forever" ;;
	both) sshq "systemd-run --unit=tk-load --collect \
	        python3 /tmp/ph-lte-load.py $((MINUTES*60+60))"
	      sshq "systemd-run --unit=tk-load-gpu --collect --setenv=XDG_RUNTIME_DIR=/run/user/10000 \
	        --setenv=WAYLAND_DISPLAY=wayland-0 --uid=10000 \
	        glmark2-es2-wayland --run-forever" ;;
	esac
}
stop_load() { sshq "systemctl stop tk-load tk-load-gpu 2>/dev/null; true"; }

run_cell() {   # run_cell CELL RUN -> prints verdict
	local cell=$1 run=$2 t0 deadline
	t0=$(uptime_s)
	[ -z "$t0" ] && { say "  $cell/$run: phone not reachable at start -- SKIPPED"; return 2; }
	start_load "$cell"
	deadline=$(( $(date +%s) + MINUTES*60 ))
	while [ "$(date +%s)" -lt "$deadline" ]; do
		sleep 20
		if alive; then continue; fi
		# Not answering. Distinguish the three failures before calling it.
		sleep 10
		if alive; then continue; fi
		if pingable; then
			say "  $cell/$run: HANG -- ping alive, ssh dead (PID-1 freeze signature)"
		else
			say "  $cell/$run: HANG -- both dead (SoC)"
		fi
		return 1
	done
	stop_load
	local t1; t1=$(uptime_s)
	if [ -z "$t1" ]; then say "  $cell/$run: unreachable at end"; return 1; fi
	if [ "$t1" -lt "$t0" ]; then
		say "  $cell/$run: REBOOTED during the run (uptime went backwards) -- counts as a hang"
		return 1
	fi
	say "  $cell/$run: clean (${MINUTES}m, uptime ${t0}s -> ${t1}s)"
	return 0
}

say "=== 2x2 hard-hang matrix: cells [$CELLS], ${REPEATS} x ${MINUTES}m each ==="
say "reminder: a cell is 'no hang' only after ${REPEATS} clean runs"
for cell in $CELLS; do
	clean=0
	for run in $(seq 1 "$REPEATS"); do
		say "cell $cell, run $run/$REPEATS"
		if run_cell "$cell" "$run"; then
			clean=$((clean + 1))
		else
			say ">> $cell: HUNG on run $run -- cell is ATTRIBUTED, stopping this cell"
			say ">> recover the phone before continuing (tools/ph-recover.sh)"
			break
		fi
	done
	[ "$clean" -eq "$REPEATS" ] && say ">> $cell: NO HANG ($clean/$REPEATS clean)"
done
say "=== done; verdicts above, full log in $LOG ==="
