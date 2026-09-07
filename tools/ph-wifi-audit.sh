#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed · 2 usage
# tk-wifi-audit.sh -- end-to-end WiFi stack audit for taimen (ath10k/WCN3990).
#
# Runs ON THE DEVICE. Answers, with evidence rather than impressions:
#   1. Is the calibration/board data actually being served? (empty firmware
#      version + locally-administered MAC == rmtfs is not serving, and the
#      regulatory domain that follows will be wrong.)
#   2. What regulatory domain is in force, where did it come from, and is the
#      PHY self-managed (i.e. does it ignore `iw reg set`)?
#   3. Per channel: is it enabled, passive-only (no IR), or radar/DFS -- and how
#      does that compare with what the target country actually permits?
#   4. Per channel, empirically: does a scan on that exact frequency ever return
#      a BSS? A channel can be listed as enabled and still be dead.
#   5. Optionally: full association -> DHCP -> DNS -> throughput on a real AP.
#
# Usage:
#   tk-wifi-audit.sh [COUNTRY] [SSID] [PSK]
#     COUNTRY  two-letter code to audit against (default IT). Supported
#              profiles: IT/EU (ETSI) and US (FCC).
#     SSID PSK optional; if given, an association test is run at the end.
#
# Exit status is 0 if every check passed, 1 otherwise, so it can gate a build.
set -u

COUNTRY=${1:-IT}
SSID=${2:-}
PSK=${3:-}
IFACE=${IFACE:-wlan0}
PHY=${PHY:-phy0}
FAIL=0

# ---------------------------------------------------------------- helpers ---
c_ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$*"; }
c_bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=1; }
c_warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; }
c_info() { printf '        %s\n' "$*"; }
hdr()    { printf '\n\033[1m== %s\033[0m\n' "$*"; }

need() {
	command -v "$1" >/dev/null 2>&1 || {
		echo "missing required tool: $1 (apk add $2)"; exit 2; }
}
need iw iw
need nmcli networkmanager

[ "$(id -u)" = 0 ] && SUDO="" || SUDO="sudo"

# Channels each regime actually permits. Kept deliberately small and explicit;
# this is a bring-up audit, not a certification tool.
#   ETSI (IT): 2.4 = 1-13; 5 GHz = 36-64 and 100-140. 149-165 is NOT allowed.
#   FCC (US):  2.4 = 1-11; 5 GHz = 36-64, 100-144 and 149-165.
case "$COUNTRY" in
	IT|EU|DE|FR|ES|GB)
		EXPECT_24="1 2 3 4 5 6 7 8 9 10 11 12 13"
		EXPECT_5="36 40 44 48 52 56 60 64 100 104 108 112 116 120 124 128 132 136 140"
		FORBID_5="149 153 157 161 165"
		REGIME="ETSI" ;;
	US)
		EXPECT_24="1 2 3 4 5 6 7 8 9 10 11"
		EXPECT_5="36 40 44 48 52 56 60 64 100 104 108 112 116 120 124 128 132 136 140 144 149 153 157 161 165"
		FORBID_5=""
		REGIME="FCC" ;;
	*)
		echo "no profile for country '$COUNTRY' (supported: IT/EU-like, US)"; exit 2 ;;
esac

echo "taimen WiFi audit -- target country $COUNTRY ($REGIME), iface $IFACE"
date

# ------------------------------------------------- 1. board / calibration ---
hdr "1. Firmware, board data and MAC"

FWVER=$(dmesg 2>/dev/null | sed -n 's/.*ath10k_snoc[^:]*: firmware ver \(.*\) api .*/\1/p' | tail -1)
if [ -n "$(echo "$FWVER" | tr -d ' ')" ]; then
	c_ok "firmware version reported: $FWVER"
else
	c_bad "firmware version string is EMPTY"
	c_info "classic tell that rmtfs is not serving the modem's EFS, so the"
	c_info "board/calibration data never reaches ath10k. The regulatory domain"
	c_info "and MAC below are then defaults, not this device's real values."
fi

MAC=$(cat "/sys/class/net/$IFACE/address" 2>/dev/null)
FIRST=$(printf '%d' "0x$(echo "$MAC" | cut -d: -f1)" 2>/dev/null || echo 0)
if [ $((FIRST & 2)) -ne 0 ]; then
	c_bad "MAC $MAC is locally administered (random) -- no valid MAC from board data"
	c_info "it will change on every boot, breaking DHCP reservations and any"
	c_info "MAC-based access control on the AP"
else
	c_ok "MAC $MAC looks like a real assigned address"
fi

if dmesg 2>/dev/null | grep -q "invalid MAC address; choosing random"; then
	c_info "dmesg: ath10k explicitly chose a random MAC"
fi
pgrep -x rmtfs >/dev/null 2>&1 && c_ok "rmtfs is running" || c_bad "rmtfs is NOT running"

# --------------------------------------------------------- 2. regulatory ---
hdr "2. Regulatory domain"

