#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: BOOTED
# env: FASTBOOT, HOST, PHONE, PORTHOLE_USER
# exits: 0 ok · 1 failed
# tk-audio-cycle.sh [--dtb] [--kernel] -- one command from "make finished" to
# "here is what the device says".
#
# Everything the audio work touches is a MODULE on this device
# (CONFIG_SLIMBUS=m, CONFIG_SLIM_QCOM_NGD_CTRL=m, the whole qdsp6 stack and
# wcd934x =m), so the SLIMbus transport fixes need no boot.img, no fastboot and
# no flash: push the .ko files, reboot, test. ~60s.
#
# Only two things need more than that:
#   --dtb     the QUAT MI2S port/pinctrl/dai-link  -> repack + RAM boot
#   --kernel  CONFIG_REGMAP_ALLOW_WRITE_DEBUGFS is in regmap-debugfs.c, which
#             is built in (CONFIG_REGMAP=y) -> needs the kernel image spliced
#             in as well, otherwise `tk-lab.py poke` stays dead.
#
# THE TRAP THIS EXISTS TO CATCH (docs/HANDOFF-audio.md section 6): a "rebuilt"
# module can silently predate your edit, and then you spend an evening
# explaining a result produced by the OLD code. Timestamps are not enough --
# a partial build leaves a fresh mtime on an object that was not recompiled.
# So every module is checked for a string that only the NEW source contains,
# and the script refuses to push if it is missing.
set -euo pipefail

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${KBUILD_OUTPUT:-$ROOT/linux/.output}
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
DO_DTB=0; DO_KERNEL=0
for a in "$@"; do
	case "$a" in
	--dtb) DO_DTB=1 ;;
	--kernel) DO_DTB=1; DO_KERNEL=1 ;;
	*) echo "usage: $0 [--dtb] [--kernel]"; exit 1 ;;
	esac
done

# module basename : a string present ONLY in the new source
declare -A WANT=(
	[slimbus.ko]='Failed to connect port'
	[slim-qcom-ngd-ctrl.ko]='qcom_slim_ngd_disable_stream'
	[snd-soc-wcd934x.ko]='mclk_rate_from_clk'
	[snd-soc-msm8998.ko]='QUAT MI2S bclk err'
	[q6afe.ko]='TKGETP'
	[q6adm.ko]='probe_budget'
	[q6routing.ko]='probe_budget'
	[q6asm.ko]='TKREAD'
	[q6asm-dai.ko]='TKBUF'
)

echo "== locating modules under $OUT"
PUSH=()
MISSING=0
for ko in "${!WANT[@]}"; do
	path=$(find "$OUT" -name "$ko" -type f 2>/dev/null | head -1)
	if [ -z "$path" ]; then
		echo "  !! $ko  NOT BUILT"
		MISSING=1
		continue
	fi
	# grep -c, not grep -q: with `set -o pipefail`, a -q grep exits the moment
	# it matches, SIGPIPEs `strings`, and the pipeline then reports FAILURE
	# because the string was found. Whether that trips depends on where in the
	# object the match lands, so it silently marked two of seven fresh modules
	# "stale" and passed the rest.
	if [ "$(strings "$path" | grep -cF "${WANT[$ko]}" || true)" = "0" ]; then
		echo "  !! $ko  STALE -- '${WANT[$ko]}' absent, this is the old object"
		MISSING=1
		continue
	fi
	printf '  ok %-26s %8s bytes  %s\n' "$ko" "$(stat -c %s "$path")" \
		"$(date -r "$path" +%H:%M:%S)"
	PUSH+=("$path")
done
[ "$MISSING" -eq 0 ] || { echo "refusing to push a half-built set"; exit 1; }

echo "== pushing $(( ${#PUSH[@]} )) modules"
"$ROOT/tools/tk-push-module.sh" "${PUSH[@]}"

if [ "$DO_DTB" -eq 1 ]; then
	DTB=$OUT/arch/arm64/boot/dts/qcom/msm8998-google-taimen.dtb
	SRC=${BOOTIMG:-/tmp/postmarketOS-export/boot.img}
	[ -f "$DTB" ] || { echo "no DTB at $DTB -- run 'make dtbs'"; exit 1; }
	[ -f "$SRC" ] || { echo "no source boot.img at $SRC (set BOOTIMG=)"; exit 1; }
	args=("$SRC" "$DTB" /tmp/boot-audio.img)
	if [ "$DO_KERNEL" -eq 1 ]; then
		KI=$OUT/arch/arm64/boot/Image.gz
		[ -f "$KI" ] || KI=$OUT/arch/arm64/boot/Image
		[ -f "$KI" ] || { echo "no kernel image under $OUT"; exit 1; }
		args+=(--kernel "$KI")
		echo "== splicing kernel $(basename "$KI") + DTB"
	else
		echo "== splicing DTB"
	fi
	python3 "$ROOT/tools/bootimg-repack-dtb.py" "${args[@]}"
	echo "== to the bootloader"
	"$ROOT/tools/tk-to-fastboot.sh"
	# RAM boot, never flash: writes nothing, burns no slot retries, and a power
	# cycle undoes it (docs/HANDOFF-audio.md section 5 rule 1).
	"${FASTBOOT:-$HOME/Android/Sdk/platform-tools/fastboot}" boot /tmp/boot-audio.img
else
	echo "== rebooting"
	"$ROOT/tools/tk-reboot.sh"
fi

echo "== waiting for ssh"
for _ in $(seq 120); do
	ssh "${TK_SSH_OPTS[@]}" \
	    -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes \
	    "$PHONE" true 2>/dev/null && break
	sleep 1
done

echo
echo "== is the new code actually running?"
ssh "${TK_SSH_OPTS[@]}" \
    "$PHONE" '
	echo "-- new module params (absent => old module still loaded):"
	for p in /sys/module/snd_soc_wcd934x/parameters/slim_irq_mask_on_err \
	         /sys/module/q6afe/parameters/probe_budget; do
		printf "   %-58s %s\n" "$p" "$(cat $p 2>/dev/null || echo MISSING)"
	done
	printf "   %-58s %s\n" "regmap registers writable (poke)" \
		"$(sudo test -w /sys/kernel/debug/regmap/217:250:1:0/registers && echo yes || echo no)"
	echo "-- unmask the SLIM port interrupt so error RATE is visible:"
	echo 0 | sudo tee /sys/module/snd_soc_wcd934x/parameters/slim_irq_mask_on_err >/dev/null \
		&& echo "   slim_irq_mask_on_err=0" || echo "   FAILED (old module)"
	echo -1 | sudo tee /sys/module/q6afe/parameters/probe_budget >/dev/null 2>&1 || true
	sudo dmesg -C
'

echo
echo "== capture"
python3 "$ROOT/tools/tk-lab.py" cap postfix -d 20 \
	-s 'MultiMedia2 Mixer SLIMBUS_0_TX=1' -s 'AIF1_CAP Mixer SLIM TX7=1' \
	-s 'CDC_IF TX7 MUX=DEC7' -s 'ADC MUX7=DMIC' -s 'DMIC MUX7=DMIC0' \
	2>&1 | tail -12

echo
echo "== what the new error paths say"
ssh "${TK_SSH_OPTS[@]}" \
    "$PHONE" 'sudo dmesg | grep -E "TKSLIMERR|TKREAD|TKBUF|Failed to connect port|No segment distribution|Cannot get presence rate|overflow|underflow|TX timed out|invalid dai id" || echo "   (nothing -- which for TKSLIMERR is the good answer)"'
