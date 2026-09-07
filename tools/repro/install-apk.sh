#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: PORTHOLE_* (ph-lib.sh)
# exits: 0 installed · 1 a push failed · 64 usage
# install-apk.sh APK... -- push locally built apks to the device and install
# them. The generic form of install-mesa.sh, which hardcodes mesa's subpackage
# list; here the caller names the files, so a shell glob picks the set.
#
#   tools/repro/install-apk.sh "$P"/webkit2gtk-6.0{,-lang,-dbg}-2.52.6-r63.apk
#
# --allow-untrusted because these are unsigned local builds. Prints what apk
# actually holds afterwards: the version that installed is the only one that
# counts, and apk silently keeps a HIGHER version already present.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../ph-lib.sh"
[ $# -gt 0 ] || { echo "usage: install-apk.sh APK..." >&2; exit 64; }
for f; do [ -f "$f" ] || { echo "missing $f" >&2; exit 1; }; done
tk_run "mkdir -p /tmp/apk-in && rm -f /tmp/apk-in/*.apk"
scp "${TK_SSH_OPTS[@]}" "$@" "$PHONE:/tmp/apk-in/" >/dev/null || { echo "PUSH FAILED" >&2; exit 1; }
TK_RUN_TIMEOUT=300 tk_run "sudo -n apk add --allow-untrusted /tmp/apk-in/*.apk 2>&1 | tail -15"
tk_run "apk info -v | grep -E '^$(basename "$1" | sed 's/-[0-9].*//')' | sort"
