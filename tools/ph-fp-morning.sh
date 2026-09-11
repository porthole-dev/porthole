#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: BOOTED
# env: HOST, PHONE
# exits: 0 verdict printed (payload, clean 0x105, or other status) · 1 build/push/load failed
#
# One-command fingerprint capture test: run this, touch the sensor
# repeatedly while it says to, read the single verdict line at the end.
# No log-reading required.
#
# Loads bringup/qseecom-app-load/qseecom_app_load.c's real taimen-native
# sequence (round 38-39 of the 2026-09-11 fingerprint bring-up): INIT
# {0x0a,0} once up front -- decompilation plus this round's own control
# showed EVERY capture all night that skipped INIT read -206 (an HAL
# init/state error, not "no image"), while capture preceded by INIT
# read 0x105 instead, reproducibly, 8/8 with nobody touching the
# sensor -- then loops PRE-WAIT {0x0a,1}, ARM {0x0a,3}, a wait on the
# fpc1020 kernel driver's own sysfs "irq" latch (a raw GPIO poll was
# shown to miss real edges, see that day's report), POST-WAIT
# {0x0a,2}, CAPTURE {0x0a,4}. CAPTURE is retried while its status reads
# 0x107 OR 0x108 -- decompiled from the real HAL's own capture loop
# (do_authenticate/do_enroll: "if (1 < (unsigned)(status-0x107)) break"
# -- both codes retry with an ACQUIRED_INSUFFICIENT notify, only this
# script skips the notify since nothing here implements the HIDL
# interface) -- and anything else is terminal.
#
# BASELINE, load-bearing: with INIT sent and nobody touching the
# sensor, CAPTURE reads status 0x105 every time (8/8 in this round).
# Decompiled fact (do_authenticate/do_enroll, same functions as above):
# the HAL's own loop treats 0x105 as "no image yet" -- it notifies
# ACQUIRED_TOO_FAST(5) and immediately calls capture again, it is not
# an error path and not treated as a result. This script reads a
# repeat of 0x105 as the clean no-finger baseline and anything else as
# a possible real result. This baseline replaces the OLD one (-206,
# from before INIT was known to be required); a run of this script
# from before round 38 that never sent INIT is not comparable.
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

# Round 40: the trustlet may already be resident from an earlier run
# this boot. app_load() then refuses with -EINVAL and TrustZone hands
# back no id on that path -- confirmed with the qcom_scm dynamic-debug
# trace, not guessed -- and app_get_id() cannot look this app up by
# name on this platform either, loaded or not. The only place a usable
# id can come from is a PRIOR successful "app_load(...) ret=0
# app_id=N" line still sitting in this boot's dmesg ring buffer.
# Recover it BEFORE clearing dmesg for the real run.
PRIOR_ID=$(ssh "$PHONE" "sudo -n dmesg" | grep -o 'app_load("fpctzappfingerprint") ret=0 app_id=[0-9]*' | tail -1 | grep -o '[0-9]*$' || true)
ssh "$PHONE" "sudo -n dmesg -c >/dev/null"

echo ""
echo "=================================================================="
echo " TOUCH THE FINGERPRINT SENSOR (rear of the phone) REPEATEDLY NOW."
echo " The window is about 90 seconds. Press, lift, repeat -- several"
echo " short touches spread across the window are better than one long"
echo " press. This will return on its own; no further input needed."
echo "=================================================================="
echo ""

FRESH_LOAD_TESTED=1
# insmod always returns nonzero by design (the probe never stays
# resident); that is expected, not a failure.
ssh "$PHONE" "sudo -n insmod $REMOTE_KO" || true
LOG=$(ssh "$PHONE" "sudo -n dmesg")

if echo "$LOG" | grep -q "app_load refused (-EINVAL)"; then
	FRESH_LOAD_TESTED=0
	if [ -n "$PRIOR_ID" ]; then
		echo ">> Trustlet already resident this boot (fresh load path not exercised this"
		echo "   run) -- recovered app_id=$PRIOR_ID from an earlier load line in dmesg,"
		echo "   retrying with it..."
		ssh "$PHONE" "sudo -n dmesg -c >/dev/null"
		ssh "$PHONE" "sudo -n insmod $REMOTE_KO app_id=$PRIOR_ID" || true
		LOG=$(ssh "$PHONE" "sudo -n dmesg")
	else
		echo "VERDICT: trustlet already resident this boot, and no prior successful"
		echo "'app_load(...) ret=0 app_id=N' line survives in dmesg to recover its id from"
		echo "(buffer likely rotated past it). This platform has no way to query an"
		echo "already-loaded app's id by name -- reboot the device, then rerun this script."
		ssh "$PHONE" "sudo -n rm -f $REMOTE_KO" || true
		exit 1
	fi
fi

AFTER=$(ssh "$PHONE" "grep fingerprint /proc/interrupts" || echo "n/a")
ssh "$PHONE" "sudo -n rm -f $REMOTE_KO" || true

SUMMARY=$(echo "$LOG" | grep "taimen loop: window ended" || echo "no summary line found")
INIT_LINE=$(echo "$LOG" | grep "taimen loop: INIT status=" || echo "no INIT line found")
NONRETRY=$(echo "$LOG" | grep "CAPTURE terminal status=" | grep -v "status=261 " || true)

echo ""
echo "------------------------------------------------------------------"
echo "/proc/interrupts before: $BEFORE"
echo "/proc/interrupts after:  $AFTER"
echo "$INIT_LINE"
echo "$SUMMARY"
if [ "$FRESH_LOAD_TESTED" = 0 ]; then
	echo "NOTE: the trustlet was already resident this run -- the genuine fresh-boot"
	echo "load path (no prior app_id known) was NOT exercised. If this is the first"
	echo "time this script runs after a reboot and it still hit this path, that is"
	echo "new information and should be reported, not assumed equivalent to tonight's runs."
fi
echo "------------------------------------------------------------------"

if [ -n "$NONRETRY" ]; then
	echo "VERDICT: a CAPTURE returned something other than the 0x105 baseline -- possible real result."
	echo "$NONRETRY"
	echo ">> Full response dump (grep the log for 'CAPTURE {0x0a,4}' near the line above):"
	echo "$LOG" | grep -A70 "CAPTURE terminal status=" | grep -v "status=261 " | head -80
elif echo "$LOG" | grep -q "CAPTURE terminal status=261 "; then
	echo "VERDICT: every CAPTURE returned 0x105 (no image, clean baseline) -- clean negative, per this run."
else
	echo "VERDICT: no CAPTURE was ever attempted (arm/wait never succeeded) -- check the summary line above."
fi
