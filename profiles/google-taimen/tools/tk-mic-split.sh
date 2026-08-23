#!/bin/sh
# scope: device:google-taimen
# tk-mic-split.sh -- runs ON THE PHONE. Three captures, one question each.
#
#  1+2. DMIC0 twice, mixer untouched between them. If the constant DIFFERS
#       between two identical runs, it is not a property of the microphone pad
#       -- it is residue latched somewhere downstream, and every "the mic is
#       not powered / not clocked" theory dies.
#  3.   The analog front end (AMIC1 via ADC1). A real ADC on an open or biased
#       input CANNOT emit a bit-exact repeated sample; it always has thermal
#       noise. If AMIC is flat too, the constant is manufactured downstream of
#       the decimator and the DMIC front end is innocent.
#
# Register dumps are taken with the regmap CACHE BYPASSED, so they are the
# silicon and not what the driver believes it wrote.
set -e
DUR=${1:-8}
REGDIR=/sys/kernel/debug/regmap/217:250:1:0

sudo rc-service greetd stop >/dev/null 2>&1 || true
sudo pkill -9 pulseaudio >/dev/null 2>&1 || true
sleep 1
sudo sh -c "echo Y > $REGDIR/cache_bypass" 2>/dev/null || echo "WARN: no cache_bypass"

set_ctl() {
	amixer -c0 -q cset name="$1" "$2" >/dev/null 2>&1 ||
		echo "WARN: cset '$1' = '$2' failed"
}

run() {
	tag=$1
	echo "--- $tag ---"
	arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d "$DUR" "/tmp/split-$tag.wav" >/dev/null 2>&1 &
	p=$!
	sleep 4
	sudo cat $REGDIR/registers > "/tmp/split-$tag.regs"
	wait $p
	echo "$tag done"
}

set_ctl 'MultiMedia2 Mixer SLIMBUS_0_TX' 1
set_ctl 'AIF1_CAP Mixer SLIM TX7' 1
set_ctl 'CDC_IF TX7 MUX' DEC7
set_ctl 'ADC MUX7' DMIC
set_ctl 'DMIC MUX7' DMIC0
run dmic0-a
run dmic0-b

set_ctl 'ADC MUX7' AMIC
set_ctl 'AMIC MUX7' ADC1
run amic1

echo "ALL DONE"
