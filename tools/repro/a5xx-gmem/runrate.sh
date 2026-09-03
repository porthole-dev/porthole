#!/bin/bash
# NOTE: set A5XX_WORK to a writable scratch dir (default /tmp/a5xx-gmem).
# Paths were de-hardcoded from the original session scratchpad.
set -uo pipefail
cd /home/user/src/taimen
source tools/tk-lib.sh
TAG=$1
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); source "$HERE/session_state.sh"
wait_ready "${WAIT_TITLE:-strip detector}" || { echo "run is invalid"; exit 9; }
scp "${TK_SSH_OPTS[@]}" "$HERE/rate.sh" "$PHONE:/tmp/" >/dev/null
TK_RUN_TIMEOUT=300 tk_run "sh /tmp/rate.sh $TAG 40"
scp "${TK_SSH_OPTS[@]}" "$PHONE:/tmp/rate-$TAG.tar" "${A5XX_WORK:-/tmp/a5xx-gmem}/" >/dev/null