GLOBAL_C=$($SUDO iw reg get 2>/dev/null | sed -n 's/^country \([A-Z0-9][A-Z0-9]\).*/\1/p' | head -1)
PHY_C=$($SUDO iw reg get 2>/dev/null | awk '/^phy#/{f=1} f&&/^country/{print substr($2,1,2); exit}')
c_info "global regdomain : ${GLOBAL_C:-unknown}"
c_info "phy regdomain    : ${PHY_C:-<none, follows global>}"

if $SUDO iw phy "$PHY" info 2>/dev/null | grep -q "self-managed"; then
	c_warn "PHY is self-managed: it sets its own regdomain and ignores 'iw reg set'"
fi

case "${PHY_C:-}" in
	"$COUNTRY") c_ok "PHY regdomain matches target country $COUNTRY" ;;
	99|00|"")
		c_bad "PHY regdomain is '${PHY_C:-unset}' (world/custom), not $COUNTRY"
		c_info "a world domain marks 5 GHz no-IR (passive only) and disables DFS,"
		c_info "which is usually why 5 GHz APs are invisible" ;;
	*)  c_bad "PHY regdomain is '$PHY_C', expected $COUNTRY" ;;
esac

dmesg 2>/dev/null | grep -E "^\[.*\] ath: (Country|EEPROM regdomain|Regpair)" | tail -3 |
	while read -r l; do c_info "dmesg: ${l#*] }"; done

# Does the driver even accept a country change?
if [ -n "$PHY_C" ] && [ "$PHY_C" != "$COUNTRY" ]; then
	$SUDO iw reg set "$COUNTRY" 2>/dev/null
	sleep 2
	NEW=$($SUDO iw reg get 2>/dev/null | awk '/^phy#/{f=1} f&&/^country/{print substr($2,1,2); exit}')
	if [ "$NEW" = "$COUNTRY" ]; then
		c_ok "'iw reg set $COUNTRY' was accepted by the PHY"
	else
		c_bad "'iw reg set $COUNTRY' was IGNORED (still '$NEW')"
		c_info "the domain is coming from board data/OTP, so fix the board data"
		c_info "(see check 1) rather than trying to set it from userspace"
	fi
fi

# ---------------------------------------------------- 3. channel matrix ----
hdr "3. Channel matrix vs $REGIME"

CHFILE=/tmp/tk-wifi-chan.$$
$SUDO iw phy "$PHY" info 2>/dev/null |
	awk '/^\t\t\t\* [0-9]+(\.[0-9]+)? MHz/ {
		# line looks like:  * 2412.0 MHz [1] (20.0 dBm) (no IR)
		# strip with two subs, not a bracket expression: busybox awk does
		# not reliably handle [\[\]]
		freq=$2; sub(/\..*/,"",freq)
		ch=$4; sub(/^\[/,"",ch); sub(/\]$/,"",ch)
		flags=""
		if (index($0,"disabled")) flags=flags "disabled,"
		if (index($0,"no IR"))    flags=flags "no-IR,"
		if (index($0,"radar"))    flags=flags "radar,"
		sub(/,$/,"",flags)
		if (flags=="") flags="enabled"
		print ch, freq, flags
	}' > "$CHFILE"

printf '  %-5s %-7s %-22s %s\n' CH FREQ FLAGS VERDICT
while read -r ch freq flags; do
	verdict=""
	case " $EXPECT_24 $EXPECT_5 " in
		*" $ch "*)
			case "$flags" in
				*disabled*) verdict="SHOULD BE USABLE in $COUNTRY"; FAIL=1 ;;
				*no-IR*)    verdict="passive only (no TX)" ;;
				*)          verdict="ok" ;;
			esac ;;
		*)
			case " $FORBID_5 " in
				*" $ch "*)
					case "$flags" in
						*disabled*) verdict="correctly disabled" ;;
						*) verdict="ENABLED but not permitted in $COUNTRY"; FAIL=1 ;;
					esac ;;
				*) verdict="-" ;;
			esac ;;
	esac
	printf '  %-5s %-7s %-22s %s\n' "$ch" "$freq" "$flags" "$verdict"
done < "$CHFILE"

MISSING=""
for ch in $EXPECT_24 $EXPECT_5; do
	grep -q "^$ch " "$CHFILE" || MISSING="$MISSING $ch"
done
[ -n "$MISSING" ] && { c_bad "channels absent from the PHY entirely:$MISSING"; }

DIS=$(awk '$3 ~ /disabled/ {printf "%s ", $1}' "$CHFILE")
[ -n "$DIS" ] && c_info "disabled channels: $DIS"

# ------------------------------------------------ 4. per-channel scanning ---
hdr "4. Per-channel scan (empirical -- does anything answer?)"

