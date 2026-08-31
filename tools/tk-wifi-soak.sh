#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (run it on the device as root, under systemd-run)
# env: TK_WIFI_SOAK_LOG, TK_WIFI_SOAK_INTERVAL
# exits: 0 ok · non-zero on failure
# tk-wifi-soak.sh -- watch a WiFi link for the two ways it fails quietly.
#
# tools/tk-soak.sh is the general stability soak: uptime, load, memory, thermal.
# It says nothing about the radio, and the two WiFi defects on this port are
# both invisible to it:
#
#   1. "connected but dead" -- the link stays UP with an address and a default
#      route, nmcli says connected, and NOTHING passes in either direction.
#      Every status field lies, so the only witness is an actual probe.
#   2. associations that never get keys -- the driver associates happily and
#      the 4-way handshake never completes, so the count of associations looks
#      HEALTHY during the failure. The ratio against key negotiations is the
#      signal, never the count.
#
# So this records, every interval: whether we are associated and to what, and
# -- separately -- whether a packet actually makes it to the gateway and back.
# Those two disagreeing IS defect 1, and that disagreement is the whole point.
#
# Counters are cumulative since the soak started, read from the journal rather
# than kept in memory, so a restart of this script does not lose them.
#
# ponytail: ping over any richer data path. If a gateway ever stops answering
# ICMP the upgrade is a TCP connect to the gateway, not a bigger framework.
set -u

LOG=${TK_WIFI_SOAK_LOG:-/var/log/tk-wifi-soak.jsonl}
INTERVAL=${TK_WIFI_SOAK_INTERVAL:-60}
IF=${TK_WIFI_SOAK_IF:-wlan0}
START=$(date '+%Y-%m-%d %H:%M:%S')

# A boot record first, so a reboot is distinguishable from a truncated log --
# same reasoning as tk-soak.sh.
printf '{"ev":"start","boot_id":"%s","if":"%s","interval":%s,"t":"%s"}\n' \
    "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" \
    "$IF" "$INTERVAL" "$START" >> "$LOG"
sync

while :; do
    NOW=$(date '+%Y-%m-%dT%H:%M:%S%z')

    # --- what every layer CLAIMS -------------------------------------------
    OPER=$(cat "/sys/class/net/$IF/operstate" 2>/dev/null || echo absent)
    LINK=$(iw dev "$IF" link 2>/dev/null)
    if echo "$LINK" | grep -q "^Connected to"; then
        BSSID=$(echo "$LINK" | sed -n 's/^Connected to \([0-9a-f:]*\).*/\1/p')
        FREQ=$(echo "$LINK"   | sed -n 's/.*freq: *\([0-9]*\).*/\1/p' | head -1)
        SIG=$(echo "$LINK"    | sed -n 's/.*signal: *\(-*[0-9]*\).*/\1/p' | head -1)
        ASSOC=1
    else
        BSSID=""; FREQ=0; SIG=0; ASSOC=0
    fi
    GW=$(ip route show default dev "$IF" 2>/dev/null | awk '{print $3; exit}')

    # --- what is actually TRUE ---------------------------------------------
    # 3 packets, 1 s each: enough to tell "dead" from "one lost packet", cheap
    # enough to run every minute for days.
    LOSS=100
    if [ -n "$GW" ]; then
        LOSS=$(ping -c 3 -W 1 "$GW" 2>/dev/null \
               | sed -n 's/.*, \([0-9]*\)% packet loss.*/\1/p')
        [ -n "$LOSS" ] || LOSS=100
    fi

    # A link that claims to be up while nothing reaches the gateway is the
    # defect, not a bad sample. Name it in the record so a reader does not have
    # to re-derive it, and shout it into kmsg where pstore may catch it.
    DEAD=0
    if [ "$ASSOC" = 1 ] && [ -n "$GW" ] && [ "$LOSS" = 100 ]; then
        DEAD=1
        echo "tk-wifi-soak CONNECTED-BUT-DEAD bssid=$BSSID freq=$FREQ sig=$SIG" \
            > /dev/kmsg 2>/dev/null
    fi

    # --- the ratio that the counts hide ------------------------------------
    A=$(journalctl -k --since "$START" --no-pager 2>/dev/null \
        | grep -c "$IF: associated")
    K=$(journalctl --since "$START" --no-pager 2>/dev/null \
        | grep -ci "Key negotiation completed")
    D=$(journalctl --since "$START" --no-pager 2>/dev/null \
        | grep -c "reason=4 locally_generated=1")

    printf '{"ev":"hb","assoc":%s,"bssid":"%s","freq":%s,"signal":%s,"gw":"%s","loss":%s,"dead":%s,"n_assoc":%s,"n_key":%s,"n_disc4":%s,"oper":"%s","t":"%s"}\n' \
        "$ASSOC" "$BSSID" "${FREQ:-0}" "${SIG:-0}" "${GW:-}" "$LOSS" "$DEAD" \
        "$A" "$K" "$D" "$OPER" "$NOW" >> "$LOG"
    sync

    echo "tk-wifi-soak assoc=$ASSOC loss=$LOSS dead=$DEAD n_assoc=$A n_key=$K" \
        > /dev/kmsg 2>/dev/null

    sleep "$INTERVAL"
done
