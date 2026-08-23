#!/bin/sh
# scope: generic
# needs: BOOTED
# env: PHONE
# exits: 0 ok · non-zero on failure
# tk-mic-watch.sh [seconds] -- runs ON THE PHONE.
#
# Samples the handful of registers that matter once a second DURING a capture,
# so a path that powers itself down part way through is visible as a change
# rather than having to be inferred from the audio.
#
# The regmap debugfs "registers" file is fixed 10 bytes per line
# ("%04x: %02x\n"), so a register is one dd at offset reg*10 -- reading all
# 64K lines once a second would itself perturb the timing.
set -e

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"
DUR=${1:-20}
REG=/sys/kernel/debug/regmap/217:250:1:0/registers

sudo rc-service greetd stop >/dev/null 2>&1 || true
sudo pkill -9 pulseaudio >/dev/null 2>&1 || true
sleep 1
sudo sh -c 'echo Y > /sys/kernel/debug/regmap/217:250:1:0/cache_bypass' 2>/dev/null || true

for i in 0 1 2 3 4 5 6 7 8; do
	amixer -c0 -q cset name="AIF1_CAP Mixer SLIM TX$i" 0 >/dev/null 2>&1 || true
done
amixer -c0 -q cset name='MultiMedia2 Mixer SLIMBUS_0_TX' 1 >/dev/null 2>&1
amixer -c0 -q cset name='AIF1_CAP Mixer SLIM TX7' 1 >/dev/null 2>&1
amixer -c0 -q cset name='CDC_IF TX7 MUX' DEC7 >/dev/null 2>&1
amixer -c0 -q cset name='ADC MUX7' DMIC >/dev/null 2>&1
amixer -c0 -q cset name='DMIC MUX7' DMIC0 >/dev/null 2>&1

# seq_file cannot be seeked, so read a bounded prefix (10 bytes per line) that
# still covers the highest register of interest, 0x0aa7.
rd() { sudo head -c 28000 $REG | grep -E "^(0218|0601|0622|0aa1|0aa2|0aa7):" | tr '\n' ' '; }

# Wall-clock timestamps, not loop counts: each bounded read is itself slow
# enough that a loop counter drifts far behind real time, which makes the end
# of the capture look like a power-down in the middle of it.
T0=$(date +%s%N)
arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d "$DUR" /tmp/watch.wav >/dev/null 2>&1 &
p=$!
while kill -0 $p 2>/dev/null; do
	# 0218 dmic clk | 0601 ana_bias | 0622 micb1 | 0aa1 tx7 ctl | 0aa2 cfg0 | 0aa7 sec2
	echo "t=$(( ($(date +%s%N) - T0) / 100000000 ))ds $(rd)"
done
wait $p 2>/dev/null || true
echo "arecord ended at t=$(( ($(date +%s%N) - T0) / 100000000 ))ds"
echo "after: $(rd)"
