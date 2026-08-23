#!/bin/bash
# scope: generic
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_USER, TK_PUSH
# exits: 0 ok · 1 failed
# Stream a command's output from the phone to a host file, across reboots.
#
# Run this BEFORE the phone boots. Plain `ssh phone 'dmesg -w'` dies the moment
# the device reboots and never comes back, which is exactly when the interesting
# lines appear -- so this reconnects forever and marks every reconnect, making a
# reboot obvious in the log.
#
# `dmesg -w` replays the whole ring buffer before following it, so reconnecting
# as soon as sshd is up still captures everything back to the first line of the
# boot. That only holds while the buffer has not wrapped, hence the tight retry.
#
# Host keys change when the rootfs is reflashed, so host key checking is off
# here deliberately -- this is a debug channel on a USB-local link, and having
# the stream die on a changed key defeats the point.
#
# Set TK_PUSH to a local script to copy it to /tmp on the phone before each
# run. The phone's /tmp is a tmpfs, so anything pushed there is gone after a
# reboot -- which is precisely when a reconnect happens.
#
# Usage: tk-stream.sh OUTFILE REMOTE_COMMAND...
#   eg:  tk-stream.sh /tmp/dmesg.log dmesg -w
#        tk-stream.sh /tmp/syslog.log logread -f
#        TK_PUSH=tools/tk-display-watch.py \
#          tk-stream.sh /tmp/display.log sudo python3 -u /tmp/tk-display-watch.py
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"

PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
HOST=${PHONE#*@}

OUT=${1:?usage: tk-stream.sh OUTFILE REMOTE_COMMAND...}
shift
[ $# -ge 1 ] || { echo "usage: tk-stream.sh OUTFILE REMOTE_COMMAND..."; exit 1; }

SSH_OPTS=(-o ConnectTimeout=5
          -o StrictHostKeyChecking=no
          -o UserKnownHostsFile=/dev/null
          -o LogLevel=ERROR
          -o ServerAliveInterval=10
          -o ServerAliveCountMax=2)

echo "=== stream started $(date -Is): $* ===" >> "$OUT"

while true; do
    if ping -c1 -W2 "$HOST" >/dev/null 2>&1; then
        if [ -n "${TK_PUSH:-}" ]; then
            scp "${SSH_OPTS[@]}" "$TK_PUSH" "$PHONE:/tmp/" >/dev/null 2>&1
        fi
        echo "=== connected $(date -Is) ===" >> "$OUT"
        ssh "${SSH_OPTS[@]}" "$PHONE" "$@" >> "$OUT" 2>/dev/null
        echo "=== disconnected $(date -Is) ===" >> "$OUT"
    fi
    sleep 2
done
