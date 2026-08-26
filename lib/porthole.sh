#!/bin/bash
# SPDX-License-Identifier: MIT
# porthole config resolution and device helpers for the shell tools.
# SOURCE this, don't run it.
#
# This is the generic form of taimen's tools/tk-lib.sh. The tk_* names are
# FROZEN as the compatibility surface -- every taimen script and every command
# line in its docs calls them, so they keep their names and their semantics
# forever. New helpers are named ph_*.
#
# Everything here exists to replace fixed `sleep`s with polling. Both probes we
# have are cheap -- `fastboot devices` and an ssh round trip -- so there is no
# excuse for waiting a fixed 30s "just in case". A reboot that is done in 18s
# should return in 18s.
#
# Config resolution, lowest precedence to highest:
#   1. the defaults below
#   2. profiles/$PORTHOLE_DEVICE/device.env      (device facts, committed)
#   3. ${XDG_CONFIG_HOME:-~/.config}/porthole/config.env  (your identity)
#   4. $PORTHOLE_ROOT/.env                        (per-checkout override)
#   5. the process environment                    (always wins)
#
# Legacy knobs are honoured and WIN over their porthole-namespaced twins:
#   PHONE     ssh target       (else composed from PORTHOLE_USER@PORTHOLE_HOST)
#   HOST      bare IP          (else TK_HOST, else the host part of PHONE)
#   TK_HOST   bare IP
#   FASTBOOT  fastboot binary path
#   TK_POLL   poll interval seconds
#   TK_FORCE  1 = `reboot -f`, skipping service shutdown
#   TK_AGENT  who holds the device lock
#   TK_DEVICE_{LOCK,TIMEOUT,MAX,STATE}
#
# Device facts live in the profile, not here. A tool that hardcodes an IP or a
# slot letter is a tool that only works on one desk.

# ------------------------------------------------------------------- roots ----

# Walk up from THIS FILE, not the cwd: tools get invoked from a kernel tree, a
# pmaports checkout, an agent's scratch dir. The cwd tells us nothing.
if [ -z "${PORTHOLE_ROOT:-}" ]; then
    _ph_self=${BASH_SOURCE[0]}
    # Resolve the symlink: tools/tk-lib.sh points here, and a tool that sources
    # it must still find profiles/ relative to the real file.
    while [ -L "$_ph_self" ]; do
        _ph_link=$(readlink "$_ph_self")
        case $_ph_link in
            /*) _ph_self=$_ph_link ;;
            *)  _ph_self=$(dirname "$_ph_self")/$_ph_link ;;
        esac
    done
    PORTHOLE_ROOT=$(cd "$(dirname "$_ph_self")/.." && pwd)
    unset _ph_self _ph_link
fi
export PORTHOLE_ROOT

PORTHOLE_RUNDIR=${PORTHOLE_RUNDIR:-$PORTHOLE_ROOT/.run}

# ph_load_env FILE -- export every KEY=value it defines, without letting the
# file execute anything. `set -a; . file` would run arbitrary shell; these are
# config files a stranger may have written, so we parse instead of source.
ph_load_env() {
    [ -r "$1" ] || return 0
    local line key value
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line#"${line%%[![:space:]]*}"}          # ltrim
        case $line in ''|'#'*) continue ;; esac
        case $line in 'export '*) line=${line#export } ;; esac
        case $line in *=*) ;; *) continue ;; esac
        key=${line%%=*}
        value=${line#*=}
        key=${key%"${key##*[![:space:]]}"}             # rtrim key
        case $key in [!A-Za-z_]*|'') continue ;; esac
        value=${value#"${value%%[![:space:]]*}"}       # ltrim value
        # A quoted value ends at its closing quote; anything after it is a
        # comment. Matching \"*\" instead would fail on `KEY="a"  # why` and
        # keep the quotes -- and profiles/ is full of documented values, so
        # that is the common case, not the edge case.
        case $value in
            \"*) value=${value#\"}; value=${value%%\"*} ;;
            \'*) value=${value#\'}; value=${value%%\'*} ;;
            *)   value=${value%% #*}                   # strip inline comment
                 value=${value%"${value##*[![:space:]]}"} ;;
        esac
        # Only set if not already in the environment: the caller's env is the
        # highest layer and must never be overwritten by a file.
        [ -n "${!key+x}" ] || printf -v "$key" '%s' "$value"
        export "${key?}"
    done < "$1"
}

# ---------------------------------------------------------------- layering ----
# ph_load_env never overwrites a key that is already set, so files are loaded
# HIGHEST precedence first: the process environment is already set and wins
# outright, then per-checkout .env, then your config.env, then the profile.
#
# The built-in defaults are applied LAST, below, for whatever is still unset.
# Applying them up here instead would make them beat the profile -- which is
# exactly backwards, and cost four failing tests to notice.

if [ -n "${PORTHOLE_DEVICE:-}" ]; then
    _ph_profile=$PORTHOLE_ROOT/profiles/$PORTHOLE_DEVICE/device.env
    if [ ! -r "$_ph_profile" ]; then
        echo "porthole: no profile for device '$PORTHOLE_DEVICE'" >&2
        echo "porthole: expected $_ph_profile" >&2
        # A glob rather than `ls | grep`: a filename with a newline or a space
        # in it would split wrongly, and shellcheck is right to object.
        _ph_known=
        for _ph_d in "$PORTHOLE_ROOT"/profiles/*/; do
            _ph_d=${_ph_d%/}; _ph_d=${_ph_d##*/}
            case $_ph_d in _*|'*') continue ;; esac
            _ph_known="$_ph_known $_ph_d"
        done
        echo "porthole: known devices:$_ph_known" >&2
        unset _ph_known _ph_d
        return 1 2>/dev/null || exit 1
    fi
