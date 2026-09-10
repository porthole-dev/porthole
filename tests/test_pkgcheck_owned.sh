#!/bin/sh
# SPDX-License-Identifier: MIT
# Does ph-pkgcheck watch what the manifest says, rather than four names
# frozen into the script? temp/mesa carries the three a5xx patches and was
# not among those four.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
fail=0

# The manifest is the source of truth, and mesa must be in it.
"$ROOT/bin/porthole" -d google-taimen pkg owned --tier required \
	| grep -qx mesa || { echo "FAIL: mesa not in the required tier"; fail=1; }

# The script must not carry a frozen list any more. A literal OWNED=( with
# package names in it is the regression.
if grep -qE '^OWNED=\([a-z]' "$ROOT/tools/ph-pkgcheck.sh"; then
	echo "FAIL: ph-pkgcheck.sh still hardcodes its aport list"
	fail=1
fi

# And it must still work with no manifest at all -- google-cheetah has none.
if ! grep -q 'fallback' "$ROOT/tools/ph-pkgcheck.sh"; then
	echo "FAIL: no documented fallback for a device with no manifest"
	fail=1
fi

# Exercise the real invocation through the production symlink topology.
# The tool runs from a device repo where 'tools' is a symlink to porthole/tools.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/fakerepo"
ln -s "$ROOT/tools" "$tmp/fakerepo/tools"

# Resolve the real pmaports path robustly. Try several paths:
# 1. From the porthole root: ../taimen/pmaports (normal repo)
# 2. From a worktree root: ../../../../taimen/pmaports
real_pmaports=""
for candidate in "$ROOT/../taimen/pmaports" "$ROOT/../../../../taimen/pmaports"; do
	if [ -d "$candidate" ]; then
		real_pmaports=$(cd "$candidate" && pwd)
		break
	fi
done
if [ -z "$real_pmaports" ] || [ ! -d "$real_pmaports" ]; then
	echo "SKIP: real pmaports not found (this is not an error during development)"
	exit 0
fi
ln -s "$real_pmaports" "$tmp/fakerepo/pmaports"

# Run the tool through the fakerepo, capturing section A output.
# Unset PORTHOLE_ROOT so ph-lib.sh recalculates it from the symlink (production behavior).
out=$(cd "$tmp/fakerepo" && unset PORTHOLE_ROOT && timeout 90 bash ./tools/ph-pkgcheck.sh 2>&1 | head -40)

# Section A output should show mesa (from the manifest).
if ! printf '%s\n' "$out" | grep -q 'mesa'; then
	echo "FAIL: mesa not in section A output (manifest not being read)"
	fail=1
fi

# The fallback message must NOT appear (that means the manifest WAS read).
if printf '%s\n' "$out" | grep -q 'no aports.conf for this device'; then
	echo "FAIL: fallback message appeared (manifest was not used)"
	fail=1
fi

# And no "note: command not found" either (that was the bug where note()
# was called before being defined).
if printf '%s\n' "$out" | grep -q 'note:'; then
	echo "FAIL: 'note:' error appeared (function ordering bug)"
	fail=1
fi

[ "$fail" -eq 0 ] && echo ok
exit "$fail"
