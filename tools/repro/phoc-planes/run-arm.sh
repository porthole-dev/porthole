#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: the device BOOTED and the arm script already staged in /tmp on it
# env: PORTHOLE_* (ph-lib.sh)
# exits: 0 the arm finished · 1 it never printed its DONE marker
# run-arm.sh SCRIPT MARKER [SECONDS]  -- run a long device arm detached and poll
# for its output file.
#
# A single `tk_run "sh /tmp/longarm.sh"` loses everything the arm printed when
# the ssh connection drops mid-run, which it does on arms of a few minutes here.
# Detaching on the phone and polling a file survives that: the arm keeps running
# and its output is on disk either way.
set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 1
source tools/ph-lib.sh
SCRIPT=${1:?arm script on the device, e.g. /tmp/planearm.sh}
MARKER=${2:?the line the arm prints when it is done, e.g. PLANEARMDONE}
SECS=${3:-420}
OUT=/tmp/arm-$(basename "$SCRIPT" .sh).out

tk_run "rm -f $OUT; setsid sh -c 'sh $SCRIPT > $OUT 2>&1' </dev/null >/dev/null 2>&1 &" >/dev/null
END=$(tk_deadline_ms "$SECS")
while ! tk_expired "$END"; do
    if tk_run "grep -q '$MARKER' $OUT" 2>/dev/null; then
        tk_run "cat $OUT"
        exit 0
    fi
done
echo ">> arm did not reach $MARKER in ${SECS}s; what it printed so far:" >&2
tk_run "cat $OUT" 2>/dev/null
exit 1