fi
ph_load_env "$PORTHOLE_ROOT/.env"
ph_load_env "${XDG_CONFIG_HOME:-$HOME/.config}/porthole/config.env"
# The device may only have become known from the files above.
[ -n "${PORTHOLE_DEVICE:-}" ] && \
    ph_load_env "$PORTHOLE_ROOT/profiles/$PORTHOLE_DEVICE/device.env"
unset _ph_profile

# ---------------------------------------------------------------- defaults ----
# The lowest layer, applied last to whatever no file supplied. Anything here
# must be safe on a device nobody has described yet -- device facts belong in a
# profile. Note HAS_AB_SLOTS defaults to 0: wrongly assuming slots means
# flashing a partition that does not exist.

: "${PORTHOLE_USER:=user}"
: "${PORTHOLE_HOST:=172.16.42.1}"
: "${PORTHOLE_SSH_PORT:=22}"
: "${PORTHOLE_POLL:=0.5}"
: "${PORTHOLE_CONNECT_TIMEOUT:=2}"
: "${PORTHOLE_NO_MUX:=0}"
: "${PORTHOLE_MUX_PERSIST:=60s}"
: "${PORTHOLE_HAS_AB_SLOTS:=0}"
: "${PORTHOLE_REBOOT_BUDGET_S:=120}"
: "${PORTHOLE_FASTBOOT_BUDGET_S:=60}"

# The kernel source directory for an arch is not the arch name: a package says
# aarch64, the tree says arch/arm64. Deriving it here keeps every build script
# from hardcoding one device's answer.
case ${PORTHOLE_ARCH:-aarch64} in
    aarch64) PORTHOLE_ARCH_DIR=arm64 ;;
    armv7|armhf|armv7l) PORTHOLE_ARCH_DIR=arm ;;
    x86_64|x86) PORTHOLE_ARCH_DIR=x86 ;;
    riscv64) PORTHOLE_ARCH_DIR=riscv ;;
    *) PORTHOLE_ARCH_DIR=$PORTHOLE_ARCH ;;
esac
export PORTHOLE_ARCH_DIR

# ------------------------------------------------- the compatibility surface --

