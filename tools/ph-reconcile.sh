#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: - (host only, no device)
# env: PORTHOLE_BASE_TAG
# exits: 0 ok · 1 failed
# ph-reconcile.sh -- does the aport series and the linux/ tree contain the same code?
#
# WHY THIS EXISTS
#   On 2026-08-20 two regressions landed on the phone from the same cause: the
#   aport and the dev branch each had fixes the other lacked, and flashing from
#   either silently dropped the other's work (docs/RECONCILIATION-2026-08-20.md).
#   Nothing warned. This answers the question directly by rebuilding the aport
#   series from vanilla v6.18 and diffing the result against linux/ HEAD.
#
#   Run it BEFORE a flash. "only-tree" lines are code that will NOT be on the
#   phone; "only-aport" lines are code that is on the phone but not in the tree
#   you are reading. Both directions have bitten this project.
#
# TRAP that broke the first version of this script: `git archive v6.18 <paths>`
#   fails wholesale if ANY path does not exist at v6.18, and most of this series
#   CREATES files. The scratch tree came out empty, every patch "failed", and it
#   read exactly like a broken aport. Extract file by file, skipping the ones
#   that upstream does not have -- the patches create those themselves.
#
# ponytail: no caching, no incremental mode. It takes ~20 s and it is only run
# before a flash. Add caching when that stops being true.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APORT="$REPO/pmaports/device/testing/linux-postmarketos-qcom-msm8998-6.18"
BASE="${PORTHOLE_BASE_TAG:-v6.18}"
S=$(mktemp -d)
trap 'rm -rf "$S"' EXIT

grep -h "^+++ b/" "$APORT"/*.patch | sed 's|^+++ b/||' | sort -u > "$S/files.txt"
while read -r f; do
	git -C "$REPO/linux" cat-file -e "$BASE:$f" 2>/dev/null || continue
	mkdir -p "$S/$(dirname "$f")"
	git -C "$REPO/linux" show "$BASE:$f" > "$S/$f" 2>/dev/null
done < "$S/files.txt"

( cd "$S" && git init -q . && git add -A && git -c user.email=t@t -c user.name=t commit -qm base ) >/dev/null 2>&1

fail=0
for p in "$APORT"/0*.patch; do
	( cd "$S" && git apply "$p" 2>/dev/null ) || { echo "APORT DOES NOT APPLY: $(basename "$p")"; fail=1; }
done
[ "$fail" -eq 0 ] || { echo; echo "The aport series is broken on its own -- fix that before comparing."; exit 1; }

echo "aport series applies cleanly to $BASE"
echo
printf "%-44s %10s %10s\n" "file" "only-tree" "only-aport"
d=0
while read -r f; do
	[ -f "$S/$f" ] || continue
	git -C "$REPO/linux" cat-file -e "HEAD:$f" 2>/dev/null || continue
	git -C "$REPO/linux" show "HEAD:$f" > "$S/.t" 2>/dev/null
	ot=$(diff "$S/.t" "$S/$f" | grep -c "^<")
	oa=$(diff "$S/.t" "$S/$f" | grep -c "^>")
	[ "$ot" = 0 ] && [ "$oa" = 0 ] && continue
	printf "%-44s %10s %10s\n" "${f##*/}" "$ot" "$oa"
	d=$((d+1))
done < "$S/files.txt"
echo
echo "$d files differ between the aport series and linux/ HEAD"
