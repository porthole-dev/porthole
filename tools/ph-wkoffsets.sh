#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED -- to name the build under test; falls back to the newest apk,
#        loudly, when the device does not answer. nm and readelf on the host.
# env: TK_WK_APK_DIR (default the porthole sandbox package dir), TK_WK_VERSION
#      (default: the version apk reports INSTALLED on the device)
# exits: 0 printed · 1 packages not found · 64 usage
# tk-wkoffsets.sh [SYMBOL...] -- uprobe offsets for WebKit phase entry points,
# from the -dbg and plain webkit apk for the build INSTALLED on the device.
#
# Prints a line to eval, giving tk-wkphase.sh the offsets for the build under
# test. Run it again after every rebuild; that is the whole point.
#
# THE ARITHMETIC, because getting it wrong measures a random instruction:
# `nm` gives a VIRTUAL address; a uprobe wants a FILE OFFSET. The difference is
# the executable LOAD segment's (vaddr - offset), read from the real .so, NOT
# from the .debug file -- the debug file's LOAD entries have zero file size and
# its offsets are meaningless. On 2.52.6 that delta is 0x10000, and the check
# that it is right is that ThreadedCompositor::renderLayerTree comes out at
# 0x226f588, the value tk-webframe.sh has hardcoded since r52.
set -u
# shellcheck source=tk-lib.sh
source "$(dirname "$0")/tk-lib.sh"
DIR=${TK_WK_APK_DIR:-$HOME/.local/var/porthole-sandbox/packages/edge/aarch64}
VER=${TK_WK_VERSION:-}
# ASK THE DEVICE, do not guess from the directory. The old default was the
# newest -dbg apk by `sort -V`, which is the build under test only by luck: on
# 2026-09-06 a --src build (2.52.6_p20260906130803-r63) sorted ABOVE the aport
# r63 the phone was actually running, and its symbols sit 1-2 KB away -- every
# uprobe would have landed inside a neighbouring function and reported numbers
# for code nobody asked about. A wrong offset does not fail; it lies.
if [ -z "$VER" ]; then
	VER=$(tk_run "apk info -v" 2>/dev/null | sed -n 's/^webkit2gtk-6\.0-\(2\..*\)$/\1/p' | head -1)
	[ -n "$VER" ] && echo "# installed on the device: webkit2gtk-6.0-$VER" >&2
fi
if [ -z "$VER" ]; then
	VER=$(ls "$DIR" 2>/dev/null | sed -n 's/^webkit2gtk-6\.0-dbg-\(.*\)\.apk$/\1/p' | sort -V | tail -1)
	echo "# device did not answer -- falling back to the newest apk, $VER. If the" >&2
	echo "# phone is running anything else, every offset below is wrong." >&2
fi
[ -n "$VER" ] || { echo "no webkit2gtk-6.0-dbg-*.apk in $DIR" >&2; exit 1; }
DBG=$DIR/webkit2gtk-6.0-dbg-$VER.apk
SO=$DIR/webkit2gtk-6.0-$VER.apk
[ -f "$DBG" ] && [ -f "$SO" ] || { echo "missing $DBG or $SO -- the device runs $VER and this host has no apk for it; build it, or install a build you have" >&2; exit 1; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
tar xzf "$SO" -C "$TMP" 2>/dev/null || true
LIB=$(find "$TMP" -name 'libwebkitgtk-6.0.so.*' ! -name '*.debug' | head -1)
[ -n "$LIB" ] || { echo "no library in $SO" >&2; exit 1; }
# The flags column is two fields ("R E"), not one, and strtonum() is a gawk
# extension -- do the hex in the shell instead of depending on either.
LOADLINE=$(readelf -lW "$LIB" | awk '/LOAD/ && / E /{ print $2, $3; exit }')
[ -n "$LOADLINE" ] || { echo "no executable LOAD segment in $LIB" >&2; exit 1; }
DELTA=$(( $(echo "$LOADLINE" | cut -d' ' -f2) - $(echo "$LOADLINE" | cut -d' ' -f1) ))
[ -n "$DELTA" ] || { echo "no executable LOAD segment in $LIB" >&2; exit 1; }

tar xzf "$DBG" -C "$TMP" 2>/dev/null || true
# Name it exactly: the -dbg package also ships JavaScriptCore's and
# WebKitWebDriver's .debug files, and `find -name '*.debug' | head -1` picks
# whichever the directory order hands over first -- which silently resolves
# every symbol against the wrong library and reports "NOT FOUND" for all of them.
SYMS=$(find "$TMP" -name 'libwebkitgtk-6.0.so.*.debug' | head -1)
[ -n "$SYMS" ] || { echo "no .debug in $DBG" >&2; exit 1; }

if [ $# -gt 0 ]; then set -- "$@"; else set -- \
	'updateRendering=WebCore::Page::updateRendering()' \
	'style=WebCore::Document::resolveStyle(WebCore::Document::ResolveStyleType)' \
	'layout=WebCore::LocalFrameViewLayoutContext::performLayout(bool)' \
	'compositing=WebCore::RenderLayerCompositor::updateCompositingLayers(WebCore::CompositingUpdateType, WebCore::RenderLayer*)' \
	'intersection=WebCore::Document::updateIntersectionObservations()' \
	'afterUpdate=WebCore::Page::doAfterUpdateRendering()'
fi

NM=$(nm -C --defined-only "$SYMS" 2>/dev/null)
out=""
for spec in "$@"; do
	name=${spec%%=*}; sym=${spec#*=}
	addr=$(printf '%s\n' "$NM" | awk -v s="$sym" '$2 ~ /^[tT]$/ { i=index($0, " " $2 " "); if (substr($0, i+3) == s) { print $1; exit } }')
	if [ -z "$addr" ]; then echo "# NOT FOUND: $sym" >&2; continue; fi
	off=$(printf '0x%x' $(( 0x$addr - DELTA )))
	echo "# $name  $sym  vaddr 0x$addr - delta $(printf '0x%x' "$DELTA") = $off" >&2
	out="$out$name=$off "
done
echo "export TK_WKPHASE_OFFSETS='${out% }'"
