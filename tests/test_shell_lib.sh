#!/bin/bash
# lib/porthole.sh: resolution, the legacy-alias contract, and agreement with
# lib/porthole.py.
#
# Two implementations of one semantics will drift unless something compares
# them. The agreement tests at the bottom are the only thing stopping a fix
# landing in the python half and not the shell half.
#
# Needs no device. Run: bash tests/test_shell_lib.sh
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); }
bad()  { FAIL=$((FAIL+1)); echo "FAIL $1"; echo "     $2"; }
is()   { # is NAME ACTUAL EXPECTED
    if [ "$2" = "$3" ]; then ok; else bad "$1" "got '$2', want '$3'"; fi
}
has()  { # has NAME HAYSTACK NEEDLE
    case $2 in *"$3"*) ok ;; *) bad "$1" "'$3' missing from: $2" ;; esac
}
hasnt() {
    case $2 in *"$3"*) bad "$1" "'$3' should be absent from: $2" ;; *) ok ;; esac
}

# Run a snippet in a clean bash with lib/porthole.sh sourced. `env -i` matters:
# the developer running the tests has PHONE or TK_HOST exported often enough
# that inheriting the environment makes these tests lie.
phsh() { # phsh 'VAR=x VAR=y' 'echo $PHONE'
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        XDG_CONFIG_HOME="$TMPXDG" $1 \
        bash -c ". '$ROOT/lib/porthole.sh'; $2" 2>&1
}

TMPXDG=$(mktemp -d)
trap 'rm -rf "$TMPXDG"' EXIT

# ------------------------------------------------------------- defaults ----

is "default PHONE composes user@host" \
   "$(phsh '' 'echo $PHONE')" "user@172.16.42.1"
is "default poll" "$(phsh '' 'echo $TK_POLL')" "0.5"
is "default fastboot" "$(phsh '' 'echo $FASTBOOT')" "fastboot"

# -------------------------------------------------------------- profile ----

is "profile supplies the SoC" \
   "$(phsh 'PORTHOLE_DEVICE=google-taimen' 'echo $PORTHOLE_SOC')" "msm8998"
is "profile supplies the forbidden slot" \
   "$(phsh 'PORTHOLE_DEVICE=google-taimen' 'echo $PORTHOLE_SLOT_FORBIDDEN')" "a"
is "profile supplies a quoted value intact" \
   "$(phsh 'PORTHOLE_DEVICE=google-taimen' 'echo $PORTHOLE_DEVICE_NAME')" \
   "Google Pixel 2 XL"
is "an inline comment is not part of the value" \
   "$(phsh 'PORTHOLE_DEVICE=google-taimen' 'echo $PORTHOLE_HAS_AB_SLOTS')" "1"

out=$(phsh 'PORTHOLE_DEVICE=nosuchdevice' 'echo unreachable')
has "a missing profile is fatal, not silent" "$out" "no profile for device"

# ------------------------------------------------- the legacy alias table ----
# One assertion per row of spec section 4.3. These are the never-break-taimen
# tests: every one of them corresponds to command lines printed in taimen docs.

is "PHONE verbatim beats a composed one" \
   "$(phsh 'PHONE=user@172.16.42.1 PORTHOLE_USER=alice PORTHOLE_HOST=10.0.0.5' \
           'echo $PHONE')" "user@172.16.42.1"
is "HOST overrides PORTHOLE_HOST" \
   "$(phsh 'HOST=172.16.42.1 PORTHOLE_HOST=10.0.0.5' 'echo $HOST')" "172.16.42.1"
is "TK_HOST overrides PORTHOLE_HOST" \
   "$(phsh 'TK_HOST=172.16.42.9 PORTHOLE_HOST=10.0.0.5' 'echo $HOST')" "172.16.42.9"
is "HOST beats TK_HOST" \
   "$(phsh 'HOST=1.1.1.1 TK_HOST=2.2.2.2' 'echo $HOST')" "1.1.1.1"
is "HOST is mined from PHONE when only PHONE is set" \
   "$(phsh 'PHONE=bob@192.168.7.7' 'echo $HOST')" "192.168.7.7"
is "TK_POLL beats PORTHOLE_POLL" \
   "$(phsh 'TK_POLL=0.1 PORTHOLE_POLL=9' 'echo $TK_POLL')" "0.1"
is "TK_AGENT beats PORTHOLE_AGENT" \
   "$(phsh 'TK_AGENT=claude PORTHOLE_AGENT=user' 'echo $TK_AGENT')" "claude"
is "TK_AGENT falls back to PORTHOLE_AGENT" \
   "$(phsh 'PORTHOLE_AGENT=user' 'echo $TK_AGENT')" "user"
is "FASTBOOT passes through" \
   "$(phsh 'FASTBOOT=/opt/fb' 'echo $FASTBOOT')" "/opt/fb"
