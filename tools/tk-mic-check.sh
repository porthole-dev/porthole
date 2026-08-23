#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_USER
# exits: 0 ok
# tk-mic-check.sh -- is the microphone producing AUDIO, or just a noise floor?
#
# WHY A TOOL AND NOT ONE arecord
#   Every naive mic test on this device passes. `arecord -D hw:0,1` opens the
#   PCM, streams for the requested duration, writes a correctly-sized WAV and
#   exits 0 -- while capturing nothing. The file size, the exit code and the
#   pulse source state are all identical whether the mic works or not, which is
#   why "the mic works" has been asserted before and why Sound Recorder in
#   phosh produced an empty clip.
#
#   The only thing that discriminates is the SAMPLE CONTENT. So this reports
#   RMS in dBFS and the distinct-value count, and it names a verdict.
#
# READING THE OUTPUT
#   rms > -60 dBFS ............ real audio is arriving
#   rms ~ -80 dBFS, distinct>1  a live ADC path with no acoustic signal --
#                               either a genuinely dead mic element/clock, or a
#                               silent room. Play a tone to tell them apart.
#   distinct == 1 ............. the documented stuck-DMIC-clock signature: the
#                               clock pad is not driving the mic (see the
#                               dmic_clk_drive comment in wcd934x.c).
#
# GAIN: DEC7 is set to 124 (+40 dB), not 40. Before 2026-08-02 the kcontrol
# clamped at 40, which is -44 dB, and that alone made the mic look dead --
# -93 dBFS spanning two LSBs. If a write of 124 clamps back to 40, you are
# running a kernel without the wcd934x platform_max fix and every number this
# script prints is meaningless. Check: `amixer -c0 cget name='DEC7 Volume'`
# must report max=124.
#
# A SILENT ROOM MAKES "no signal" MEANINGLESS. For a real verdict play a known
# tone near the phone and look for it in that specific frequency bin, with a
# second bin as a control -- see docs/HANDOFF-audio.md 13.4, and note that
# tools/tk-acoustic.py already automates the host-plays-tone loop.
#
# TRAP THIS ENCODES: raw arecord does NOT apply UCM, and pulse tears the path
# down when the source suspends, so a cold `arecord` measures a disabled mixer.
# The enable sequence below is UCM's SectionDevice."Mic", applied by hand.
#
# ponytail: fixed at DMIC1/DEC7, the UCM-declared path. --sweep tries them all.
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
SECS=${SECS:-3}

MEASURE='python3 -c "
import wave,array,math,sys
w=wave.open(sys.argv[1]); a=array.array(\"h\"); a.frombytes(w.readframes(w.getnframes()))
if not len(a): print(\"NO DATA\"); raise SystemExit(2)
rms=math.sqrt(sum(x*x for x in a)/len(a)); peak=max(abs(x) for x in a); d=len(set(a))
dbfs=20*math.log10(rms/32768) if rms>0 else -999
v=(\"REAL AUDIO\" if dbfs>-60 else
   \"STUCK CLOCK (distinct==1)\" if d==1 else
   \"NOISE FLOOR ONLY -- no acoustic signal\")
print(f\"rms={rms:7.1f} ({dbfs:6.1f} dBFS)  peak={peak:5d}  distinct={d:5d}  -> {v}\")
" '

enable_path() {
	local dmic=${1:-DMIC1}
	cat <<EOF
amixer -c0 -q cset name='MultiMedia2 Mixer SLIMBUS_0_TX' 1
amixer -c0 -q cset name='AIF1_CAP Mixer SLIM TX7' 1
amixer -c0 -q cset name='CDC_IF TX7 MUX' DEC7
amixer -c0 -q cset name='ADC MUX7' DMIC
amixer -c0 -q cset name='DMIC MUX7' $dmic
amixer -c0 -q cset name='DEC7 Volume' 124
EOF
}

if [ "${1:-}" = "--sweep" ]; then
	for d in DMIC0 DMIC1 DMIC2 DMIC3 DMIC4 DMIC5; do
		printf '%-7s ' "$d"
		ssh "$PHONE" "$(enable_path $d)
		  timeout $((SECS+5)) arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d $SECS /tmp/mc.wav >/dev/null 2>&1
		  $MEASURE /tmp/mc.wav" 2>&1 | tail -1
	done
	exit 0
fi

# Default: measure the UCM path, then check the SLIMbus TX error counter, which
# is the one kernel-side witness that the codec fed the port nothing.
ssh "$PHONE" "$(enable_path DMIC1)
  timeout $((SECS+5)) arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d $SECS /tmp/mc.wav >/dev/null 2>&1
  $MEASURE /tmp/mc.wav
  n=\$(sudo -n dmesg 2>/dev/null | grep -c 'underflow error on TX port 7')
  echo \"wcd934x TX7 underflows since boot: \$n  (nonzero means the codec is not feeding the port)\"" 2>&1
