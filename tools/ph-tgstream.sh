#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
# ph-tgstream.sh -- capture from the CSID's own test generator.
#
# The point is that NOTHING upstream of the CSID is involved: the CSIPHY link is
# disabled, so the sensor and the PHY are entirely out of the picture. That
# makes this the only way to exercise CSID -> ISPIF -> VFE -> DMA on a device
# whose sensor does not transmit, and the only reproducer for the VFE
# write-master defect.
#
# Run ON the phone.
#
# ponytail: subdev nodes are resolved BY NAME. /dev/v4l-subdevN renumbers on
# every module reload, and hardcoding it silently tests a different entity --
# that cost two contradictory results before it was noticed.
set -u

MEDIA=/dev/media0
W=${W:-1640}
H=${H:-922}
COUNT=${COUNT:-2}
SECS=${SECS:-8}

sd() { media-ctl -d $MEDIA -p 2>/dev/null | grep -A2 "entity .*: $1 " |
       grep -o "/dev/v4l-subdev[0-9]*" | head -1; }

CSID=$(sd msm_csid1)
[ -n "$CSID" ] || { echo ">> could not resolve msm_csid1"; exit 1; }
echo ">> msm_csid1 = $CSID"

# CSIPHY link OFF -- csid_set_test_pattern() returns -EBUSY while it is enabled.
media-ctl -d $MEDIA -l '"msm_csiphy1":1->"msm_csid1":0[0]'   || exit 1
media-ctl -d $MEDIA -l '"msm_csid1":1->"msm_ispif1":0[1]'    || exit 1
media-ctl -d $MEDIA -l '"msm_ispif1":1->"msm_vfe0_rdi0":0[1]' || exit 1

# TG=0 sets up the identical pipeline with the test generator OFF. Since the
# CSIPHY link is already cut, that is a pipeline which streams with NO data
# reaching the VFE -- the control for any "did the VFE write?" measurement.
TG=${TG:-1}
v4l2-ctl -d "$CSID" --set-ctrl test_pattern=$TG 2>/dev/null
echo ">> $(v4l2-ctl -d "$CSID" --get-ctrl test_pattern 2>/dev/null)"

for pad in '"msm_csid1":0' '"msm_csid1":1' \
           '"msm_ispif1":0' '"msm_ispif1":1' \
           '"msm_vfe0_rdi0":0' '"msm_vfe0_rdi0":1'; do
	media-ctl -d $MEDIA -V "$pad [fmt:SRGGB10_1X10/${W}x${H}]" ||
		echo ">> FMT FAILED on $pad"
done

v4l2-ctl -d /dev/video0 -v width=$W,height=$H,pixelformat=pRAA >/dev/null || exit 1

sudo dmesg -C
# SETUP_ONLY leaves the pipeline configured for another tool to stream it.
[ -n "${SETUP_ONLY:-}" ] && { echo ">> pipeline set up (TG=$TG, ${W}x${H})"; exit 0; }
timeout $SECS v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=$COUNT \
	--stream-to=/tmp/tg.raw 2>&1 | tail -3
echo ">> captured: $(stat -c %s /tmp/tg.raw 2>/dev/null || echo 0) bytes"
sudo dmesg | grep -E "VFE irqs|ispif vfe|sof timeout|reg update|halt"
