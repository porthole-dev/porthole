#!/bin/bash
# scope: generic
# needs: - (host only, no device)
# exits: 0 ok · 64 usage · 75 lock unavailable
# Serialise KERNEL BUILDS across parallel agents.
#
# Why this exists: `source envkernel.sh` bind-mounts the kernel tree onto
# chroot_native/mnt/linux in the SHARED pmbootstrap chroot. There is exactly one
# such mount point, so two agents building two different worktrees silently
# corrupt each other -- the second mount stacks on the first and `make` builds a
# tree that is not the one you think it is. That has already cost a session.
#
# tk-device.sh guards the phone. This guards the chroot. Same flock pattern, so
# there is one idiom to learn, not two.
#
# Usage:  tools/tk-build.sh <command> [args...]
#   env:  TK_BUILD_LOCK     lock path (default /tmp/taimen-build.lock)
#         TK_BUILD_TIMEOUT  seconds to wait (default 5400 -- kernel builds are slow)
#         TK_AGENT          holder label, for the "who has it" file
#
# Exit 75 (EX_TEMPFAIL) on timeout, distinct from the wrapped command failing.
#
# ponytail: flock, not a build queue. If we ever need to build on more than one
# host, that is the upgrade path -- flock is per-machine only.
set -euo pipefail

LOCK=${TK_BUILD_LOCK:-/tmp/taimen-build.lock}
TIMEOUT=${TK_BUILD_TIMEOUT:-5400}
AGENT=${TK_AGENT:-unknown}

[ $# -gt 0 ] || { echo "usage: $0 <command> [args...]" >&2; exit 64; }

exec 9>"$LOCK"
if ! flock -w "$TIMEOUT" 9; then
    echo "tk-build: timed out after ${TIMEOUT}s waiting for $LOCK" >&2
    echo "tk-build: holder: $(cat "$LOCK.holder" 2>/dev/null || echo unknown)" >&2
    exit 75
fi

printf 'agent=%s pid=%s since=%s cmd=%s\n' \
    "$AGENT" "$$" "$(date -Is)" "$*" >"$LOCK.holder"
trap 'rm -f "$LOCK.holder"' EXIT

"$@" 9>&-