# HOST: the legacy names win, then the host part of PHONE (tk-stream.sh does
# `HOST=${PHONE#*@}`, so someone who set only PHONE still needs a pingable
# address), then the profile.
if [ -z "${HOST:-}" ]; then
    if [ -n "${TK_HOST:-}" ]; then
        HOST=$TK_HOST
    elif [ -n "${PHONE:-}" ]; then
        HOST=${PHONE##*@}
    else
        HOST=$PORTHOLE_HOST
    fi
fi
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
FASTBOOT=${FASTBOOT:-${PORTHOLE_FASTBOOT:-fastboot}}
TK_POLL=${TK_POLL:-$PORTHOLE_POLL}
TK_AGENT=${TK_AGENT:-${PORTHOLE_AGENT:-unknown}}
export PHONE HOST FASTBOOT TK_POLL TK_AGENT

# ConnectTimeout keeps a probe against a vanished USB interface from stalling
# the poll loop; when the device is down the connect fails instantly anyway.
#
# StrictHostKeyChecking=no plus a /dev/null known-hosts file is MANDATORY, not
# laziness: host keys change on essentially every boot, and with BatchMode a
# real known_hosts turns every tool into an outright failure.
TK_SSH_OPTS=(-o "ConnectTimeout=$PORTHOLE_CONNECT_TIMEOUT"
             -o StrictHostKeyChecking=no
             -o UserKnownHostsFile=/dev/null
             -o LogLevel=ERROR
             -o BatchMode=yes)

[ "$PORTHOLE_SSH_PORT" != "22" ] && TK_SSH_OPTS+=(-p "$PORTHOLE_SSH_PORT")
[ -n "${PORTHOLE_SSH_KEY:-}" ] && \
    TK_SSH_OPTS+=(-i "${PORTHOLE_SSH_KEY/#\~/$HOME}" -o IdentitiesOnly=yes)

# Connection multiplexing. THE single biggest speed win in the toolbox: without
# it every probe pays a full handshake (~200ms), with a warm master a round
# trip is ~15ms, and the tools run in tight loops.
#
# It is only safe because every reboot path calls ph_ssh_mux_reset FIRST. Host
# keys change on essentially every boot, so a master socket that outlives a
# reboot is a live handle to a dead sshd: the next command inherits the dead
# channel and hangs until ControlPersist expires instead of failing fast.
#
# ControlPersist is deliberately short, not `yes`: a stale socket should die on
# its own well inside a session. PORTHOLE_NO_MUX=1 disables the whole thing,
# which is the first thing to try when diagnosing a weird hang.
if [ "$PORTHOLE_NO_MUX" != "1" ]; then
    mkdir -p "$PORTHOLE_RUNDIR" 2>/dev/null
    TK_SSH_OPTS+=(-o ControlMaster=auto
                  # %C hashes host+port+user, so two profiles or two developers
                  # on one machine never share a socket.
                  -o "ControlPath=$PORTHOLE_RUNDIR/ssh-%C"
                  -o "ControlPersist=$PORTHOLE_MUX_PERSIST")
fi

ph_ssh_mux_reset() {
    [ "$PORTHOLE_NO_MUX" = "1" ] && return 0
    ssh "${TK_SSH_OPTS[@]}" -O exit "$PHONE" >/dev/null 2>&1
    return 0
}

# ---------------------------------------------------------------- timing ----

tk_now_ms() { date +%s%3N; }

# tk_since START_MS -> "12.4" (seconds, one decimal)
tk_since() {
    local d=$(( $(tk_now_ms) - $1 ))
    printf '%d.%d' $(( d / 1000 )) $(( (d % 1000) / 100 ))
}

# tk_deadline_ms SECONDS -> absolute epoch-ms deadline
tk_deadline_ms() { echo $(( $(tk_now_ms) + $1 * 1000 )); }

tk_expired() { [ "$(tk_now_ms)" -ge "$1" ]; }

# ph_timed LABEL START_MS -- emit a probe timing when PORTHOLE_TIMING=1.
# This is how "blazing fast" stays a measurement instead of a claim.
ph_timed() {
    [ "${PORTHOLE_TIMING:-0}" = "1" ] || return 0
    echo "[porthole] $1 $(( $(tk_now_ms) - $2 ))ms" >&2
}

# --------------------------------------------------------------- probes ----

# True only in the real bootloader.
#
# On a device where lsusb mislabels the running pmOS gadget (taimen:
# 18d1:d001, PORTHOLE_USB_LIES_AS_FASTBOOT=1) this is the ONLY reliable
# discriminator -- USB IDs cannot tell a booted device from a bootloader.
tk_in_fastboot() {
    local t; t=$(tk_now_ms)
    local out; out=$(timeout 5 "$FASTBOOT" devices 2>/dev/null)
    ph_timed "fastboot devices" "$t"
    [ -n "$out" ]
}

# Echo the running kernel's boot_id, or nothing (non-zero) if unreachable.
#
# IT RETRIES, AND THAT IS LOAD-BEARING. A single timed-out read returns empty,
# and an empty boot_id compares unequal to every real one -- so a caller that
# takes its BASELINE through one transient failure sees "the device rebooted"
# on the very next read, forever. Empty must mean "unknown", never "changed".
#
# Paid for on taimen 2026-08-19: sshd was taking ~7.9s to answer a trivial
# command (the FROZEN/PAM stall), the old 6s timeout ate the baseline, and a
# 20-cycle camera test printed "BOOT_ID CHANGED" and FAIL against a phone that
# had not rebooted. The timeout has to sit ABOVE that stall or retrying buys
# nothing -- two attempts at 12s, not three at 6s.
#
# Cost when the device is genuinely gone is still ~nothing: with the USB
# interface down ssh fails instantly, so the second attempt is free. The 24s
# worst case is only spent on a device that accepts the connection and then
# stalls -- exactly the case worth waiting for.
tk_boot_id() {
    local id t
    for _ in 1 2; do
        t=$(tk_now_ms)
        id=$(timeout 12 ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
                 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null </dev/null)
        ph_timed "boot_id" "$t"
        [ -n "$id" ] && { echo "$id"; return 0; }
    done
    return 1
}

tk_uptime() {
    timeout 6 ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
        'cut -d. -f1 /proc/uptime' 2>/dev/null </dev/null
}

# tk_pkill NAME [NAME...] -- kill processes on the DEVICE, by exact name.
#
# `pkill -f <pattern>` over ssh matches YOUR OWN command line: the ssh invocation
# carries the pattern, so the pattern matches it, and the shell running it dies
# mid-command. That killed a working ssh session twice in one session, and once
# on 2026-08-20 with `pkill -f xdg-permission-store`.
# brain/traps/a-journal-grep-matches-your-own-command-line.md names the trap;
# this is the fix, because a note cannot stop you typing the short form.
#
# -x matches the executable name only, never the command line, so it cannot
# match the ssh that is asking. If you genuinely need a pattern, kill by
# recorded PID instead -- every tool here that kills does that.
#
# Prints what it killed. Returns 0 even when nothing matched: "it was not
# running" is the desired end state, not a failure.
tk_pkill() {
    [ $# -gt 0 ] || { echo "tk_pkill: name required" >&2; return 64; }
    local n
    for n in "$@"; do
        case "$n" in
        -*) echo "tk_pkill: refusing the flag '$n' -- names only." >&2
            echo "  -f matches this ssh command line and kills the session." >&2
            return 64 ;;
        esac
    done
    local remote=""
    for n in "$@"; do
        remote="$remote if pgrep -x $(printf '%q' "$n") >/dev/null 2>&1; then"
        remote="$remote sudo -n pkill -x $(printf '%q' "$n") 2>/dev/null;"
        remote="$remote echo killed $n; fi;"
    done
    timeout 15 ssh "${TK_SSH_OPTS[@]}" "$PHONE" "$remote" 2>/dev/null </dev/null
    return 0
}

# tk_device_state -> FASTBOOT | BOOTED | FROZEN | ABSENT
#
# The device lock says WHO is using the device, never WHAT the device is doing.
# On taimen 2026-08-19 an agent queued ten minutes against a phone another
# agent had left in the bootloader. This is the probe that answers the second
# question.
#
# Order is forced by the hardware: a device in the bootloader has no USB
# network at all, so fastboot is asked first. ssh distinguishes BOOTED; ping
# alone distinguishes FROZEN (kernel alive, userspace gone) from ABSENT (needs
# a human). Do NOT parallelise these -- the order IS the semantics.
#
# ~0.4s on a healthy booted device cold, under 0.1s with a warm ssh master.
# Set TK_DEVICE_STATE to skip the probe when you already know the answer.
tk_device_state() {
    [ -n "${TK_DEVICE_STATE:-}" ] && { echo "$TK_DEVICE_STATE"; return 0; }
    tk_in_fastboot && { echo FASTBOOT; return 0; }
    [ -n "$(tk_boot_id)" ] && { echo BOOTED; return 0; }
    ping -c1 -W2 "$HOST" >/dev/null 2>&1 && { echo FROZEN; return 0; }
    echo ABSENT
}

# --------------------------------------------------------------- actions ----

# Ask the device to reboot. Target is "" (normal) or "bootloader".
# The reboot is detached on the far side so sshd going away mid-command doesn't
# leave us blocked; a non-zero exit here is normal and NOT an error.
#
# TK_FORCE=1 skips service shutdown (`reboot -f`), which on taimen turned a
# ~45s cycle into ~30s. Opt-in because it bypasses service shutdown; we always
# `sync` first so the journal is consistent, but nothing gets to flush state
# the polite way. Force is deliberately NOT applied to the bootloader path:
# reaching the bootloader needs the reboot(2) command string to survive, and
# that is exactly what the force path is least trustworthy about.
tk_request_reboot() {
    local target=${1:-} cmd
    ph_ssh_mux_reset          # the socket must not outlive the sshd
    if [ "${TK_FORCE:-0}" = "1" ] && [ -z "$target" ]; then
        cmd='sudo -n sync; (sudo -n reboot -f >/dev/null 2>&1 &); exit 0'
    else
        cmd="sudo -n sync; (sudo -n reboot ${target} >/dev/null 2>&1 &); exit 0"
    fi
    timeout 12 ssh "${TK_SSH_OPTS[@]}" "$PHONE" "$cmd" >/dev/null 2>&1 </dev/null
    ph_ssh_mux_reset
    return 0
}

# The kernel's own reset path, below systemd, below busybox, below anything that
# can swallow a request. Syncs twice first because nothing else is going to.
#
# sysrq is a bitmask and is usually NOT set to allow reboot (taimen ships 16,
# sync only), so the allow-all bit has to be written before the trigger.
tk_request_sysrq_reboot() {
    ph_ssh_mux_reset
    timeout 12 ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
        'sudo -n sh -c "sync; sync; echo 1 > /proc/sys/kernel/sysrq; \
                        echo b > /proc/sysrq-trigger"' \
        >/dev/null 2>&1 </dev/null
    ph_ssh_mux_reset
    return 0
}

# ESCALATE, never repeat. Paid for on taimen 2026-08-25: `systemctl reboot`
# returned 0, `systemctl is-system-running` said `running`, `list-jobs` was
# empty and the device stayed up -- so re-issuing the identical request every
# 25s did nothing except burn the entire timeout, three times over. Each retry
# now drops a level instead: systemd, then reboot -f, then sysrq.
#
# Pass this to tk_wait_ssh as the reissue function.
TK_REBOOT_LEVEL=0
tk_reboot_escalate() {
    TK_REBOOT_LEVEL=$((TK_REBOOT_LEVEL + 1))
    case $TK_REBOOT_LEVEL in
        1)  echo ">> reboot not taken -- escalating to reboot -f" >&2
            TK_FORCE=1 tk_request_reboot ;;
        *)  echo ">> still up -- escalating to a sysrq reset" >&2
            tk_request_sysrq_reboot ;;
    esac
}

