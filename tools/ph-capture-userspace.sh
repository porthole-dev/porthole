#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: PHONE, PORTHOLE_USER, HOST, TK_RUN_TIMEOUT, PORTHOLE_ARCH,
#      PORTHOLE_SANDBOX_PMB_DIR, PORTHOLE_LOCAL_REPO, PORTHOLE_DEVICE
# exits: 0 ok · 1 failed, or the device answered but nothing matched the local
#        repo · 64 usage · 69 no local repo to compare against, or the device
#        could not be reached
# Capture the device's locally-built package set, so a from-scratch install
# cannot silently swap a hand-built package for whatever the local repo
# happens to hold under the same name.
#
# The device carries mesa r14, webkit r63, epiphany r50 and gst-plugins-good
# r1 while the sandbox repo holds gst-plugins-good r50 -- a reinstall from the
# repo would return a DIFFERENT gst than the one on the phone, silently. This
# manifest is what turns that into a decision instead of an accident.
#
# device-*/firmware-* packages the repo builds are recorded as `# excluded:`
# comments, not as restorable lines: the install itself provides them, and
# `apk add` on one can flash the boot partition or eat the radio stack.
#
#   tools/ph-capture-userspace.sh capture <outfile>   record the device's set
#   tools/ph-capture-userspace.sh restore <infile>    reinstall it
#   tools/ph-capture-userspace.sh --parse-only        filter stdin, no device
#
# Manifest format: one `name-version-release` per line, `#` comments allowed.
#
# --parse-only reads `apk info -v` output on stdin with no device and no ssh --
# that is what makes the filter logic testable. See tests/test_capture_userspace.py.
#
# pipefail is deliberate: `tk_run ... | parse` must not read as success when
# tk_run failed. parse() always returns 0 (it exists to filter a stream, not
# to report on the read that fed it), so without pipefail a dead device or a
# failing `apk info -v` would write a manifest with a clean header, a
# "captured 0 package(s)" line, and exit 0 -- indistinguishable from a real
# capture of a device with nothing locally built. At Gate C that manifest
# would sail through and the wipe would proceed with the hand-built userspace
# gone.
set -euo pipefail

cd "$(dirname "$0")"
. ./ph-lib.sh

# The set of package NAMES this box has actually built: whatever names appear
# in the sandbox's own package repo, regardless of which pkgrel is sitting
# there right now. PORTHOLE_LOCAL_REPO overrides the directory outright, for a
# host running a differently-laid-out repo.
repo_names() {
	local dir="${PORTHOLE_LOCAL_REPO:-}"
	[ -n "$dir" ] || dir="${PORTHOLE_SANDBOX_PMB_DIR:-$HOME/.local/var/porthole-sandbox}/packages/edge/${PORTHOLE_ARCH:-aarch64}"
	dir="${dir/#\~/$HOME}"
	[ -d "$dir" ] || { echo "ph-capture-userspace: no local repo at $dir" >&2; exit 69; }
	# `mesa-dbg-26.1.6-r14.apk` -> `mesa-dbg`. Strip .apk, then the trailing
	# -<pkgver>-r<pkgrel>, which is the only part guaranteed to be two dashed
	# fields from the end -- pkgnames themselves can contain digits and dashes,
	# so there is no other anchor.
	ls "$dir" | sed -n 's/\.apk$//p' | sed 's/-[^-]*-r[0-9]*$//' | sort -u
}

