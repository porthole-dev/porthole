#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: BOOTED
# env: PHONE, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST, PORTHOLE_DT2W_WAIT
# exits: 0 a double tap was reported · 1 armed and none was · 65 nobody came to the phone · 70 harness or setup failed
# Prove double-tap-to-wake at the CONTROLLER, without a suspend and without
# asking anyone to hit a window.
#
#   ph-dt2w-test.sh [SECONDS_PER_PHASE]      default 600
#
# IT WAITS FOR THE OPERATOR, NOT THE OTHER WAY ROUND. Three designs failed on
# 2026-09-20 before this one. Two announced a time to tap and measured nothing
# because nobody was watching the terminal. The third put an awk program inside
# an `sh -c '...'` inside ssh, and busybox ash's "syntax error: bad for loop
# variable" was then reported to the operator as "nobody touched the phone in
# 600s" -- a harness failure wearing a device result's clothes.
#
# So: all device logic lives in ph-dt2w-probe.py, which is COPIED to the phone
# and run as a file. Nothing here quotes a program into a shell.
#
# Start it and walk to the phone whenever; it ends the moment it has an answer.
set -u
. "$(dirname "$0")/../../../tools/ph-lib.sh"

WAIT=${1:-${PORTHOLE_DT2W_WAIT:-600}}
PROBE="$(dirname "$0")/ph-dt2w-probe.py"
[ -r "$PROBE" ] || { echo "missing $PROBE" >&2; exit 70; }

scp -q "${TK_SSH_OPTS[@]}" "$PROBE" "${PHONE:-$PORTHOLE_USER@$HOST}:/tmp/ph-dt2w-probe.py" ||
	{ echo "could not copy the probe to the phone" >&2; exit 70; }

echo ">>>> Go to the phone whenever you like, within ${WAIT}s."
echo ">>>> 1. TOUCH THE SCREEN once. Nothing is armed yet; this is the control"
echo ">>>>    that proves a finger is there, and it starts the test."
echo ">>>> 2. Then DOUBLE-TAP the middle, twice quickly, and repeat."
echo ">>>>    Ordinary touch is dead while armed -- that is the mode working."
echo

# +120 so the ssh channel outlives both phases; the probe owns the deadline.
TK_RUN_TIMEOUT=$((2 * WAIT + 120)) tk_run \
	"sudo -n python3 /tmp/ph-dt2w-probe.py $WAIT"
rc=$?

case $rc in
0)  echo; echo "DOUBLE TAP WORKS at the controller. ftm4_suspend() arms this"
    echo "exact state, so the suspend case follows from it." ;;
1)  echo; echo "NO DOUBLE TAP while armed. The mode engaged and the gesture was"
    echo "not recognised -- chase the parameters, not the wake path." ;;
65) echo; echo "NO RESULT: nobody came to the phone. Nothing was armed and"
    echo "nothing changed. This is not a result about double tap." ;;
*)  echo; echo "HARNESS OR SETUP FAILURE (rc=$rc) -- read the lines above."
    echo "This is NOT a statement about the controller." ; rc=70 ;;
esac
exit $rc
