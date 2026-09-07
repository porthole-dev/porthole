#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: any (probes state; handles BOOTED and FASTBOOT)
# env: FASTBOOT, HOST, PORTHOLE_DEVICE, TK_AGENT, TK_DEVICE_LOCK, TK_DEVICE_MAX, TK_DEVICE_TIMEOUT
# exits: 0 ok · 64 usage · 75 lock unavailable · 76 wrong device state · 124 timed out at the hold ceiling
# Serialise access to the ONE physical phone across parallel agents.
#
# Usage:  tools/ph-device.sh [--need-booted|--need-fastboot] <command> [args...]
#
# The flags are OPT-IN and the no-flag behaviour is unchanged. They exist
# because the lock serialises the USB device and knows nothing about what the
# phone is doing: on 2026-08-19 an agent queued ten minutes for a phone that
# another agent had left in the bootloader, then failed at its own ceiling.
# Either flag fails fast with exit 76 BEFORE queueing, and again after the
# lock is taken (the state can change while you wait), naming the state it
# actually found. The observed state is recorded in $LOCK.holder either way,
# so the next agent's timeout message says what the phone was doing.
#
# Why flock and not a lockfile: flock is race-free across processes and the
# kernel releases the lock when the holding process dies. A hand-rolled
# lockfile gets exactly that case wrong, and a crashed agent then wedges every
# other agent until someone notices.
#
# ponytail: flock over a lock daemon. If cross-HOST locking is ever needed,
# that is the upgrade path -- flock is per-machine only.
set -euo pipefail

# Namespaced by device: two phones on one desk are two resources, and sharing
# one lock between them serialises work that never needed serialising. Falls
# back to a generic name so the mutex still works before a device is selected.
LOCK=${TK_DEVICE_LOCK:-/tmp/porthole-${PORTHOLE_DEVICE:-device}.lock}
# TWO DIFFERENT TIMEOUTS -- do not confuse them:
#   TK_DEVICE_TIMEOUT  how long we WAIT for the lock before giving up (exit 75)
#   TK_DEVICE_MAX      how long the wrapped command may HOLD the lock (exit 124)
TIMEOUT=${TK_DEVICE_TIMEOUT:-900}
MAXHOLD=${TK_DEVICE_MAX:-1800}
AGENT=${TK_AGENT:-unknown}

# shellcheck source=ph-lib.sh
. "$(dirname "$0")/ph-lib.sh"

NEED=
while [ $# -gt 0 ]; do
    case $1 in
        --need-booted)   NEED=BOOTED;   shift ;;
        --need-fastboot) NEED=FASTBOOT; shift ;;
        --)              shift; break   ;;
        *)               break          ;;
    esac
done

[ $# -gt 0 ] || {
    echo "usage: $0 [--need-booted|--need-fastboot] <command> [args...]" >&2
    exit 64
}

# 76 (EX_PROTOCOL) -- deliberately distinct from 75 "could not get the lock"
# and from the wrapped command's own status. "Wrong state" is not retryable by
# waiting; something has to move the phone first.
wrong_state() {
    echo "tk-device: phone is $1, and this command needs $NEED" >&2
    echo "tk-device: agent=$AGENT cmd=$CMD" >&2
    echo "tk-device: see brain/playbooks/00-device-protocol.md -- do NOT recover someone else's experiment" >&2
    exit 76
}

CMD=$*

# </dev/null on every probe: ssh forwards stdin to the remote command, and
# callers legitimately pipe a script in (`ph-device.sh 'sh -s' < foo.sh`).
# Without this the probe eats their script and they run an empty one.
#
# Before the queue, so a wrong-state caller does not burn TK_DEVICE_TIMEOUT.
if [ -n "$NEED" ]; then
    state=$(tk_device_state </dev/null)
    [ "$state" = "$NEED" ] || wrong_state "$state"
fi

exec 9>"$LOCK"
if ! flock -w "$TIMEOUT" 9; then
    echo "tk-device: timed out after ${TIMEOUT}s waiting for $LOCK" >&2
    echo "tk-device: holder: $(cat "$LOCK.holder" 2>/dev/null || echo unknown)" >&2
    exit 75   # EX_TEMPFAIL -- retryable, distinct from the command failing
fi

# Re-probe: whoever held the lock may have moved the phone while we waited.
# This also fills in state= for the holder file, which is what turns the next
# agent's timeout message from "someone has it" into "someone has it and the
# phone is in the bootloader".
#
# Here the state is an ANNOTATION, not a precondition: a caller that named no
# --need-* wants the mutex, and has said nothing about the device. So ask only
# if the probe can answer -- tk_device_state reaches tk_in_fastboot, which
# exits 69 when $FASTBOOT cannot run, and that must not take down a run that
# never asked. Unknown is already this file's word for it, two messages up.
# A caller that DID name --need-* was refused before the lock, above.
#
# TK_DEVICE_STATE is checked first because tk_device_state honours it before
# it probes anything, so an override still annotates the holder file on a host
# that has no fastboot at all.
if [ -n "${TK_DEVICE_STATE:-}" ] || ph_have_fastboot; then
    state=$(tk_device_state </dev/null)
else
    state=unknown
fi
printf 'agent=%s pid=%s since=%s state=%s cmd=%s\n' \
    "$AGENT" "$$" "$(date -Is)" "$state" "$*" >"$LOCK.holder"
trap 'rm -f "$LOCK.holder"' EXIT
[ -z "$NEED" ] || [ "$state" = "$NEED" ] || wrong_state "$state"

# Close fd 9 for the wrapped command (and anything it backgrounds): otherwise
# a detached child inherits the lock fd and keeps holding it after we exit and
# our EXIT trap has already deleted .holder -- a silent wedge with no holder
# to blame.
#
# The ceiling exists to break WEDGES, not to discipline long measurements, so
# the default is deliberately generous: a CPU benchmark legitimately held the
# device for well over ten minutes on 2026-08-10, and a ceiling that kills a
# valid measurement is a worse bug than the one it fixes. A caller that
# genuinely needs longer sets TK_DEVICE_MAX explicitly.
#
# What it is really for: a tool that deliberately induces a reset -- suspend
# and pm_test work -- must treat "the phone stopped answering" as the EXPECTED
# outcome, and so must put a timeout on every ssh. ph-suspend-cycle.sh does
# this correctly via its S() helper, which is why its suspend arms never wedged
# the lock. An ad-hoc `ssh ... ; ssh ...` one-liner does not, and one held the
# lock for ten minutes against every other agent that day. This is the backstop
# for the callers that forget.
# set +e: we must observe the exit code, not die on it -- a failing wrapped
# command is normal and its status belongs to the caller.
set +e
timeout "$MAXHOLD" "$@" 9>&-
rc=$?
set -e
if [ "$rc" -eq 124 ]; then
    echo "tk-device: KILLED the wrapped command after ${MAXHOLD}s holding $LOCK" >&2
    echo "tk-device: agent=$AGENT cmd=$*" >&2
    echo "tk-device: this is a wedge, not a command failure -- raise TK_DEVICE_MAX" >&2
    echo "tk-device: if the command legitimately needs longer than ${MAXHOLD}s." >&2
fi
exit "$rc"