parse() {
	# stdin: `apk info -v` output (one `name-pkgver-rN` per line). stdout: only
	# the lines whose NAME is something the local repo builds -- the exact
	# pkgrel on the device is kept verbatim, never rewritten to the repo's.
	#
	# A device-*/firmware-* NAME THE REPO BUILDS is recorded but never handed
	# to restore: the install itself provides these (world file, deviceinfo),
	# and `apk add` on one can flash the boot partition or eat the radio stack
	# -- brain/traps/installing-firmware-can-flash-the-boot-partition.md and
	# brain/traps/a-sideloaded-device-apk-can-eat-the-radio-stack.md. Anchored
	# on repo_names(), not on the raw name, so an unrelated upstream package
	# that merely starts with "device-" (device-mapper) is not caught by this
	# -- it was never a locally-built name to begin with.
	#
	# The match itself is a bare `device-*`/`firmware-*` prefix on a name the
	# repo DOES build, which is deliberately broader than "packages this
	# device port owns": a hypothetical locally-built `device-mapper` would
	# also be excluded by it (tests/test_capture_userspace.py proves this).
	# Left as is on purpose -- the two costs are not symmetric. Wrongly
	# excluding a restorable package costs a rebuild; wrongly restoring a
	# device/firmware package can flash boot or kill the radio. Erring toward
	# exclusion is correct, so do not tighten this without re-reading that
	# asymmetry.
	local names; names=$(repo_names)
	while read -r line; do
		[ -n "$line" ] || continue
		local name="${line%-*-r*}"
		printf '%s\n' "$names" | grep -qxF "$name" || continue
		case $name in
			device-*|firmware-*) printf '# excluded: %s\n' "$line" ;;
			*)                   printf '%s\n' "$line" ;;
		esac
	done
	return 0
}

case "${1:-}" in
	--parse-only) parse ;;
	capture)
		[ -n "${2:-}" ] || { echo "usage: $0 capture OUTFILE" >&2; exit 64; }
		# Captured into a variable BEFORE anything is written to $2, and
		# checked in two stages, so a failed capture never produces a file at
		# all -- not a truncated one, not a "0 package(s)" success. pipefail
		# (set above) is what makes the `||` here see tk_run's failure through
		# the pipe instead of parse()'s always-0 return.
		body=$(tk_run "apk info -v" | parse) || {
			echo "ph-capture-userspace: could not read the package list from ${PORTHOLE_DEVICE:-the device} -- refusing to write a manifest" >&2
			exit 69
		}
		[ -n "$body" ] || {
			echo "ph-capture-userspace: the device answered but nothing matched the local repo -- refusing to write an empty manifest" >&2
			exit 1
		}
		{
			echo "# porthole userspace manifest -- $(date -u +%FT%TZ)"
			echo "# device: ${PORTHOLE_DEVICE:-unknown}"
			echo "# excluded -- device/firmware packages are installed by the install itself, and"
			echo "# apk add on them can flash boot / eat the radio stack. See"
			echo "# brain/traps/installing-firmware-can-flash-the-boot-partition.md and"
			echo "# brain/traps/a-sideloaded-device-apk-can-eat-the-radio-stack.md"
			printf '%s\n' "$body"
		} > "$2"
		echo "captured $(printf '%s\n' "$body" | grep -cv '^#') package(s) to $2" ;;
	restore)
		[ -s "${2:-}" ] || { echo "usage: $0 restore INFILE" >&2; exit 64; }
		# `apk add` pins an exact build with `name=version`, not with the
		# manifest's own dash-joined `name-version` -- so split each line the
		# same way parse() does before handing it to the device.
		#
		# `|| true`: a manifest that is ALL excluded/comment lines makes
		# `grep -v '^#'` find nothing, which is a normal outcome here, not a
		# failure -- pipefail must not turn that into a hard stop before the
		# empty-specs check below gets to see it.
		specs=$(grep -v '^#' "$2" | while read -r line; do
			[ -n "$line" ] || continue
			name="${line%-*-r*}"
			printf '%s=%s\n' "$name" "${line#"$name"-}"
		done || true)
		[ -n "$specs" ] || { echo "no packages in $2" >&2; exit 0; }
		# shellcheck disable=SC2086  # word-splitting the specs is the point
		tk_run sudo -n apk add $specs ;;
	*) echo "usage: $0 {capture OUTFILE|restore INFILE|--parse-only}" >&2; exit 64 ;;
esac
