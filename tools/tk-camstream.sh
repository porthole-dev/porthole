#!/bin/sh
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
# tk-camstream.sh -- one capture attempt on the IMX179 front camera, with the
# only two instruments that survive it: the camss IRQ counters and dmesg.
#
# Run ON the phone. Everything here is HANDOFF-2026-08-03 §3c; the value added
# is the before/after IRQ diff, which answers "did the CSIPHY see anything at
# all" without poking registers on a block whose clocks may be gone.
#
#   csiphy1 stays 0  -> the PHY never even latched a lane event
#   csiphy1 climbs   -> lines are toggling; the fault is CSID/VFE-side
#
# ponytail: hardcoded to csiphy1/csid1/vfe0_rdi0 + video0, the only path the
# IMX179 is wired to. Parameterise when the rear IMX362 lands.
set -u

MEDIA=/dev/media0
VIDEO=${VIDEO:-/dev/video0}
FMT=${FMT:-SRGGB10_1X10}
W=${W:-1640}
H=${H:-922}
COUNT=${COUNT:-3}

irqs() { grep -E "camss_msm_(csiphy1|csid1|vfe0)" /proc/interrupts |
         awk '{s=0; for(i=2;i<=NF-4;i++) s+=$i; print $NF, s}'; }

echo "=== links"
media-ctl -d $MEDIA -l '"msm_csiphy1":1->"msm_csid1":0[1]' || exit 1
media-ctl -d $MEDIA -l '"msm_csid1":1->"msm_ispif1":0[1]' || exit 1
media-ctl -d $MEDIA -l '"msm_ispif1":1->"msm_vfe0_rdi0":0[1]' || exit 1

echo "=== formats"
for pad in '"imx179 5-0010":0' '"msm_csiphy1":0' '"msm_csiphy1":1' \
           '"msm_csid1":0' '"msm_csid1":1' \
           '"msm_ispif1":0' '"msm_ispif1":1' \
           '"msm_vfe0_rdi0":0' '"msm_vfe0_rdi0":1'; do
	media-ctl -d $MEDIA -V "$pad [fmt:$FMT/${W}x$H]" || exit 1
done

v4l2-ctl -d $VIDEO -v width=$W,height=$H,pixelformat=pRAA >/dev/null || exit 1

sudo dmesg -C
BEFORE=$(irqs)

echo "=== stream"
timeout 25 v4l2-ctl -d $VIDEO --stream-mmap --stream-count=$COUNT \
	--stream-to=/tmp/frame.raw 2>&1 | tail -5
echo "(v4l2-ctl exit $?)"

AFTER=$(irqs)

echo "=== irq delta"
echo "$BEFORE" | while read -r name before; do
	after=$(echo "$AFTER" | awk -v n="$name" '$1==n {print $2}')
	echo "  $name  $before -> $after  (+$((after - before)))"
done

echo "=== dmesg"
sudo dmesg | tail -40

echo "=== frame"
ls -l /tmp/frame.raw 2>/dev/null || echo "  (no frame)"
