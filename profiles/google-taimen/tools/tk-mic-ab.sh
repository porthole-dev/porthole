#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: BOOTED
# env: PHONE
# exits: 0 ok · non-zero on failure
# tk-mic-ab.sh [seconds] -- runs ON THE PHONE.
#
# Records the SAME mic twice, in the two setups that behave differently, and
# snapshots the codec registers during each:
#
#   A  plain: clear ports, set the path, record.               -> observed FLAT
#   B  primed: clear ports, run a capture that FAILS because no
#      codec TX port is connected, THEN set the path and record. -> observed LIVE
#
# B is the only known-working state on this hardware, so diffing B against A is
# the shortest route to what the plain path is missing. Registers are read with
# the regmap cache bypassed, i.e. actual silicon.
set -e

# shellcheck source=../../../lib/porthole.sh
. "$(dirname "$0")/../../../tools/tk-lib.sh"
DUR=${1:-25}
REG=/sys/kernel/debug/regmap/217:250:1:0

sudo rc-service greetd stop >/dev/null 2>&1 || true
sudo pkill -9 pulseaudio >/dev/null 2>&1 || true
sleep 1
sudo sh -c "echo Y > $REG/cache_bypass" 2>/dev/null || true

clr() {
	for i in 0 1 2 3 4 5 6 7 8; do
		amixer -c0 -q cset name="AIF1_CAP Mixer SLIM TX$i" 0 >/dev/null 2>&1 || true
	done
}
path() {
	amixer -c0 -q cset name='MultiMedia2 Mixer SLIMBUS_0_TX' 1 >/dev/null 2>&1
	amixer -c0 -q cset name='AIF1_CAP Mixer SLIM TX7' 1 >/dev/null 2>&1
	amixer -c0 -q cset name='CDC_IF TX7 MUX' DEC7 >/dev/null 2>&1
	amixer -c0 -q cset name='ADC MUX7' DMIC >/dev/null 2>&1
	amixer -c0 -q cset name='DMIC MUX7' DMIC0 >/dev/null 2>&1
}
run() {
	arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d "$DUR" "/tmp/ab-$1.wav" >/dev/null 2>&1 &
	p=$!
	sleep 8
	# shellcheck disable=SC2024  # sudo is for the READ; the redirect target is /tmp and needs none
	sudo cat $REG/registers > "/tmp/ab-$1.regs"
	wait $p
	echo "  $1 done"
}

echo "A: plain"
clr; path; run a

echo "B: primed by a failed capture"
clr
amixer -c0 -q cset name='MultiMedia2 Mixer SLIMBUS_0_TX' 1 >/dev/null 2>&1
arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d 2 /tmp/ab-fail.wav >/dev/null 2>&1 || true
path; run b

echo ALLDONE
