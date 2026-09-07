#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: - (host only, no device)
# env: HOST, PORTHOLE_DTC_OUT
# exits: 0 ok · 1 failed
# ph-dtc.sh [board.dts] -- compile a board DTS on the HOST, before asking for a
# kernel build.
#
# Why this exists: docs/HANDOFF-audio.md section 5 rule 2 -- a bad devicetree on
# this device does not fail gracefully, it hangs the boot at the Google splash
# and burns A/B slot retries. Every DT change is therefore worth compiling
# first, and `make dtbs` needs the whole cross toolchain and sudo, which is the
# human's build cycle, not ours.
#
# The in-tree scripts/dtc cannot just be built with `make scripts_dtc` without a
# configured tree, and a naive `gcc *.c` pulls in fdtget/fdtput (which do not
# belong to dtc) and trips over dt_to_yaml. This builds exactly the dtc objects,
# stubbing the YAML output nobody here uses.
#
# This checks that the tree PARSES and that every phandle, label and
# dt-bindings constant resolves. It cannot tell you a widget name is wrong or a
# register is at the wrong address -- only the device can.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
LINUX=$ROOT/linux
OUT=${PORTHOLE_DTC_OUT:-/tmp/tk-dtc}
DTS=${1:-$LINUX/arch/arm64/boot/dts/qcom/msm8998-google-taimen.dts}

mkdir -p "$OUT"

if [ ! -x "$OUT/dtc" ]; then
	echo ">> building host dtc"
	cd "$LINUX/scripts/dtc"
	bison -o "$OUT/dtc-parser.tab.c" -d dtc-parser.y
	flex -o "$OUT/dtc-lexer.lex.c" dtc-lexer.l
	# dtc.c references dt_to_yaml unconditionally; the real one needs libyaml.
	printf '#include "dtc.h"\nvoid dt_to_yaml(FILE *f, struct dt_info *d)\n{ (void)f; (void)d; die("dtc built without yaml support\\n"); }\n' \
		> "$OUT/noyaml.c"
	gcc -O1 -I. -I"$OUT" -Ilibfdt -o "$OUT/dtc" \
		checks.c data.c dtc.c flattree.c fstree.c livetree.c srcpos.c \
		treesource.c util.c "$OUT/noyaml.c" \
		"$OUT/dtc-parser.tab.c" "$OUT/dtc-lexer.lex.c" libfdt/*.c 2>/dev/null
fi

cd "$LINUX"
echo ">> preprocessing $(basename "$DTS")"
cpp -nostdinc -I include -I arch/arm64/boot/dts -undef \
    -x assembler-with-cpp "$DTS" -o "$OUT/board.dts.pp"

echo ">> compiling"
# The msm8998 dtsi emits a pile of pre-existing unit-address/simple-bus
# warnings; showing them every run trains you to ignore the output. Report
# errors always, and warnings only for the board file being changed.
if ! "$OUT/dtc" -I dts -O dtb -o "$OUT/board.dtb" "$OUT/board.dts.pp" 2>"$OUT/log"; then
	echo "!! FAILED"
	cat "$OUT/log"
	exit 1
fi
grep -i "$(basename "${DTS%.dts}")" "$OUT/log" || true
printf 'OK  %s -> %s (%s bytes)\n' \
	"$(basename "$DTS")" "$OUT/board.dtb" "$(stat -c %s "$OUT/board.dtb")"
printf '    %d pre-existing warnings from included dtsi (see %s)\n' \
	"$(grep -c Warning "$OUT/log" || true)" "$OUT/log"
