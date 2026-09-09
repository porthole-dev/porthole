#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: the device BOOTED with a session up
# env: PORTHOLE_* (ph-lib.sh), TK_BLANK_URL, TK_BLANK_ENV, TK_BLANK_LABEL, TK_BLANK_DRAG
# exits: 0 the arm finished · 1 it never finished
# arm.sh [SECONDS] -- stage and run one blank-band arm. See blankarm.sh.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$HERE/../../.." || exit 1
source tools/ph-lib.sh

scp "${TK_SSH_OPTS[@]}" \
	tools/repro/a5xx-gmem/sess.sh tools/ph-webeval.py tools/ph-webvq.py tools/ph-touch.py \
	tools/ph-ui.py tools/ph-gesture-bench.py tools/ph-blankwatch.py tools/threadcpu.py \
	tools/ph-wkphase.sh tools/ph-key.py "$HERE/blankarm.sh" "$PHONE:/tmp/" >/dev/null || exit 1

# TK_BLANK_PHASE=1 adds uprobes on the main thread's rendering phases. The
# offsets are per BUILD, so they are recomputed here rather than cached.
if [ -n "${TK_BLANK_PHASE:-}" ]; then
	eval "$(tools/ph-wkoffsets.sh \
		'updateRendering=WebCore::Page::updateRendering()' \
		'style=WebCore::Document::resolveStyle(WebCore::Document::ResolveStyleType)' \
		'layout=WebCore::LocalFrameViewLayoutContext::performLayout(bool)' \
		'compositing=WebCore::RenderLayerCompositor::updateCompositingLayers(WebCore::CompositingUpdateType, WebCore::RenderLayer*)' \
		'record=WebCore::CoordinatedPlatformLayer::record(WebCore::IntRect const&)' \
		'updateIfNeeded=WebCore::CoordinatedBackingStoreProxy::updateIfNeeded(WebCore::IntRect const&, WebCore::IntRect const&, float, bool, WTF::Vector<WebCore::IntRect, 1ul, WTF::CrashOnOverflow, 16ul, WTF::FastMalloc> const&, WebCore::Damage&, WebCore::CoordinatedPlatformLayer&)' \
		'createTiles=WebCore::CoordinatedBackingStoreProxy::createOrDestroyTiles(WebCore::IntRect const&, WebCore::IntRect const&, WebCore::IntSize const&, float, int, WebCore::Damage&, WTF::Vector<unsigned int, 0ul, WTF::CrashOnOverflow, 16ul, WTF::FastMalloc>&, WTF::Vector<unsigned int, 0ul, WTF::CrashOnOverflow, 16ul, WTF::FastMalloc>&)' \
		2>/dev/null)"
	[ -n "${TK_WKPHASE_OFFSETS:-}" ] || { echo "no offsets -- is the -dbg apk for the installed build present?" >&2; exit 1; }
	echo "offsets: $TK_WKPHASE_OFFSETS"
	tk_run "printf 'export TK_WKPHASE_OFFSETS=%s\n' \"'$TK_WKPHASE_OFFSETS'\" > /tmp/wkoff.sh" >/dev/null
fi

# sess.sh is the session env every device tool sources; the arm's own knobs go
# in a SEPARATE file that blankarm.sh sources after it, so re-running an arm
# replaces them instead of appending a second copy of every export.
ENVF=$(mktemp)
for v in TK_BLANK_URL TK_BLANK_ENV TK_BLANK_LABEL TK_BLANK_DRAG TK_BLANK_PHASE TK_BLANK_VIDEO TK_BLANK_VIEWPORTS PORTHOLE_EPHY_ARGS; do
	[ -n "${!v:-}" ] && printf 'export %s=%q\n' "$v" "${!v}" >> "$ENVF"
done
scp "${TK_SSH_OPTS[@]}" "$ENVF" "$PHONE:/tmp/blankenv.sh" >/dev/null
rm -f "$ENVF"

exec tools/repro/phoc-planes/run-arm.sh /tmp/blankarm.sh BLANKARMDONE "${1:-420}"
