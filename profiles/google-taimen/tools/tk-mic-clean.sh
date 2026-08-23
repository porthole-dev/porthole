#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: BOOTED
# env: PHONE
# exits: 0 ok · 1 failed
# tk-mic-clean.sh [mic] [seconds] [stray] -- runs ON THE PHONE.
#
# mic:    mic1 (DMIC0->DEC7->TX7) | mic2 (DMIC2->DEC5->TX5) | mic3 (DMIC4->DEC6->TX6)
#         -- the three physical taimen mics, per the vendor's mictest-taimen-* paths.
# stray:  optional extra "AIF1_CAP Mixer SLIM TXn" to also enable, to reproduce
#         the failure on purpose.
#
# The point of this script is the FIRST loop. Every AIF1/2/3_CAP SLIM TX port is
# explicitly cleared before one is enabled. A single stray enabled TX port puts a
# second channel into the codec's SLIM stream while the DSP is configured for
# one, and the DSP then reads a slot the mic is not on -- which looks exactly
# like a dead microphone (one repeated value, immune to mixer changes).
#
# Mixer is set once, then left alone; the analyser discards the head. See
# docs/HANDOFF-audio.md section 4 for why anything less is not evidence.
set -e

# shellcheck source=../../../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tools/tk-lib.sh"
MIC=${1:-mic1}
DUR=${2:-25}
STRAY=$3

case "$MIC" in
mic1) TX=7; DEC=DEC7; DMIC=DMIC0 ;;
mic2) TX=5; DEC=DEC5; DMIC=DMIC2 ;;
mic3) TX=6; DEC=DEC6; DMIC=DMIC4 ;;
*) echo "unknown mic $MIC"; exit 1 ;;
esac

sudo rc-service greetd stop >/dev/null 2>&1 || true
sudo pkill -9 pulseaudio >/dev/null 2>&1 || true
sleep 1

set_ctl() {
	amixer -c0 -q cset name="$1" "$2" >/dev/null 2>&1 ||
		echo "WARN: cset '$1' = '$2' failed"
}

# THE IMPORTANT PART: no stray capture ports.
for a in AIF1 AIF2 AIF3; do
	for i in 0 1 2 3 4 5 6 7 8 9 10 11 12 13; do
		amixer -c0 -q cset name="${a}_CAP Mixer SLIM TX$i" 0 >/dev/null 2>&1 || true
	done
done

set_ctl 'MultiMedia2 Mixer SLIMBUS_0_TX' 1
set_ctl "AIF1_CAP Mixer SLIM TX$TX" 1
set_ctl "CDC_IF TX$TX MUX" "$DEC"
set_ctl "ADC MUX$TX" DMIC
set_ctl "DMIC MUX$TX" "$DMIC"
[ -n "$STRAY" ] && set_ctl "AIF1_CAP Mixer SLIM TX$STRAY" 1

echo "$MIC: TX$TX/$DEC/$DMIC${STRAY:+ + stray TX$STRAY}; settling"
sleep 3
arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d "$DUR" "/tmp/clean-$MIC.wav" >/dev/null 2>&1
echo "done /tmp/clean-$MIC.wav"
