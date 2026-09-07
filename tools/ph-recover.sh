#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: any (probes state; handles BOOTED and FASTBOOT)
# env: FASTBOOT, HOST, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST, TK_IMG
# exits: 0 ok · 1 failed · 2 usage · 3 see source · 4 see source · 5 see source · 6 see source · 7 see source
# tk-recover.sh -- one command to get taimen back after the 2026-08-01 incident,
# and after any hang that leaves it off the bus.
#
# It detects which of the four states the phone is in and does the right thing,
# because the states look confusingly alike from the host:
#
#   BOOTED     pmOS gadget 18d1:d001 enumerated AND ssh answers
#   FROZEN     ping answers but ssh does not      -> PID-1 freeze, use rescue :2323
#   FASTBOOT   18d1:4ee0                          -> reflash the known-good boot.img
#   ABSENT     nothing on USB at all              -> needs a human: long-press power
#
# NOTE lsusb mislabels the running pmOS gadget as "fastboot"; the product ID is
# what distinguishes them (d001 = our gadget, 4ee0 = real fastboot), never the
# lsusb text.
#
# Usage: tk-recover.sh [--flash]     (--flash allows reflashing if it finds fastboot)
set -u

cd "$(dirname "$0")" || exit 1
HOST=${TK_HOST:-$PORTHOLE_HOST}
IMG=${TK_IMG:-/tmp/postmarketOS-export/boot.img}
BLACKLIST=/etc/modprobe.d/tkdiag-ladder-no-ipa.conf
LADDER_LOG=/var/log/tk-ladder.log
DO_FLASH=0
[ "${1:-}" = "--flash" ] && DO_FLASH=1

say() { echo ">> $*"; }
# StrictHostKeyChecking=no + a /dev/null known-hosts file: the phone's host
# key changes on essentially every boot (tk-lib.sh header). Without these a
# changed key makes ssh fail, which this script reads as "ssh does not
# answer" -- so a perfectly healthy phone is classified FROZEN and rebooted.
ssh_d() { timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=6 \
	-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
	-o LogLevel=ERROR "$PORTHOLE_USER@$HOST" "$@" 2>/dev/null; }

detect() {
	local usb ping_ok ssh_ok
	usb=$(lsusb 2>/dev/null | grep -oE '18d1:[0-9a-f]{4}' | head -1)
	[ "$usb" = "18d1:4ee0" ] && { echo FASTBOOT; return; }
	ping -c1 -W2 "$HOST" >/dev/null 2>&1 && ping_ok=1 || ping_ok=0
	ssh_d true >/dev/null 2>&1 && ssh_ok=1 || ssh_ok=0
	if   [ $ssh_ok -eq 1 ];   then echo BOOTED
	elif [ $ping_ok -eq 1 ];  then echo FROZEN
	elif [ -n "$usb" ];       then echo "UNKNOWN_USB:$usb"
	else                           echo ABSENT
	fi
}

STATE=$(detect)
say "state: $STATE"

case "$STATE" in
ABSENT)
	cat <<-EOF

	Nothing on USB. This needs hands:
	  1. long-press power ~10 s to force off, then power on
	     (a dark screen is NOT proof of failure on this device)
	  2. still nothing -> power + volume-down for the bootloader
	  3. then re-run: $0 --flash
	EOF
	exit 2
	;;
FROZEN)
	say "ping answers but ssh does not -- PID-1 freeze signature."
	say "trying the PAM-free rescue channel on :2323"
	printf 'uptime\ncat /proc/1/stat | cut -d" " -f3\nexit\n' | timeout 10 nc "$HOST" 2323 || \
		say "rescue channel not answering (it ships from pkgrel 28; older images lack it)"
	say "if the rescue channel is dead too, long-press power and re-run this script"
	exit 3
	;;
FASTBOOT)
	if [ $DO_FLASH -ne 1 ]; then
		say "in fastboot. Re-run with --flash to reflash $IMG"
		exit 4
	fi
	[ -s "$IMG" ] || { say "FATAL: $IMG missing/empty"; exit 5; }
	say "reflashing known-good boot.img via tk-flash-boot.sh"
	./tk-flash-boot.sh "$IMG" 240 || { say "flash failed"; exit 6; }
	STATE=$(detect)
	say "state after flash: $STATE"
	[ "$STATE" = BOOTED ] || exit 7
	;;
esac

# ---- from here the phone is up ----
say "uptime:     $(ssh_d 'uptime | tr -s " "')"
say "bootreason: $(ssh_d "tr ' ' '\\n' < /proc/cmdline | grep bootreason")"

# TIME-CRITICAL: capture this BEFORE the pack charges up, or the evidence is gone.
# qcom_fg has NO capacity learning (CHARGE_FULL is a TODO returning the DT design
# capacity), so SOC is computed against a 2017 pack's ORIGINAL capacity and can read
# high on a degraded cell. The scale also cannot express 0 or 100 -- it maps raw
# 1..255 to 1..99 -- so there is no low-battery warning to miss.
# Voltage is the honest number:
#   ~4.0-4.1 V at a genuine 90%.  <=3.5 V while claiming 90% => the gauge is lying
#   and the phone really did run out.  >=3.9 V => power was NOT the cause.
say "--- BATTERY (capture now, before it charges) ---"
say "  capacity:    $(ssh_d 'cat /sys/class/power_supply/bms/capacity 2>/dev/null') %"
say "  voltage_now: $(ssh_d 'cat /sys/class/power_supply/bms/voltage_now 2>/dev/null') uV"
say "  bms status:  $(ssh_d 'cat /sys/class/power_supply/bms/status 2>/dev/null')"
say "  current_now: $(ssh_d 'cat /sys/class/power_supply/bms/current_now 2>/dev/null') (POSITIVE = DISCHARGING on this device)"
say "  charger:     $(ssh_d 'cat /sys/class/power_supply/pmi8998_charger/status /sys/class/power_supply/pmi8998_charger/usb_type 2>/dev/null | tr "\\n" " "')"

# The whole point of the experiment that crashed it. Synced per line, so this
# should have survived even a hard hang.
say "--- $LADDER_LOG (names the rung that hung) ---"
ssh_d "sudo cat $LADDER_LOG 2>/dev/null" || say "(no ladder log)"
say "--- end ladder log ---"

# Leftover experiment blacklists are the single most expensive class of bug in
# this project's history (see tk-no-modem.conf, 2026-08-01). Always clear them.
if ssh_d "test -f $BLACKLIST"; then
	say "removing leftover $BLACKLIST (it keeps the modem/GPS/SMS dead)"
	ssh_d "sudo rm -f $BLACKLIST" && say "removed -- REBOOT REQUIRED for the modem to return"
fi
say "modprobe.d leftovers anywhere:"
ssh_d 'for d in /etc/modprobe.d /lib/modprobe.d /usr/lib/modprobe.d /run/modprobe.d; do
        [ -d "$d" ] && grep -rln "TKDIAG\|blacklist ipa\|blacklist qcom_q6v5" "$d" 2>/dev/null; done' \
	|| true
say "slot health:"; ssh_d 'sudo qbootctl' | sed 's/^/     /'
say "failed units: $(ssh_d 'systemctl --failed --no-legend --no-pager | wc -l')"
say "done."
