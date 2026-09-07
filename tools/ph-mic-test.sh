#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: PHONE
# exits: 0 ok · non-zero on failure
# ph-mic-test.sh [DMIC0..DMIC5] [seconds] [rate] -- runs ON THE PHONE.
#
# Records one clean capture on the requested DMIC following the vendor's
# taimen path (mictest-taimen-mic1 = dmic1 = DMICn -> DEC7 -> SLIM TX7) and
# snapshots the full WCD934X register map before and during the capture.
#
# Rules from docs/HANDOFF-audio.md that this script exists to enforce:
#   - the mixer is set ONCE, before the stream starts, and never touched again
#   - the first seconds are discarded by the analyser, not by this script
#   - "DECn Volume" is never written (kcontrol max clamps it to a huge gain cut)
set -e

# shellcheck source=../lib/porthole.sh
. "$(dirname "$0")/ph-lib.sh"
DMIC=${1:-DMIC0}
DUR=${2:-25}
# Rate must match snd_soc_msm8998's be_rate, or the DSP resamples on top of
# whatever the codec is doing and the 2:1 structure is no longer readable.
RATE=${3:-48000}
OUT=/tmp/cap-$DMIC-$RATE.wav
REG=/sys/kernel/debug/regmap/217:250:1:0/registers

sudo rc-service greetd stop >/dev/null 2>&1 || true
sudo pkill -9 pulseaudio >/dev/null 2>&1 || true
sleep 1

set_ctl() {
	amixer -c0 -q cset name="$1" "$2" >/dev/null 2>&1 ||
		echo "WARN: cset '$1' = '$2' failed"
}

set_ctl 'MultiMedia2 Mixer SLIMBUS_0_TX' 1
set_ctl 'AIF1_CAP Mixer SLIM TX7' 1
set_ctl 'CDC_IF TX7 MUX' DEC7
set_ctl 'ADC MUX7' DMIC
set_ctl "DMIC MUX7" "$DMIC"

# shellcheck disable=SC2024  # sudo is for the READ; the redirect target is /tmp and needs none
sudo cat $REG > /tmp/reg-idle.txt

echo "MIXER SET ($DMIC); starting ${DUR}s capture at ${RATE} Hz"
arecord -D hw:0,1 -f S16_LE -r "$RATE" -c 1 -d "$DUR" "$OUT" >/dev/null 2>&1 &
APID=$!
sleep 6
# shellcheck disable=SC2024  # sudo is for the READ; the redirect target is /tmp and needs none
sudo cat $REG > /tmp/reg-run.txt
echo "REGS CAPTURED at t+6s"
wait $APID
echo "DONE $OUT $(stat -c %s "$OUT") bytes"
