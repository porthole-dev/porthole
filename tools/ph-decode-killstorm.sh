#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (gstreamer + a v4l2 stateful decoder; kill by PID only)
# env: -
# exits: 0 no wedge in N rounds · 1 a post-kill decode failed
# ph-decode-killstorm.sh -- SIGKILL a hardware decode mid-flight, N times,
# verifying after every kill that the NEXT decode still completes.
#
# Exists because "SIGKILL mid-decode wedges venus until reboot" was reported,
# believed for a day, and then refuted by exactly this loop (12/12 survived,
# both codecs; brain finding
# the-sigkill-venus-wedge-was-vp9-bandwidth-starvation). A decoder teardown
# claim without a kill-storm behind it is an anecdote. The probe timeout must
# comfortably exceed the SLOWEST honest decode of the clip -- a starved-DDR
# VP9 run took 34 s for a 20 s clip and a short timeout misreads that as a
# wedge, which is precisely how the original misdiagnosis happened.
#
#   ph-decode-killstorm.sh [DECODER] [CLIP] [ROUNDS] [PROBE_TIMEOUT_S]
#   ph-decode-killstorm.sh v4l2vp9dec /tmp/clip.webm 10 90
DEC=${1:-v4l2h264dec}; CLIP=${2:-/tmp/h264_1080p.mp4}; N=${3:-10}; T=${4:-70}
case $CLIP in
*.webm) DEMUX="matroskademux ! queue";;
*) DEMUX="qtdemux ! h264parse";;
esac
[ -f "$CLIP" ] || { echo "no clip at $CLIP"; exit 1; }
i=0
while [ "$i" -lt "$N" ]; do
	i=$((i+1))
	setsid gst-launch-1.0 -q filesrc location="$CLIP" ! $DEMUX ! "$DEC" \
		! fakesink sync=false >/dev/null 2>&1 &
	GPID=$!
	# random-ish 200-3200 ms into the decode
	R=$(( ($(date +%s%N 2>/dev/null || echo $$) % 3000) + 200 ))
	usleep $((R * 1000)) 2>/dev/null || sleep "$(awk "BEGIN{print $R/1000}")"
	kill -9 "$GPID" 2>/dev/null
	sleep 1
	if timeout "$T" gst-launch-1.0 -q filesrc location="$CLIP" ! $DEMUX \
		! "$DEC" ! fakesink sync=false >/dev/null 2>&1; then
		echo "round $i: killed at ${R}ms, next decode OK"
	else
		echo "round $i: killed at ${R}ms, NEXT DECODE FAILED (rc=$?)"
		echo "before calling it a wedge: is the decode merely SLOWER than ${T}s?"
		exit 1
	fi
done
echo "no wedge in $N rounds ($DEC)"
