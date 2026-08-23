#!/bin/bash
# scope: soc:msm8998
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_USER
# exits: 0 ok · non-zero on failure
# tk-pkgcheck.sh -- is the thing that SHIPS the thing you EDITED?
#
# Run before every flash, and after every aport edit.
#
# WHY THIS EXISTS
#   Three separate times on 2026-08-21 an edit did not reach the device, each
#   looking completely different and all being the same bug:
#
#     1. device-google-taimen r12 was rebuilt IN PLACE after editing
#        kernel-cmdline.mainline.conf. apk saw the same version string and
#        skipped the upgrade. The old file shipped.
#     2. `pmbootstrap index` was not chained after the build, so apk could not
#        see the new package and silently kept the installed one.
#     3. The -kernel-mainline SUBPACKAGE, which is what actually installs
#        /usr/lib/kernel-cmdline.d/50-device-google-taimen.conf, sat at r6
#        while the parent aport was at r13. Seven revisions of drift in a file
#        nobody thought of as separately versioned.
#
#   Earlier the same day, the kernel aport had patches appended without a
#   pkgrel bump, so two different trees both reported #40 in /proc/version.
#
#   Every one of these is "the version string is not a function of the
#   content". So check content, not versions.
#
# WHAT IT CHECKS
#   A. CONTENT vs PKGREL. Hash every source file + APKBUILD of each aport we
#      own. If that hash changed since last time but pkgrel did not, fail --
#      that is exactly trap 1, and it is the one apk cannot protect you from.
#   B. APORT vs INSTALLED. Compare each aport's pkgrel against what is actually
#      installed, in the rootfs chroot and on the device, for the package AND
#      every subpackage. That is traps 2 and 3.
#
# ponytail: a flat file of hashes, no database. Add per-file granularity when
# "which file changed" is a question someone actually asks.
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APORTS="$REPO/pmaports/device/testing"
STATE="$REPO/.pkg-content-hashes"
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
OWNED=(device-google-taimen linux-postmarketos-qcom-msm8998-6.18)
rc=0; touch "$STATE"

note() { printf '%s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; rc=1; }

# --- A. content vs pkgrel ---------------------------------------------------
for p in "${OWNED[@]}"; do
	d="$APORTS/$p"; [ -d "$d" ] || { fail "$p: no such aport"; continue; }
	rel=$(sed -n 's/^pkgrel=//p' "$d/APKBUILD" | head -1)
	# Hash the APKBUILD and every regular file beside it, sorted for stability.
	h=$(find "$d" -maxdepth 1 -type f -print0 | sort -z | xargs -0 sha256sum \
		| sha256sum | cut -d' ' -f1)
	prev=$(awk -v k="$p" '$1==k {print $2" "$3}' "$STATE")
	prev_h=${prev%% *}; prev_rel=${prev##* }
	if [ -n "$prev" ] && [ "$h" != "$prev_h" ] && [ "$rel" = "$prev_rel" ]; then
		fail "$p: CONTENT CHANGED but pkgrel is still $rel."
		note "      apk will not upgrade a package whose version did not move,"
		note "      so the edit you just made would not ship. Bump pkgrel."
	else
		note "ok    $p r$rel (content hash ${h:0:12})"
	fi
	grep -v "^$p " "$STATE" > "$STATE.new" 2>/dev/null || true
	printf '%s %s %s\n' "$p" "$h" "$rel" >> "$STATE.new"
	mv "$STATE.new" "$STATE"
done

# --- B. aport vs installed --------------------------------------------------
inst_chroot=$(pmbootstrap -y chroot -r -- apk info -v 2>/dev/null)
inst_dev=$(timeout 15 ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
	'apk info -v' 2>/dev/null)

for p in "${OWNED[@]}"; do
	d="$APORTS/$p"; [ -d "$d" ] || continue
	rel=$(sed -n 's/^pkgrel=//p' "$d/APKBUILD" | head -1)
	ver=$(sed -n 's/^pkgver=//p' "$d/APKBUILD" | head -1)
	# The parent plus every subpackage name, which is where drift hides.
	names=$(awk '/^subpackages=/,/^"/' "$d/APKBUILD" \
		| grep -o '\$pkgname-[a-z0-9-]*' | sed "s|\$pkgname|$p|" | sort -u)
	for n in $p $names; do
		for where in chroot dev; do
			[ "$where" = chroot ] && hay="$inst_chroot" || hay="$inst_dev"
			[ -n "$hay" ] || continue
			got=$(printf '%s\n' "$hay" | grep -E "^$n-[0-9]" | head -1)
			[ -n "$got" ] || continue
			want="$n-$ver-r$rel"
			if [ "$got" != "$want" ]; then
				fail "$where: $n is $got, aport says $want"
			fi
		done
	done
done

# The kernel's own claim, which lies after a boot-only flash (modules and the
# apk DB are not replaced), so /proc/version is the only truth.
kver=$(timeout 15 ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
	'grep -o "#[0-9]*" /proc/version' 2>/dev/null)
krel=$(sed -n 's/^pkgrel=//p' "$APORTS/linux-postmarketos-qcom-msm8998-6.18/APKBUILD" | head -1)
if [ -n "$kver" ]; then
	want="#$((krel + 1))"
	[ "$kver" = "$want" ] && note "ok    running kernel $kver == aport r$krel + 1" \
		|| fail "device runs kernel $kver, aport r$krel expects $want"
fi

[ $rc -eq 0 ] && note "" && note "everything that ships is what you edited."
exit $rc
