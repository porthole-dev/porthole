#!/bin/bash
# scope: generic
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_USER
# exits: 0 ok · 1 failed
# Dump and decode the DPU registers that matter for a stuck display.
#
# Why this exists: /sys/kernel/debug/dri/0/kms dumps every DPU block (top, lm_*,
# sspp_*, pingpong_*, intf_*, ctl_*) as raw hex, for free, with no rebuild and
# no devmem -- but the interesting fields are scattered and unlabelled. This
# pulls out the ones that actually answer "why is nothing on screen", decoded.
#
# What each one told us during bring-up, so the numbers mean something:
#   CTL_FLUSH    non-zero long after a commit = the hardware never consumed the
#                flush, i.e. no frame started. Bits: 0-5 SSPP, 6-11 LM,
#                17 CTL, 22 DSC, 28-31 INTF_3..INTF_0.
#   CTL_TOP      BIT(17) = cmd mode; bits 4-7 = intf (enum, INTF_0 == 1, so
#                DSI0/INTF_1 reads as 2); bits 19-21 = 3D merge.
#   PP_LINE_COUNT / PP_OUT_LINE_COUNT
#                zero while a commit is in flight = the pingpong never started
#                the transfer at all, as opposed to stalling partway.
#   INTR_EN/STATUS  bits 8-11 PP done, 12-15 PP rd_ptr, 24/26/28/30 INTF.
#                A latched STATUS bit with the EN bit clear = the event is
#                firing but masked.
#   MDP_VSYNC_SEL   4-bit field per pingpong (PP0 at bit 12); 0-2 GPIO,
#                15 watchdog timer. Bits 3:0 and 31:29 are hardwired.
#
# Usage: tk-dpu-regs.sh [block ...]      (default: the useful summary)
#        tk-dpu-regs.sh raw              (the whole kms dump)
set -eu

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"

PHONE=${PHONE:-$PORTHOLE_USER@$HOST}

ping -c1 -W2 $HOST >/dev/null 2>&1 || {
    echo "phone is not on the USB network"; exit 1; }

dump=$(ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
    'sudo cat /sys/kernel/debug/dri/0/kms' 2>/dev/null)

[ -n "$dump" ] || { echo "empty dump -- is msm loaded?"; exit 1; }

if [ "${1:-}" = "raw" ]; then
    printf '%s\n' "$dump"
    exit 0
fi

block() { printf '%s\n' "$dump" | sed -n "/====$1====/,/^====/p"; }
# word N of the row whose offset label is $2, within block $1
word() {
    block "$1" | awk -v want="$2" -v idx="$3" \
        '$1 == want { print $(idx + 3) }' | head -1
}

echo "=== top ==="
echo "  INTR_EN        0x$(word top 0x10 0)   (8-11 pp_done, 12-15 rd_ptr, 24/26/28/30 intf)"
echo "  INTR_STATUS    0x$(word top 0x10 1)"
echo "  MDP_VSYNC_SEL  0x$(word top 0x410 1)   (4 bits per pp, pp0 at bit 12)"
echo "  WD_TIMER_0 CTL 0x$(word top 0x380 0)  CTL2 0x$(word top 0x380 1)  LOAD 0x$(word top 0x380 2)"

for c in ctl_0 ctl_1 ctl_2 ctl_3 ctl_4; do
    top=$(word $c 0x10 1); flush=$(word $c 0x10 2)
    # 0x1f00 is the idle/reset value -- skip CTLs that were never used
    [ "$top" = "00001f00" ] || [ -z "$top" ] && continue
    echo "=== $c (in use) ==="
    echo "  CTL_LAYER(LM_0) 0x$(word $c 0x0 0)   CTL_LAYER(LM_1) 0x$(word $c 0x0 1)"
    echo "  CTL_LAYER(LM_2) 0x$(word $c 0x0 2)"
    echo "  CTL_TOP         0x$top"
    echo "  CTL_FLUSH       0x$flush   (non-zero after a commit = flush not consumed)"
done

for pp in pingpong_0 pingpong_1 pingpong_2 pingpong_3; do
    en=$(word $pp 0x0 0)
    [ -z "$en" ] && continue
    line=$(word $pp 0x20 3); out=$(word $pp 0x20 2)
    dsc=$(word $pp 0xa0 0)
    [ "$en" = "00000000" ] && [ "$dsc" = "00000000" ] && continue
    echo "=== $pp ==="
    echo "  TEAR_CHECK_EN  0x$en    INT_COUNT_VAL 0x$(word $pp 0x10 1)"
    echo "  OUT_LINE_COUNT 0x$out    LINE_COUNT 0x$line   (0 mid-commit = never started)"
    echo "  PP_DSC_MODE    0x$dsc"
done
