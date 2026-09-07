#!/bin/bash
# Lockscreen handling, kept deliberately dumb because every "proper" API lied:
# logind LockedHint, org.gnome.ScreenSaver.GetActive and lswt all report
# "unlocked" while phosh's lockscreen (a layer-shell surface) is on screen, and
# greetd initial_session does not suppress it either. So: look at the screen,
# and if the "Slide up to unlock" banner is there, swipe. The PIN is disabled
# on this device, so the swipe is the whole unlock and is harmless if repeated.
SPD_STATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

is_locked() {   # 0 = locked, 1 = not locked
    tk_run ". /tmp/sess.sh; grim -g '156,900 167x30' /tmp/lockprobe.png" >/dev/null 2>&1 || return 1
    scp "${TK_SSH_OPTS[@]}" "$PHONE:/tmp/lockprobe.png" "$SPD_STATE/lockprobe.png" >/dev/null 2>&1 || return 1
    local rmse
    rmse=$(magick compare -metric RMSE "$SPD_STATE/ref_lock_small.png" "$SPD_STATE/lockprobe.png" null: 2>&1 | sed 's/.*(\(.*\))/\1/')
    [ -n "$rmse" ] || return 1
    awk -v r="$rmse" 'BEGIN{exit !(r < 0.15)}'
}

unlock_swipe() {
    local i
    for i in 1 2 3 4; do
        if ! is_locked; then echo "  not on lockscreen"; return 0; fi
        echo "  lockscreen detected, swiping up (attempt $i)"
        tk_run "sudo -n python3 /tmp/ph-touch.py swipe 720 2600 720 1000 350" >/dev/null 2>&1
        sleep 5
    done
    is_locked && { echo "  STILL on lockscreen after 4 swipes"; return 1; }
    return 0
}

ss_toplevel() {  # "app-id :: title" lines; lswt -j is NOT valid json on this build
    tk_run ". /tmp/sess.sh; lswt 2>/dev/null" \
      | awk 'NR>1 && NF {printf "%s :: ", $1; $1=""; print}' | tr -d '\r'
}

# wait_ready <title-substring> -- not on the lockscreen AND the window exists.
# Epiphany can take well over 30 s to map its toplevel here, so poll.
wait_ready() {
    local want=$1 i tl
    for i in $(seq 1 15); do
        # The session re-locks on its own (idle/blank) while the browser starts,
        # so swipe whenever we find it locked rather than only once up front.
        if is_locked; then
            tk_run "sudo -n python3 /tmp/ph-touch.py swipe 720 2600 720 1000 350" >/dev/null 2>&1
            sleep 4
        fi
        if ! is_locked; then
            tl=$(ss_toplevel)
            case "$tl" in *"$want"*) echo "  ready: $tl"; return 0 ;; esac
        fi
        sleep 5
    done
    echo "  NOT ready: locked=$(is_locked && echo yes || echo no) toplevels=$(ss_toplevel)"
    return 9
}
