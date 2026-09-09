#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: - (host only, no device -- reads the aport sources)
# env: PORTHOLE_PMB_DIR
# exits: 0 ok · 1 a contributed flag was lost · 64 usage · 69 confs not found
# ph-chromium-conf-check.sh [confdir] -- runs ON THE HOST.
#
# Asserts the one property that /etc/chromium/*.conf has to have: however many
# files contribute Chromium features, EXACTLY ONE --enable-features switch is
# emitted and no contributed name is lost.
#
# WHY THIS IS A TEST AND NOT A COMMENT. base::CommandLine keeps only the LAST
# occurrence of a switch, so two conf files that each append their own
# --enable-features do not merge -- the earlier one is silently deleted and
# still reads as though it works. Hardware video decode and the mobile
# scrollbars are contributed by two different packages, so that failure would
# have looked exactly like "V4L2 decode does not work on this device".
# The same shape already cost a session once, in environment.d rather than
# here: brain/traps/the-av1-demotion-deleted-the-v4l2-ranks.
#
# Two scenarios, because the device conf carries a fallback for a chromium
# that ships no emitter (Alpine's stock build, for one):
#   forked - chromium + taimen + v4l2-venus + zz-features   (emitter present)
#   stock  - chromium + taimen                              (no emitter)
set -eu

PMB=${PORTHOLE_PMB_DIR:-$HOME/.local/var/pmbootstrap}
APORTS=$PMB/cache_git/pmaports

if [ $# -gt 1 ]; then
	echo "usage: $0 [confdir]" >&2; exit 64
fi

if [ $# -eq 1 ]; then
	SRC=$1
	for c in chromium.conf taimen-chromium.conf v4l2-venus.conf zz-features.conf; do
		[ -f "$SRC/$c" ] || { echo "missing $SRC/$c" >&2; exit 69; }
	done
else
	SRC=$(mktemp -d); trap 'rm -rf "$SRC"' EXIT
	for c in chromium.conf v4l2-venus.conf zz-features.conf; do
		f=$APORTS/temp/chromium/$c
		[ -f "$f" ] || { echo "missing $f -- is temp/chromium forked?" >&2; exit 69; }
		cp "$f" "$SRC/"
	done
	f=$APORTS/device/testing/device-google-taimen/taimen-chromium.conf
	[ -f "$f" ] || { echo "missing $f" >&2; exit 69; }
	cp "$f" "$SRC/"
fi

TMP=$(mktemp -d)
# shellcheck disable=SC2064
trap "rm -rf '$TMP'; [ $# -eq 1 ] || rm -rf '$SRC'" EXIT

# The device conf's guard tests an absolute path, matching the launcher's
# hardcoded glob. Rewrite that one path into the scenario dir. TEST-ONLY, and
# it must stay in step with the guard in taimen-chromium.conf.
setup() {
	d=$TMP/$1; mkdir -p "$d"; shift
	for c in "$@"; do
		sed "s|/etc/chromium/zz-features.conf|$d/zz-features.conf|g" \
			"$SRC/$c" > "$d/$c"
	done
	echo "$d"
}

run() (
	CHROMIUM_FLAGS=""
	for f in "$1"/*.conf; do [ -f "$f" ] && . "$f"; done
	echo "$CHROMIUM_FLAGS"
)

rc=0
check() {
	scenario=$1; flags=$2; shift 2
	echo "  $scenario: $flags"
	n=$(printf '%s\n' $flags | grep -c '^--enable-features=' || true)
	if [ "$n" -ne 1 ]; then
		echo "  FAIL [$scenario]: $n --enable-features switches, want exactly 1" >&2
		rc=1
	fi
	for want in "$@"; do
		case "$flags" in
			*"$want"*) ;;
			*) echo "  FAIL [$scenario]: lost '$want'" >&2; rc=1 ;;
		esac
	done
}

d=$(setup forked chromium.conf taimen-chromium.conf v4l2-venus.conf zz-features.conf)
check forked "$(run "$d")" \
	AcceleratedVideoDecoder OverlayScrollbar \
	--disable-gpu-rasterization --top-chrome-touch-ui=enabled

d=$(setup stock chromium.conf taimen-chromium.conf)
check stock "$(run "$d")" OverlayScrollbar --disable-gpu-rasterization

[ "$rc" -eq 0 ] && echo "PASS: one --enable-features switch in both scenarios, nothing lost"
exit "$rc"