# Ask the device to reboot INTO THE BOOTLOADER, the way `adb reboot bootloader`
# does it. On taimen: lands in fastboot in ~9s, first try.
#
# `sudo -n reboot bootloader` does NOT do this where /usr/sbin/reboot is
# busybox, whose reboot applet is `reboot [-d DELAY] [-nf]` -- it takes no mode
# argument at all, so the word "bootloader" is silently discarded and you get a
# plain reboot. Every apparent success of `reboot bootloader` on taimen was
# actually the A/B retry counter running out on its own.
#
# The kernel side is usually already there: a PMIC pon node with
# mode-bootloader and a driver bound to it means the reboot-mode framework will
# write the mode to the register the bootloader reads. It just needs userspace
# to pass the mode string, i.e. reboot(2) with LINUX_REBOOT_CMD_RESTART2 rather
# than the plain RB_AUTOBOOT busybox issues. Nothing in a pmOS rootfs does
# that, so we make the syscall ourselves via python3.
#
#   142        = __NR_reboot on arm64 (generic syscall table)
#   0xfee1dead = LINUX_REBOOT_MAGIC1
#   0x28121969 = LINUX_REBOOT_MAGIC2
#   0xa1b2c3d4 = LINUX_REBOOT_CMD_RESTART2  (the one that carries a string)
#
# Note this does NOT burn a boot retry: the bootloader stops on purpose rather
# than because the counter hit 0, so the slot keeps whatever retries it had.
tk_request_bootloader() {
    if [ "${PORTHOLE_REBOOT_MODE_VIA_SYSCALL:-0}" != "1" ]; then
        tk_request_reboot bootloader
        return 0
    fi
    local py
    py="import ctypes;l=ctypes.CDLL(None);s=l.syscall;s.restype=ctypes.c_long;"
    py="${py}s.argtypes=[ctypes.c_long,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint,ctypes.c_char_p];"
    py="${py}s(142,0xfee1dead,0x28121969,0xa1b2c3d4,b\"bootloader\")"
    ph_ssh_mux_reset
    # Foreground, not backgrounded. Detaching it with (... &) and exiting the
    # shell races: ssh tears the session down before the child reaches the
    # syscall often enough that the request is simply lost, which the caller
    # then reads as "the PMIC mode was not honoured" and falls back to burning
    # boot retries for three minutes. Run it inline and let the connection die
    # mid-call -- that death IS the success signal.
    timeout 25 ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
        "sudo -n sync; sudo -n python3 -c '$py'" >/dev/null 2>&1 </dev/null
    ph_ssh_mux_reset
    return 0
}

