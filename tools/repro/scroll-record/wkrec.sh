#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: runs ON THE DEVICE. /tmp/sess.sh, /tmp/ph-wkphase.sh, /tmp/ph-scrollarm.sh
#        and the python helpers scrollarm needs, all staged in /tmp.
# env: TK_WKPHASE_OFFSETS (required)
# exits: 0 measured · 1 the arm is void
# wkrec.sh -- one settled-scroll arm with the TILE-RECORD path probed, plus the
# positive control that says whether the probes attached at all.
#
# The 2026-09-06 handoff's null ("a settled scroll fires none of the six
# rendering phases") is only a refutation if the probes were attached to THIS
# browser instance -- and a uprobe does not attach to an already-mapped library,
# so "no hits" and "never attached" print identically. The control window here
# forces a synchronous layout in the same instance after the drag: probes that
# fire there were attached, which makes the drag window's null real.
set -u
[ -f /tmp/wkoff.sh ] && . /tmp/wkoff.sh
: "${TK_WKPHASE_OFFSETS:?run tools/ph-wkoffsets.sh on the host}"
export TK_WKPHASE_OFFSETS
[ -n "${TK_SCROLL_URL:-}" ] && export TK_SCROLL_URL
. /tmp/sess.sh

for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do
	systemctl --user stop "$u" 2>/dev/null
done
pkill -x epiphany 2>/dev/null
sleep 3
pkill -f WebKitWebProcess 2>/dev/null
sleep 3
N=$(pgrep -fc WebKitWebProcess 2>/dev/null || echo 0)
echo "webprocs before arming: $N"
[ "$N" -eq 0 ] || { echo "REFUSE: a browser is still running, probes would not attach"; echo WKRECDONE; exit 1; }

sh /tmp/ph-wkphase.sh arm

# The hook runs after the page is up and before the drag: settle, then start the
# measurement detached so it spans the drag scrollarm is about to run.
rm -f /tmp/wk-drag.out
PORTHOLE_SCROLL_HOOK="sleep ${PORTHOLE_SETTLE:-20}; setsid sh -c \"sh /tmp/ph-wkphase.sh measure 16 > /tmp/wk-drag.out 2>&1; cp /tmp/wk.trace /tmp/wk-drag.trace; echo DRAGWINDOWDONE >> /tmp/wk-drag.out\" </dev/null >/dev/null 2>&1 &" \
	sh /tmp/ph-scrollarm.sh rec
RC=$?

# The drag is shorter than the window that measures it, so scrollarm returns
# while the detached measure is still sleeping -- the first run of this read an
# empty file and reported the drag window as silent. Poll for its marker.
i=0
while [ "$i" -lt 60 ]; do
	grep -q DRAGWINDOWDONE /tmp/wk-drag.out 2>/dev/null && break
	sleep 1
	i=$((i + 1))
done
echo "=== DRAG WINDOW (settled scroll) ==="
cat /tmp/wk-drag.out 2>/dev/null || echo "no drag output"

echo "=== CONTROL: forced layout in the same instance ==="
setsid sh -c "sh /tmp/ph-wkphase.sh measure 8 > /tmp/wk-ctl.out 2>&1; cp /tmp/wk.trace /tmp/wk-ctl.trace" </dev/null >/dev/null 2>&1 &
sleep 2
for _ in 1 2 3; do
	python3 /tmp/ph-webeval.py 'document.body.appendChild(document.createElement("hr")); document.body.offsetHeight' >/dev/null 2>&1
	sleep 1
done
sleep 6
cat /tmp/wk-ctl.out 2>/dev/null || echo "no control output"

sh /tmp/ph-wkphase.sh off
echo "arm exit $RC"
echo WKRECDONE
