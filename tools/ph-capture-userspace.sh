#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: PORTHOLE_ARCH, PORTHOLE_SANDBOX_PMB_DIR, PORTHOLE_LOCAL_REPO, PORTHOLE_DEVICE
# exits: 0 ok · 1 failed · 64 usage · 69 no local repo to compare against
# Capture the device's locally-built package set, so a from-scratch install
# cannot silently swap a hand-built package for whatever the local repo
# happens to hold under the same name.
#
# The device carries mesa r14, webkit r63, epiphany r50 and gst-plugins-good
# r1 while the sandbox repo holds gst-plugins-good r50 -- a reinstall from the
# repo would return a DIFFERENT gst than the one on the phone, silently. This
# manifest is what turns that into a decision instead of an accident.
#
#   tools/ph-capture-userspace.sh capture <outfile>   record the device's set
#   tools/ph-capture-userspace.sh restore <infile>    reinstall it
#   tools/ph-capture-userspace.sh --parse-only        filter stdin, no device
#
# Manifest format: one `name-version-release` per line, `#` comments allowed.
#
# --parse-only reads `apk info -v` output on stdin with no device and no ssh --
# that is what makes the filter logic testable. See tests/test_capture_userspace.py.
set -eu

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
	local names; names=$(repo_names)
	while read -r line; do
		[ -n "$line" ] || continue
		local name="${line%-*-r*}"
		printf '%s\n' "$names" | grep -qxF "$name" && printf '%s\n' "$line"
	done
	return 0
}

case "${1:-}" in
	--parse-only) parse ;;
	capture)
		[ -n "${2:-}" ] || { echo "usage: $0 capture OUTFILE" >&2; exit 64; }
		{
			echo "# porthole userspace manifest -- $(date -u +%FT%TZ)"
			echo "# device: ${PORTHOLE_DEVICE:-unknown}"
			tk_run "apk info -v" | parse
		} > "$2"
		echo "captured $(grep -cv '^#' "$2") package(s) to $2" ;;
	restore)
		[ -s "${2:-}" ] || { echo "usage: $0 restore INFILE" >&2; exit 64; }
		# `apk add` pins an exact build with `name=version`, not with the
		# manifest's own dash-joined `name-version` -- so split each line the
		# same way parse() does before handing it to the device.
		specs=$(grep -v '^#' "$2" | while read -r line; do
			[ -n "$line" ] || continue
			name="${line%-*-r*}"
			printf '%s=%s\n' "$name" "${line#"$name"-}"
		done)
		[ -n "$specs" ] || { echo "no packages in $2" >&2; exit 0; }
		# shellcheck disable=SC2086  # word-splitting the specs is the point
		tk_run sudo -n apk add $specs ;;
	*) echo "usage: $0 {capture OUTFILE|restore INFILE|--parse-only}" >&2; exit 64 ;;
esac