# Is the syscall path usable? Without python3 we must fall back to burning
# boot retries, which is far slower.
tk_have_python() {
    timeout 8 ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
        'command -v python3 >/dev/null' >/dev/null 2>&1 </dev/null
}

# Re-arm the known-good slot and leave the bootloader. This is the standard
# recovery for the every-Nth-boot drop-to-bootloader; it resets the retry
# counter and clears unbootable.
#
# Refuses PORTHOLE_SLOT_FORBIDDEN. On taimen that is slot a, which has no good
# image: setting it active strands the phone in the bootloader with no way back
# except a manual flash.
tk_rearm_and_boot() {
    ph_ssh_mux_reset
    local slot=${PORTHOLE_ACTIVE_SLOT:-}
    if [ -n "$slot" ]; then
        if [ "$slot" = "${PORTHOLE_SLOT_FORBIDDEN:-}" ]; then
            echo ">> refusing to set_active $slot: the profile marks it" \
                 "PORTHOLE_SLOT_FORBIDDEN (no known-good image)" >&2
            return 1
        fi
        "$FASTBOOT" set_active "$slot" >/dev/null 2>&1 || return 1
    fi
    "$FASTBOOT" reboot >/dev/null 2>&1 || return 1
    return 0
}

# ---------------------------------------------------------------- waiting ----

