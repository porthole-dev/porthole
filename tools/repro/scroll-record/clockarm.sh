#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: runs ON THE DEVICE. /tmp/sess.sh and everything ph-scrollarm.sh needs,
#        staged in /tmp. A page served locally (see TK_SCROLL_URL).
# env: TK_SCROLL_URL, CLK_MIN (gpu devfreq min_freq, or "-"), CLK_POLL
#      (polling_interval ms, or "-"), ARMS (default 3)
# exits: 0 measured
# clockarm.sh LABEL -- N scroll arms under one clock condition, printing only
# the numbers that discriminate: how many frames the CLIENT presented late.
#
# The compositor's own rate is not the metric. phoc reports 59.9 fps and zero
# jank while the browser is presenting every fourth vsync -- see the trap of
# that name. What a user sees is the client's presentation cadence.
set -u
L=${1:?usage: clockarm.sh LABEL}
D=$(ls -d /sys/class/devfreq/*gpu* | head -1)
ARMS=${ARMS:-3}
. /tmp/sess.sh

restore_min=$(cat "$D/min_freq")
restore_poll=$(cat "$D/polling_interval")
[ "${CLK_MIN:--}" = "-" ] || sudo -n sh -c "echo ${CLK_MIN} > $D/min_freq"
[ "${CLK_POLL:--}" = "-" ] || sudo -n sh -c "echo ${CLK_POLL} > $D/polling_interval"
echo "[$L] gpu min=$(cat "$D/min_freq") max=$(cat "$D/max_freq") poll=$(cat "$D/polling_interval")"

i=1
while [ "$i" -le "$ARMS" ]; do
	sh /tmp/ph-scrollarm.sh "$L$i" 2>&1 | grep -E "client wl_surface|client presented|scrollY|arm void|scrollHeight"
	python3 /tmp/ph-wlgaps.py "/tmp/wl-$L$i.log.drag" 6 2>&1 | tail -12
	i=$((i + 1))
done

sudo -n sh -c "echo $restore_min > $D/min_freq; echo $restore_poll > $D/polling_interval"
echo "[$L] restored min=$(cat "$D/min_freq") poll=$(cat "$D/polling_interval")"
echo CLOCKARMDONE
