#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: on-device
# env: -
# exits: 0 ok · 1 no qseecom · 2 lookup path broken
# ph-fp-ta.sh -- what does the TrustZone say about the fingerprint trustlet?
#
# Replaces ph-fp-probe.sh, which argued from the struck verdict, named the
# part an FPC1020, and toggled GPIOs to prove electrical liveness -- a
# question the transport answers properly once it exists.
#
# Run ON THE DEVICE as root. Reads only.
set -e

echo "== is QSEECom built in =="
zcat /proc/config.gz | grep -E '^CONFIG_QCOM_QSEECOM' || {
	echo "CONFIG_QCOM_QSEECOM is not =y -- every reading below is void"
	exit 1
}

echo
echo "== what the kernel said at boot =="
# NOT dmesg: an ath10k firmware-restart WARN storm flushes the ring and the
# qseecom line goes missing from it. This is a measured false negative.
journalctl -b 0 -k | grep -i qseecom || echo "(nothing -- QSEE does not answer)"

echo
echo "== is the auxiliary device there =="
ls -d /sys/bus/auxiliary/devices/*qseecom* 2>/dev/null \
	|| echo "(no qseecom aux device -- machine not allowlisted?)"

echo
echo "== app lookup =="
if [ -e /sys/kernel/debug/qseecom_ta/lookup ]; then
	echo fpctzappfingerprint > /sys/kernel/debug/qseecom_ta/lookup 2>&1 || true
	cat /sys/kernel/debug/qseecom_ta/lookup
else
	echo "(no lookup interface yet -- expected until the transport lands)"
fi
