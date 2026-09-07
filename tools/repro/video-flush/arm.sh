#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: the device BOOTED; the webkit apks for the INSTALLED build in the
#        sandbox package dir. Pin PORTHOLE_WK_VERSION while a newer build exists but
#        is not installed yet -- ph-wkoffsets.sh picks the NEWEST -dbg apk, and
#        offsets from a build the device is not running measure random
#        instructions.
# env: PORTHOLE_* (ph-lib.sh), PORTHOLE_PROBES, PORTHOLE_VID_* (see vidrec.sh)
# exits: 0 the arm finished · 1 it never finished
# arm.sh -- one steady-state YouTube playback arm with the flush path probed.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$HERE/../../.." || exit 1
source tools/ph-lib.sh

case "${PORTHOLE_PROBES:-flush}" in
flush) SYMS='
flushBuf=WebCore::MediaPlayerPrivateGStreamer::flushCurrentBuffer()
copyBuf=WebCore::CoordinatedPlatformLayerBufferVideo::copyBuffer() const
replaceCopy=WebCore::CoordinatedPlatformLayer::replaceCurrentContentsBufferWithCopy()
capsChange=WebCore::MediaPlayerPrivateGStreamer::updateVideoSizeAndOrientationFromCaps(_GstCaps const*)
reenqueue=WebCore::SourceBufferPrivate::reenqueueMediaForTime(WebCore::TrackBuffer&, unsigned long, WTF::MediaTime const&, WebCore::SourceBufferPrivate::NeedsFlush)
triggerRepaint=WebCore::MediaPlayerPrivateGStreamer::triggerRepaint(WTF::GRefPtr<_GstSample, WTF::GRefPtrDefaultRefDerefTraits<_GstSample> >&&)' ;;
phases) SYMS='
updateRendering=WebCore::Page::updateRendering()
style=WebCore::Document::resolveStyle(WebCore::Document::ResolveStyleType)
layout=WebCore::LocalFrameViewLayoutContext::performLayout(bool)
triggerRepaint=WebCore::MediaPlayerPrivateGStreamer::triggerRepaint(WTF::GRefPtr<_GstSample, WTF::GRefPtrDefaultRefDerefTraits<_GstSample> >&&)
flushBuf=WebCore::MediaPlayerPrivateGStreamer::flushCurrentBuffer()
record=WebCore::CoordinatedPlatformLayer::record(WebCore::IntRect const&)' ;;
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
	tools/threadcpu.py tools/ph-wkphase.sh "$HERE/vidrec.sh" "$PHONE:/tmp/" >/dev/null || exit 1
tk_run "printf 'export TK_WKPHASE_OFFSETS=%s\n' \"'$TK_WKPHASE_OFFSETS'\" > /tmp/wkoff.sh" >/dev/null
for v in PORTHOLE_VID_URL PORTHOLE_VID_LIMIT PORTHOLE_VID_QUALITY PORTHOLE_VID_WINDOW PORTHOLE_VID_ENV; do
	eval "val=\${$v:-}"
	[ -n "$val" ] && tk_run "printf 'export %s=%s\n' '$v' \"'$val'\" >> /tmp/wkoff.sh" >/dev/null
done

exec tools/repro/phoc-planes/run-arm.sh /tmp/vidrec.sh VIDRECDONE "${1:-420}"
