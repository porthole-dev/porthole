#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: the device BOOTED; the webkit apks for the INSTALLED build in the
#        sandbox package dir (ph-wkoffsets.sh reads them)
# env: PORTHOLE_* (ph-lib.sh), TK_SCROLL_URL, PORTHOLE_SCROLL_DRAG
# exits: 0 the arm finished · 1 it never finished
# arm.sh -- WHY is layout dirty during a settled scroll? Same harness as
# repro/scroll-record, different probe set: instead of measuring the six
# rendering phases again (done, and the answer was "layout"), this probes the
# CALLERS that can dirty layout or force it on a per-scroll basis, so the tail
# can be attributed to a mechanism rather than to "the page's JavaScript".
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$HERE/../../.." || exit 1
source tools/ph-lib.sh

# The probe set is a knob: the first arm asks WHICH phase costs, the next asks
# WHO dirtied it. Same harness, different symbols -- so a follow-up question
# costs an env var, not a new script.
case "${PORTHOLE_PROBES:-why}" in
why)  SYMS='
layout=WebCore::LocalFrameViewLayoutContext::performLayout(bool)
style=WebCore::Document::resolveStyle(WebCore::Document::ResolveStyleType)
updateRendering=WebCore::Page::updateRendering()
scrollPos=WebCore::LocalFrameView::scrollPositionChanged(WebCore::IntPoint const&, WebCore::IntPoint const&)
anchorUpd=WebCore::ScrollAnchoringController::updateAnchorElement()
anchorPick=WebCore::ScrollAnchoringController::chooseAnchorElement(WebCore::Document&)
hover=WebCore::Document::updateHoverActiveState(WebCore::HitTestRequest const&, WebCore::Element*, WebCore::Document::CaptureChange)
intersect=WebCore::Document::updateIntersectionObservations()
cfgChange=WebCore::LocalFrameViewLayoutContext::setNeedsLayoutAfterViewConfigurationChange()
slowRepaint=WebCore::LocalFrameView::repaintSlowRepaintObjects()
fakeMouse=WebCore::EventHandler::dispatchFakeMouseMoveEventSoon()' ;;
invalidation) SYMS='
layout=WebCore::LocalFrameViewLayoutContext::performLayout(bool)
style=WebCore::Document::resolveStyle(WebCore::Document::ResolveStyleType)
updateRendering=WebCore::Page::updateRendering()
setFrameRect=WebCore::LocalFrameView::setFrameRect(WebCore::IntRect const&)
availSize=WebCore::LocalFrameView::availableContentSizeChanged(WebCore::ScrollableArea::AvailableSizeChangeReason)
mediaQ=WebCore::Document::evaluateMediaQueriesAndReportChanges()
vpUnits=WebCore::Document::updateViewportUnitsOnResize()
fullRebuild=WebCore::Document::scheduleFullStyleRebuild()
sheetEnv=WebCore::Style::Scope::didChangeStyleSheetEnvironment()
sheetCand=WebCore::Style::Scope::didChangeActiveStyleSheetCandidates()
activeSheets=WebCore::Style::Scope::updateActiveStyleSheets(WebCore::Style::Scope::UpdateType)' ;;
wheel) SYMS='
layout=WebCore::LocalFrameViewLayoutContext::performLayout(bool)
style=WebCore::Document::resolveStyle(WebCore::Document::ResolveStyleType)
syncWheel=WebKit::EventDispatcher::dispatchWheelEventViaMainThread(WTF::ObjectIdentifierGeneric<WebCore::PageIdentifierType, WTF::ObjectIdentifierMainThreadAccessTraits<unsigned long>, unsigned long>, WebKit::WebWheelEvent const&, WTF::OptionSet<WebCore::WheelEventProcessingSteps, (WTF::ConcurrencyTag)0>, WebKit::EventDispatcher::WheelEventOrigin)
waitMain=WebCore::ThreadedScrollingTree::waitForEventToBeProcessedByMainThread(WebCore::PlatformWheelEvent const&)
wheelInternal=WebCore::EventHandler::handleWheelEventInternal(WebCore::PlatformWheelEvent const&, WTF::OptionSet<WebCore::WheelEventProcessingSteps, (WTF::ConcurrencyTag)0>, WTF::OptionSet<WebCore::EventHandling, (WTF::ConcurrencyTag)0>&)
hitTest=WebCore::Document::hitTest(WebCore::HitTestRequest const&, WebCore::HitTestLocation const&, WebCore::HitTestResult&)
vpNeedLayout=WebCore::LocalFrameView::setViewportConstrainedObjectsNeedLayout()
updLayoutVp=WebCore::LocalFrameView::updateLayoutViewport()
reconcile=WebCore::AsyncScrollingCoordinator::reconcileScrollingState(WebCore::LocalFrameView&, WebCore::FloatPoint const&, mpark::variant<std::optional<WebCore::FloatPoint>, std::optional<WebCore::FloatRect> > const&, WebCore::ScrollType, WebCore::ViewportRectStability, WebCore::ScrollingLayerPositionAction)
compositing=WebCore::RenderLayerCompositor::updateCompositingLayers(WebCore::CompositingUpdateType, WebCore::RenderLayer*)
fastPath=WebCore::LocalFrameView::scrollContentsFastPath(WebCore::IntSize const&, WebCore::IntRect const&, WebCore::IntRect const&)' ;;
*) SYMS=$PORTHOLE_PROBES ;;
esac

OLDIFS=$IFS; IFS=$'\n'
# shellcheck disable=SC2046  # word splitting is the point: IFS is a
# newline here, and one C++ signature -- spaces and all -- per line.
set -- $(printf '%s' "$SYMS" | grep .)
IFS=$OLDIFS
eval "$(tools/ph-wkoffsets.sh "$@" 2>/dev/null)"
[ -n "${TK_WKPHASE_OFFSETS:-}" ] || { echo "no offsets -- is the -dbg apk for the installed build present?" >&2; exit 1; }
echo "offsets: $TK_WKPHASE_OFFSETS"

scp "${TK_SSH_OPTS[@]}" \
	tools/repro/a5xx-gmem/sess.sh tools/ph-webeval.py tools/ph-webvq.py tools/ph-touch.py tools/ph-ui.py \
	tools/ph-gesture-bench.py tools/threadcpu.py tools/ph-scrollarm.sh tools/ph-wkphase.sh \
	tools/repro/scroll-record/wkrec.sh "$PHONE:/tmp/" >/dev/null || exit 1
tk_run "printf 'export TK_WKPHASE_OFFSETS=%s\n' \"'$TK_WKPHASE_OFFSETS'\" > /tmp/wkoff.sh" >/dev/null
for v in TK_SCROLL_URL PORTHOLE_SCROLL_DRAG PORTHOLE_SETTLE PORTHOLE_EPHY_ARGS; do
	eval "val=\${$v:-}"
	[ -n "$val" ] && tk_run "printf 'export %s=%s\n' '$v' \"'$val'\" >> /tmp/wkoff.sh" >/dev/null
done

exec tools/repro/phoc-planes/run-arm.sh /tmp/wkrec.sh WKRECDONE "${1:-420}"