# NetworkManager scans on its own schedule and a concurrent `iw scan` just gets
# -EBUSY, which is indistinguishable from "channel is dead" unless it is
# reported. Pause NM's autoconnect scanning for the duration, and retry.
NM_WAS_MANAGED=$($SUDO nmcli -g GENERAL.STATE device show "$IFACE" 2>/dev/null | head -1)
$SUDO nmcli device set "$IFACE" managed no >/dev/null 2>&1
$SUDO ip link set "$IFACE" up >/dev/null 2>&1
sleep 2

TOTAL24=0; TOTAL5=0; SCANNED=0; BUSY=0
printf '  %-5s %-7s %-11s %s\n' CH FREQ "BSSes seen" NOTE
while read -r ch freq flags; do
	case "$flags" in *disabled*) continue ;; esac
	SCANNED=$((SCANNED + 1))
	note=""; out=""
	# up to 3 attempts; -EBUSY is transient
	for _try in 1 2 3; do
		out=$($SUDO iw dev "$IFACE" scan freq "$freq" 2>&1)
		case "$out" in
			*"resource busy"*|*"Operation not permitted"*)
				note="busy, retrying"; sleep 3; continue ;;
		esac
		note=""; break
	done
	case "$out" in
		*"resource busy"*)
			n=0; note="SCAN BUSY -- result not trustworthy"; BUSY=$((BUSY + 1)) ;;
		*)
			# `iw scan freq X` scans X but then dumps the WHOLE cached BSS
			# table, so counting "^BSS " credits every cached 2.4 GHz AP to
			# whatever channel we just scanned. Count only BSSes whose
			# reported freq is the one under test.
			n=$(echo "$out" | awk -v f="$freq" '
				/^[ \t]*freq:/ { g=$2; sub(/\..*/,"",g); if (g==f) c++ }
				END { print c+0 }') ;;
	esac
	[ -z "$n" ] && n=0
	if [ "$freq" -lt 3000 ]; then TOTAL24=$((TOTAL24 + n)); else TOTAL5=$((TOTAL5 + n)); fi
	printf '  %-5s %-7s %-11s %s\n' "$ch" "$freq" "$n" "$note"
	sleep 1
done < "$CHFILE"

case "$NM_WAS_MANAGED" in
	*unmanaged*) : ;;
	*) $SUDO nmcli device set "$IFACE" managed yes >/dev/null 2>&1 ;;
esac
[ "$BUSY" -gt 0 ] && c_warn "$BUSY channel(s) could not be scanned cleanly (device busy)"

c_info "scanned $SCANNED enabled channels"
[ "$TOTAL24" -gt 0 ] && c_ok "2.4 GHz: $TOTAL24 BSSes seen" || c_bad "2.4 GHz: nothing found"
if [ "$TOTAL5" -gt 0 ]; then
	c_ok "5 GHz: $TOTAL5 BSSes seen"
else
	c_bad "5 GHz: NOTHING found on any enabled channel"
	c_info "if 5 GHz channels are all 'no IR' the radio may only listen passively;"
	c_info "combined with a world regdomain this makes 5 GHz APs invisible"
fi

# ------------------------------------------------------ 5. association -----
if [ -n "$SSID" ]; then
	hdr "5. Association / DHCP / DNS / throughput on '$SSID'"
	$SUDO nmcli device wifi connect "$SSID" ${PSK:+password "$PSK"} ifname "$IFACE" >/dev/null 2>&1
	sleep 8
	STATE=$($SUDO nmcli -g GENERAL.STATE device show "$IFACE" 2>/dev/null | head -1)
	case "$STATE" in
		*connected*) c_ok "associated ($STATE)" ;;
		*) c_bad "did not associate (state: ${STATE:-unknown})" ;;
	esac

	IP=$(ip -4 addr show "$IFACE" 2>/dev/null | sed -n 's/.*inet \([0-9.]*\).*/\1/p' | head -1)
	[ -n "$IP" ] && c_ok "DHCP address $IP" || c_bad "no IPv4 address"

	BR=$($SUDO iw dev "$IFACE" link 2>/dev/null | sed -n 's/.*rx bitrate: \(.*\)/\1/p')
	FR=$($SUDO iw dev "$IFACE" link 2>/dev/null | sed -n 's/.*freq: \([0-9]*\).*/\1/p')
	[ -n "$FR" ] && c_info "associated on $FR MHz, rx bitrate ${BR:-unknown}"

	if ping -I "$IFACE" -c3 -W3 1.1.1.1 >/dev/null 2>&1; then
		c_ok "ICMP to 1.1.1.1 works"
	else
		c_bad "no ICMP connectivity"
	fi
	if nslookup example.com >/dev/null 2>&1; then c_ok "DNS resolves"; else c_bad "DNS failed"; fi
fi

rm -f "$CHFILE"
hdr "RESULT"
[ "$FAIL" = 0 ] && { echo "  ALL CHECKS PASSED"; exit 0; }
echo "  ONE OR MORE CHECKS FAILED (see FAIL lines above)"
exit 1