# tk_wait_ssh OLD_BOOT_ID DEADLINE_MS [REISSUE_FN] [REISSUE_AFTER_S]
#
# Poll until the device is up on a NEW boot_id. Auto-recovers if it lands in
# the bootloader instead. If REISSUE_FN is given, it is called when the OLD
# boot_id is still answering after REISSUE_AFTER_S -- i.e. the reboot request
# was swallowed and never took effect.
#
# Exit: 0 up (boot id echoed on stdout), 1 deadline hit.
tk_wait_ssh() {
    local old_id=$1 deadline=$2 reissue_fn=${3:-} reissue_after=${4:-25}
    local recovered=0 last_request_ms
    last_request_ms=$(tk_now_ms)

    while ! tk_expired "$deadline"; do
        local id
        if id=$(tk_boot_id) && [ -n "$id" ]; then
            if [ "$id" != "$old_id" ]; then
                # New boot: the old master (if any) points at a dead sshd.
                ph_ssh_mux_reset
                echo "$id"
                [ "$recovered" -gt 0 ] && \
                    echo ">> (recovered from the bootloader $recovered time(s))" >&2
                return 0
            fi
            # Same boot_id: the old userspace is still answering, so the reboot
            # either hasn't landed yet or was never acted on.
            if [ -n "$reissue_fn" ] && \
               [ $(( ($(tk_now_ms) - last_request_ms) / 1000 )) -ge "$reissue_after" ]; then
                echo ">> reboot request looks swallowed, re-issuing" >&2
                "$reissue_fn"
                last_request_ms=$(tk_now_ms)
            fi
        elif tk_in_fastboot; then
            recovered=$(( recovered + 1 ))
            echo ">> landed in the bootloader -- re-arming (recovery #$recovered)" >&2
            tk_rearm_and_boot || echo ">> WARNING: fastboot re-arm failed" >&2
            last_request_ms=$(tk_now_ms)
        fi
        sleep "$TK_POLL"
    done
    return 1
}

# tk_wait_fastboot DEADLINE_MS -> 0 once `fastboot devices` answers.
tk_wait_fastboot() {
    local deadline=$1
    while ! tk_expired "$deadline"; do
        tk_in_fastboot && return 0
        sleep "$TK_POLL"
    done
    return 1
}
