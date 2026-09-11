#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: BOOTED
# env: HOST, PHONE
# exits: 0 verdict printed (payload, clean -206, or other status) · 1 build/push/load failed
#
# One-command fingerprint capture test: run this, touch the sensor
# repeatedly while it says to, read the single verdict line at the end.
# No log-reading required.
#
# Loads bringup/qseecom-app-load/qseecom_app_load.c's real taimen-native
# arm/wait/capture loop (round 36-37 of the 2026-09-11 fingerprint
# bring-up: group 0x0a, cmd 3 = arm-and-wait, cmd 4 = capture, waited on
# via the fpc1020 kernel driver's own sysfs "irq" latch, not a raw GPIO
# poll -- a raw poll was shown to miss real edges, see that day's
# report). The loop runs for ~90s, retrying CAPTURE {0x0a,4} while its
# status reads the "not enough data yet" code 0x107 and treating
# anything else as terminal.
#
# CAVEAT, load-bearing: overnight, on an idle sensor with nobody
# touching it, {0x0a,4} returned status -206 on all ~1500 attempts,
# every time preceded by a wait that "succeeded" (the arm command
# itself produces a real, kernel-counted edge -- confirmed against
# /proc/interrupts independently, not just this module's own count).
# This script reads a repeat of -206 as "no image was produced" and a
# real payload as success. If -206 turns out to mean something other
# than "no image" (a trustlet RE pass may recover its real meaning),
# that reading is wrong and this script's verdict needs revisiting --
# it is the best evidence available the night this was written, not a
# confirmed meaning.
#
# Never touches gpio 81-84 (the SPI pads, fenced). Never disables a
# regulator. mod rung only -- no flash, no reboot.
set -euo pipefail

# shellcheck source=ph-lib.sh
. "$(dirname "$0")/ph-lib.sh"

MODDIR="$(cd "$(dirname "$0")/.." && pwd)/bringup/qseecom-app-load"
REMOTE_KO=/tmp/qseecom_app_load.ko

echo ">> Building the bring-up probe module (mod rung, no flash)..."
porthole sandbox shell --command "
set -e
cd /work
export PORTHOLE_KERNEL_TREE=linux-ws
. tools/ph-lib.sh
. tools/ph-build.sh
_ph_activate
mkdir -p /work/linux-ws/.qfp-build
cp /work/bringup/qseecom-app-load/qseecom_app_load.c /work/bringup/qseecom-app-load/Makefile /work/linux-ws/.qfp-build/
chmod -R a+rwX /work/linux-ws/.qfp-build
shopt -s expand_aliases
eval make M=/mnt/linux/.qfp-build modules
cp /work/linux-ws/.qfp-build/qseecom_app_load.ko /work/bringup/qseecom-app-load/qseecom_app_load.ko
rm -rf /work/linux-ws/.qfp-build
" || { echo "BUILD FAILED"; exit 1; }
python3 "$(dirname "$0")/ph-strip-btf.py" "$MODDIR/qseecom_app_load.ko"

echo ">> Pushing to the phone..."
scp -o ConnectTimeout=5 "$MODDIR/qseecom_app_load.ko" "$PHONE:$REMOTE_KO" \
	|| { echo "PUSH FAILED"; exit 1; }

BEFORE=$(ssh "$PHONE" "grep fingerprint /proc/interrupts" || echo "n/a")
ssh "$PHONE" "sudo -n dmesg -c >/dev/null"

echo ""
echo "=================================================================="
echo " TOUCH THE FINGERPRINT SENSOR (rear of the phone) REPEATEDLY NOW."
echo " The window is about 90 seconds. Press, lift, repeat -- several"
echo " short touches spread across the window are better than one long"
echo " press. This will return on its own; no further input needed."
echo "=================================================================="
echo ""

# insmod always returns nonzero by design (the probe never stays
# resident); that is expected, not a failure.
ssh "$PHONE" "sudo -n insmod $REMOTE_KO" || true

LOG=$(ssh "$PHONE" "sudo -n dmesg")
AFTER=$(ssh "$PHONE" "grep fingerprint /proc/interrupts" || echo "n/a")
ssh "$PHONE" "sudo -n rm -f $REMOTE_KO" || true

SUMMARY=$(echo "$LOG" | grep "taimen loop: window ended" || echo "no summary line found")
NONRETRY=$(echo "$LOG" | grep "CAPTURE terminal status=" | grep -v "status=-206" || true)

echo ""
echo "------------------------------------------------------------------"
echo "/proc/interrupts before: $BEFORE"
echo "/proc/interrupts after:  $AFTER"
echo "$SUMMARY"
echo "------------------------------------------------------------------"

if [ -n "$NONRETRY" ]; then
	echo "VERDICT: a CAPTURE returned something other than -206 -- possible real result."
	echo "$NONRETRY"
	echo ">> Full response dump (grep the log for 'CAPTURE {0x0a,4}' near the line above):"
	echo "$LOG" | grep -A70 "CAPTURE terminal status=" | grep -v "status=-206" | head -80
elif echo "$LOG" | grep -q "CAPTURE terminal status=-206"; then
	echo "VERDICT: every CAPTURE returned -206 (no image produced) -- clean negative, per this run."
else
	echo "VERDICT: no CAPTURE was ever attempted (arm/wait never succeeded) -- check the summary line above."
fi
