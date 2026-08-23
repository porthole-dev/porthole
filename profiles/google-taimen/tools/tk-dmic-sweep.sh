#!/bin/sh
# scope: device:google-taimen
# tk-dmic-sweep.sh [seconds] -- runs ON THE PHONE.
#
# Records every DMIC0..DMIC5 in turn through DEC7/TX7, each as its own stream
# with the mixer set BEFORE the stream and untouched during it, and all other
# capture ports cleared. Ambient room noise is enough to tell distinct==1 from
# a live mic; the margin is hundreds, not units.
#
# Only DMIC0 had ever been tested with this discipline. On a WCD934x each DMIC
# pin-pair shares one clock and one data line, with the two channels sampled on
# opposite clock edges, so a mono mic wired to the "odd" slot returns nothing
# when the "even" one is selected -- worth sweeping rather than assuming.
set -e
DUR=${1:-12}

sudo rc-service greetd stop >/dev/null 2>&1 || true
sudo pkill -9 pulseaudio >/dev/null 2>&1 || true
sleep 1

for d in 0 1 2 3 4 5; do
	for i in 0 1 2 3 4 5 6 7 8; do
		amixer -c0 -q cset name="AIF1_CAP Mixer SLIM TX$i" 0 >/dev/null 2>&1 || true
	done
	amixer -c0 -q cset name='MultiMedia2 Mixer SLIMBUS_0_TX' 1 >/dev/null 2>&1
	amixer -c0 -q cset name='AIF1_CAP Mixer SLIM TX7' 1 >/dev/null 2>&1
	amixer -c0 -q cset name='CDC_IF TX7 MUX' DEC7 >/dev/null 2>&1
	amixer -c0 -q cset name='ADC MUX7' DMIC >/dev/null 2>&1
	amixer -c0 -q cset name="DMIC MUX7" "DMIC$d" >/dev/null 2>&1
	sleep 1
	arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d "$DUR" "/tmp/sweep-$d.wav" >/dev/null 2>&1
	echo "DMIC$d done"
done
echo ALLDONE
