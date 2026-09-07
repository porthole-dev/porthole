#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: runs ON THE DEVICE; /tmp/sess.sh and what ph-scrollarm.sh needs
# env: TK_SCROLL_URL, PORTHOLE_SCROLL_DRAG
# exits: 0 measured
# cpuarm.sh LABEL -- one scroll arm with per-thread CPU residency sampled
# during the drag, to see which cluster the browser's threads actually use.
set -u
L=${1:?usage: cpuarm.sh LABEL}
. /tmp/sess.sh
export PORTHOLE_SCROLL_HOOK='p=$(pgrep -f WebKitWebProcess | tail -1); echo "watching pid $p"; (sudo -n python3 /tmp/ph-threadcpus.py "$p" 14 20 > /tmp/cpures.txt 2>&1) &'
sh /tmp/ph-scrollarm.sh "$L" 2>&1 | grep -E "scrollHeight|client wl_surface|client presented|scrollY|arm void|watching"
echo "=== thread CPU residency ==="
cat /tmp/cpures.txt 2>/dev/null
echo CPUARMDONE
