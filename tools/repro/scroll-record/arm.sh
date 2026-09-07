#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: the device BOOTED; the webkit apks for the INSTALLED build in the
#        sandbox package dir (ph-wkoffsets.sh reads them)
# env: PORTHOLE_* (ph-lib.sh), PORTHOLE_WK_VERSION (default: the newest -dbg apk)
# exits: 0 the arm finished · 1 it never finished
# arm.sh -- stage and run one settled-scroll arm with the tile-record path
# probed. Prints both windows and leaves the raw traces in /tmp on the device.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$HERE/../../.." || exit 1
source tools/ph-lib.sh

eval "$(tools/ph-wkoffsets.sh \
	'updateRendering=WebCore::Page::updateRendering()' \
	'style=WebCore::Document::resolveStyle(WebCore::Document::ResolveStyleType)' \
	'layout=WebCore::LocalFrameViewLayoutContext::performLayout(bool)' \
	'compositing=WebCore::RenderLayerCompositor::updateCompositingLayers(WebCore::CompositingUpdateType, WebCore::RenderLayer*)' \
	'record=WebCore::CoordinatedPlatformLayer::record(WebCore::IntRect const&)' \
	'paint=WebCore::CoordinatedPlatformLayer::paint(WebCore::IntRect const&)' \
	'updateIfNeeded=WebCore::CoordinatedBackingStoreProxy::updateIfNeeded(WebCore::IntRect const&, WebCore::IntRect const&, float, bool, WTF::Vector<WebCore::IntRect, 1ul, WTF::CrashOnOverflow, 16ul, WTF::FastMalloc> const&, WebCore::Damage&, WebCore::CoordinatedPlatformLayer&)' \
	'flushCompos=WebCore::CoordinatedPlatformLayer::flushCompositingState(WTF::OptionSet<WebCore::CompositionReason, (WTF::ConcurrencyTag)0> const&)' \
	'glcFlush=WebCore::GraphicsLayerCoordinated::flushCompositingState(WebCore::FloatRect const&)' \
	2>/dev/null)"
[ -n "${TK_WKPHASE_OFFSETS:-}" ] || { echo "no offsets -- is the -dbg apk for the installed build present?" >&2; exit 1; }
echo "offsets: $TK_WKPHASE_OFFSETS"

scp "${TK_SSH_OPTS[@]}" \
	tools/repro/a5xx-gmem/sess.sh tools/ph-webeval.py tools/ph-webvq.py tools/ph-touch.py tools/ph-ui.py \
	tools/ph-gesture-bench.py tools/threadcpu.py tools/ph-scrollarm.sh tools/ph-wkphase.sh \
	"$HERE/wkrec.sh" "$PHONE:/tmp/" >/dev/null || exit 1
tk_run "printf 'export TK_WKPHASE_OFFSETS=%s\n' \"'$TK_WKPHASE_OFFSETS'\" > /tmp/wkoff.sh" >/dev/null
# The page under test travels with the offsets: the same probe set on a static
# page with no images and no script is the control that says whether the layouts
# below are WebKit's or Wikipedia's.
[ -n "${TK_SCROLL_URL:-}" ] && tk_run "printf 'export TK_SCROLL_URL=%s\n' \"'$TK_SCROLL_URL'\" >> /tmp/wkoff.sh" >/dev/null

exec tools/repro/phoc-planes/run-arm.sh /tmp/wkrec.sh WKRECDONE "${1:-420}"
