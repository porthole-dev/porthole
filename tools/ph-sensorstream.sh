#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
# ph-sensorstream.sh -- stream the REAL sensor path, imx179 -> CSIPHY -> CSID ->
# ISPIF -> VFE, as opposed to ph-tgstream.sh which cuts the CSIPHY out and feeds
# the CSID's own test generator.
#
# This is the pipeline for blocker #1 (the sensor transmits, every CSIPHY is
# deaf). Keeping it in a script matters because the CSIPHY's clocks are only up
# while the pipeline is streaming -- reading their rates at idle shows XO and
# means nothing.
#
# Run ON the phone.
#
# ponytail: subdev nodes resolved BY NAME, same as ph-tgstream.sh -- v4l-subdevN
# renumbers on every module reload.
set -u

MEDIA=/dev/media0
W=${W:-1640}
H=${H:-922}
FMT=${FMT:-SRGGB10_1X10}
COUNT=${COUNT:-2}
SECS=${SECS:-8}

sd() { media-ctl -d $MEDIA -p 2>/dev/null | grep -A2 "entity .*: $1 " |
       grep -o "/dev/v4l-subdev[0-9]*" | head -1; }

media-ctl -d $MEDIA -l '"msm_csid1":1->"msm_ispif1":0[0]'    2>/dev/null
media-ctl -d $MEDIA -l '"msm_csiphy1":1->"msm_csid1":0[1]'   || exit 1
media-ctl -d $MEDIA -l '"msm_csid1":1->"msm_ispif1":0[1]'    || exit 1
media-ctl -d $MEDIA -l '"msm_ispif1":1->"msm_vfe0_rdi0":0[1]' || exit 1

for pad in '"imx179 5-0010":0' \
           '"msm_csiphy1":0' '"msm_csiphy1":1' \
           '"msm_csid1":0' '"msm_csid1":1' \
           '"msm_ispif1":0' '"msm_ispif1":1' \
           '"msm_vfe0_rdi0":0' '"msm_vfe0_rdi0":1'; do
	media-ctl -d $MEDIA -V "$pad [fmt:${FMT}/${W}x${H}]" ||
		echo ">> FMT FAILED on $pad"
done

v4l2-ctl -d /dev/video0 -v width=$W,height=$H,pixelformat=pRAA >/dev/null || exit 1

[ -n "${SETUP_ONLY:-}" ] && { echo ">> sensor pipeline set up (${W}x${H} $FMT)"; exit 0; }

sudo dmesg -C
timeout $SECS v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=$COUNT \
	--stream-to=/tmp/sensor.raw 2>&1 | tail -3
echo ">> captured: $(stat -c %s /tmp/sensor.raw 2>/dev/null || echo 0) bytes"
sudo dmesg | grep -E "VFE counts|ispif vfe|sof timeout|csiphy|TKWROTE buf0"
