#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: FASTBOOT
# env: HOST, PORTHOLE_WORKDIR
# exits: 0 ok · 1 failed
# lib-exempt: watches raw USB/fastboot transitions around `fastboot boot`, including the confirmed-disconnect gate; the lib's helpers assume a settled device
# Boot an image and time the USB transitions, distinguishing a real reboot from noise.
#
# The trap this exists to avoid: right after `fastboot boot` returns, the host has NOT yet
# processed the USB disconnect, so `fastboot devices` still lists the phone for a second or
# two. Polling naively from t=0 therefore reports a "reboot" at t+2s for ANY image, including
# one that just hangs. See taimen-blind-debug-failure -- signatures that look like a result
# but are really bootloader/USB behaviour have burned this project before.
#
# So: wait for a CONFIRMED disconnect first, and only then start looking for a reappearance.
#
# Usage: boot-probe.sh IMAGE "label for the log"
set -u
IMG="${1:?usage: boot-probe.sh IMAGE LABEL}"
LABEL="${2:-unlabelled}"
LOGDIR=$PORTHOLE_WORKDIR/logs
mkdir -p "$LOGDIR"
LOG="$LOGDIR/probe-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG") 2>&1

present() { [ -n "$(timeout 3 fastboot devices 2>/dev/null)" ]; }

# A dark screen on taimen means nothing -- the panel is never lit by mainline. The phone can
# be fully booted with USB networking up while looking identical to a hang. Watching only for
# a fastboot reappearance once made this script report "HUNG" for a boot that had actually
# succeeded, so check for the gadget too.
booted() {
    [ -n "$(ip -br addr show to 172.16.42.0/24 2>/dev/null)" ] && return 0
    ping -c1 -W1 $HOST >/dev/null 2>&1 && return 0
    [ -e /dev/ttyACM0 ] && return 0
    return 1
}

echo "### $LABEL"
echo "### image: $IMG"
echo "### cmdline:"
$PORTHOLE_WORKDIR/tools/bootimg-cmdline.py show "$IMG" | sed 's/^/###   /'
echo

if ! present; then
    echo "ABORT: phone is not in the bootloader. Hold Power+VolDown ~15s, then VolDown to"
    echo "       reach the bootloader menu, and re-run. (Note: that long-press is a COLD"
    echo "       reset and wipes the ramoops region -- no pstore to recover afterwards.)"
    exit 1
fi

echo "[$(date +%H:%M:%S)] pre-boot:"
for v in current-slot slot-retry-count:a slot-retry-count:b; do
    echo "  $(timeout 5 fastboot getvar $v 2>&1 | head -1)"
done
echo

T0=$(date +%s)
echo "[$(date +%H:%M:%S)] fastboot boot"
timeout 60 fastboot boot "$IMG" 2>&1 | sed 's/^/  /'

# Phase 1 -- confirm the phone actually left fastboot.
GONE=""
for _ in $(seq 1 60); do
    sleep 0.5
    if ! present; then
        # require it to stay gone, so one flaky poll does not count
        sleep 0.5
        present || { GONE=$(( $(date +%s) - T0 )); break; }
    fi
done
if [ -z "$GONE" ]; then
    echo "[$(date +%H:%M:%S)] phone NEVER left fastboot -- the bootloader refused the image"
    echo "=== RESULT: REJECTED (no handoff). Check the dtbo stub is on the ACTIVE slot."
    exit 0
fi
echo "[$(date +%H:%M:%S)] confirmed handoff at t+${GONE}s (USB disconnected, kernel has control)"

# Phase 2 -- now a reappearance means something.
BACK=""
UP=""
for _ in $(seq 1 150); do
    sleep 1
    if booted; then
        UP=$(( $(date +%s) - T0 )); break
    fi
    if present; then
        sleep 1
        present && { BACK=$(( $(date +%s) - T0 )); break; }
    fi
done

echo
if [ -n "$UP" ]; then
    echo "[$(date +%H:%M:%S)] *** GADGET UP at t+${UP}s (t+$(( UP - GONE ))s after handoff)"
    echo "=== RESULT: BOOTED. The kernel reached userspace and the initramfs ran."
    echo "    phone $HOST / host 172.16.42.2. Debug shell on port 23:"
    echo "      taimen/tools/tsh.py 'dmesg' -o taimen/logs/dmesg.log"
    echo "    Grab dmesg FAST -- buffyboard floods the ring buffer at 30 Hz when there is"
    echo "    no framebuffer and evicts the entire early log within ~45s."
    exit 0
fi
if [ -n "$BACK" ]; then
    echo "[$(date +%H:%M:%S)] *** REAPPEARED at t+${BACK}s (t+$(( BACK - GONE ))s after handoff)"
    echo "=== RESULT: the phone REBOOTED. Something reset it."
    echo "    Timing guide: ~10s after handoff == panic=10 fired (kernel was alive)."
    echo "                  ~2-3s after handoff == too fast for panic; suspect bootloader."
    echo "    A positive here MUST be confirmed with a negative control: re-run this script"
    echo "    on the UNMODIFIED boot.img, which is known to hang forever. If the control also"
    echo "    reappears, the signal is not from the kernel."
    for v in current-slot slot-retry-count:a slot-retry-count:b; do
        echo "  $(timeout 5 fastboot getvar $v 2>&1 | head -1)"
    done
else
    echo "[$(date +%H:%M:%S)] no reappearance within 150s of handoff"
    echo "=== RESULT: HUNG. No reset, so no panic fired."
    echo "    There is no watchdog in msm8998.dtsi, so this state is terminal --"
    echo "    only a cold Power+VolDown escapes it, and that wipes ramoops."
fi
echo
echo "log: $LOG"