is "the frozen tk_* surface is defined" \
   "$(phsh '' 'for f in tk_boot_id tk_uptime tk_device_state tk_in_fastboot \
                       tk_request_reboot tk_request_bootloader tk_rearm_and_boot \
                       tk_wait_ssh tk_wait_fastboot tk_now_ms tk_since \
                       tk_deadline_ms tk_expired tk_have_python; do \
                 declare -F $f >/dev/null || echo "MISSING $f"; done; echo ok')" "ok"

# ------------------------------------------------------------- ssh opts ----

opts=$(phsh '' 'echo "${TK_SSH_OPTS[@]}"')
has "host keys change every boot: no strict checking" "$opts" "StrictHostKeyChecking=no"
has "host keys change every boot: null known_hosts"   "$opts" "UserKnownHostsFile=/dev/null"
has "batch mode, so a prompt fails instead of hanging" "$opts" "BatchMode=yes"
has "connect timeout bounds the poll loop"             "$opts" "ConnectTimeout=2"
has "multiplexing is on by default"                    "$opts" "ControlMaster=auto"
has "control path is keyed on host+port+user"          "$opts" "ControlPath="
has "control master expires on its own"                "$opts" "ControlPersist=60s"

opts=$(phsh 'PORTHOLE_NO_MUX=1' 'echo "${TK_SSH_OPTS[@]}"')
hasnt "PORTHOLE_NO_MUX=1 disables multiplexing" "$opts" "ControlMaster"
has   "PORTHOLE_NO_MUX=1 keeps the mandatory flags" "$opts" "StrictHostKeyChecking=no"

opts=$(phsh 'PORTHOLE_SSH_PORT=2222' 'echo "${TK_SSH_OPTS[@]}"')
has "a non-default port reaches ssh" "$opts" "-p 2222"

# ------------------------------------------------- reboot paths reset mux ----
# A master socket that outlives a reboot is a live handle to a dead sshd. If
# any of these stops calling ph_ssh_mux_reset, the toolbox hangs instead of
# failing fast, and the cause is invisible.
for fn in tk_request_reboot tk_request_bootloader tk_rearm_and_boot; do
    body=$(phsh '' "declare -f $fn")
    has "$fn tears down the ssh master" "$body" "ph_ssh_mux_reset"
done
has "tk_wait_ssh resets the master on a new boot_id" \
    "$(phsh '' 'declare -f tk_wait_ssh')" "ph_ssh_mux_reset"

# --------------------------------------------- the forbidden-slot guard ----

body=$(phsh '' 'declare -f tk_rearm_and_boot')
has "rearm refuses the forbidden slot" "$body" "PORTHOLE_SLOT_FORBIDDEN"

out=$(phsh 'PORTHOLE_DEVICE=google-taimen PORTHOLE_ACTIVE_SLOT=a FASTBOOT=/bin/true' \
           'tk_rearm_and_boot; echo "rc=$?"')
has "set_active on the forbidden slot is refused" "$out" "refusing to set_active a"
has "and it reports failure"                      "$out" "rc=1"

# ------------------------------------------------------ python agreement ----
# The two libs must resolve identically or the toolbox behaves differently
# depending on which language a tool happens to be written in.

agree() { # agree ENV KEY  -- compare shell vs python for one resolved key
    local env=$1 key=$2 sh py
    sh=$(phsh "$env" "echo \$$key")
    py=$(env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
             XDG_CONFIG_HOME="$TMPXDG" $env python3 -c "
import sys; sys.path.insert(0, '$ROOT/lib')
import porthole
cfg = porthole.load_config()
key = '$key'
if key == 'PHONE':  print(porthole.resolve_phone(cfg))
elif key == 'HOST': print(porthole.resolve_host(cfg))
else:               print(cfg.get(key, ''))
" 2>&1)
    is "shell and python agree on $key ($env)" "$sh" "$py"
}

agree '' PHONE
agree '' HOST
agree 'PHONE=user@172.16.42.1' PHONE
agree 'PHONE=bob@192.168.7.7' HOST
agree 'HOST=1.1.1.1 TK_HOST=2.2.2.2' HOST
agree 'TK_HOST=172.16.42.9' HOST
agree 'PORTHOLE_USER=alice PORTHOLE_HOST=10.0.0.5' PHONE
agree 'PORTHOLE_DEVICE=google-taimen' PORTHOLE_SOC
agree 'PORTHOLE_DEVICE=google-taimen' PORTHOLE_SLOT_FORBIDDEN
agree 'PORTHOLE_DEVICE=google-taimen' PORTHOLE_DEVICE_NAME
agree 'PORTHOLE_DEVICE=google-taimen' PORTHOLE_WATCHDOG_MAX_S

echo "$PASS/$((PASS+FAIL)) passed"
[ "$FAIL" -eq 0 ]
