#!/bin/bash
# NOTE: set A5XX_WORK to a writable scratch dir (default /tmp/a5xx-gmem).
# Paths were de-hardcoded from the original session scratchpad.
# One arm: restart the graphical session with a chosen FD_MESA_DEBUG for phoc
# (via ~/.phoshdebug, which /usr/bin/phosh-session sources before exec'ing phoc),
# unlock for real (swipe + PIN, logind's unlock-sessions does NOT dismiss phosh's
# lockscreen), load the strip detector, and measure the panel at the bin edge.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$HERE/../../.." || exit
source tools/tk-lib.sh
source "$HERE/session_state.sh"
TAG=$1; VAL=${2:-}; OUT=$3
case $VAL in '') tk_run 'rm -f ~/.phoshdebug' >/dev/null ;; *=*) tk_run "printf 'export %s\n' '$VAL' > ~/.phoshdebug" >/dev/null ;; *) tk_run "printf 'export FD_MESA_DEBUG=%s\n' '$VAL' > ~/.phoshdebug" >/dev/null ;; esac
sid=$(tk_run "loginctl list-sessions --no-legend | awk '\$4==\"seat0\"{print \$1}' | head -1" | tr -d '\r ')
tk_run "sudo -n loginctl terminate-session $sid" >/dev/null 2>&1 || true
sleep 12
for _ in $(seq 1 30); do timeout 5 ssh "${TK_SSH_OPTS[@]}" "$PHONE" true 2>/dev/null && break; sleep 3; done
scp "${TK_SSH_OPTS[@]}" tools/tk-greetd-login.py tools/tk-touch.py tools/tk-key.py tools/tk-webeval.py tools/tk-webvq.py "$PHONE:/tmp/" >/dev/null 2>&1
printf '%s' "$TK_LOGIN_PASSWORD" | ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
  'sudo -n env TK_LOGIN_PASSWORD="$(cat)" python3 /tmp/tk-greetd-login.py '"$PORTHOLE_USER" 2>&1 | tail -1
sleep 15
unlock_swipe || { echo '  ABORT: still locked'; exit 1; }
echo -n "  phoc env: "; tk_run "p=\$(pgrep -x phoc); tr '\\0' '\\n' < /proc/\$p/environ | grep FD_MESA_DEBUG || echo UNSET"
scp "${TK_SSH_OPTS[@]}" "$HERE/sess.sh" tools/tk-rangehttp.py "$PHONE:/tmp/" >/dev/null 2>&1
scp "${TK_SSH_OPTS[@]}" tools/repro/a5xx-gmem-strip.html "$PHONE:~/" >/dev/null 2>&1
tk_run ". /tmp/sess.sh; pkill -x epiphany; sleep 2; rm -f ~/.local/share/epiphany/session_state.xml; \
  (cd ~ && setsid python3 /tmp/tk-rangehttp.py 8080 ~ >/tmp/http.log 2>&1 &); sleep 2; \
  setsid systemd-run --user --scope --quiet --slice=app.slice -u app-eph-$TAG.scope \
  env ${EPH_ENV:-} WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222 \
  ${CLIENT_CMD:-epiphany 'http://127.0.0.1:8080/a5xx-gmem-strip.html'} >/tmp/eph.log 2>&1 </dev/null & sleep 28; echo '  client launched'"
wait_ready "${WAIT_TITLE:-strip detector}" || { echo "  ABORT: arm invalid"; exit 3; }
tk_run ". /tmp/sess.sh; grim /tmp/cap-$TAG.png" >/dev/null 2>&1
scp "${TK_SSH_OPTS[@]}" "$PHONE:/tmp/cap-$TAG.png" "$OUT/" >/dev/null 2>&1
echo "  panel stddev  x=1016:$(magick $OUT/cap-$TAG.png -crop 4x75+1016+10 +repage -colorspace Gray -format '%[fx:standard_deviation]' info:)  x=1024:$(magick $OUT/cap-$TAG.png -crop 4x75+1024+10 +repage -colorspace Gray -format '%[fx:standard_deviation]' info:)  x=1280:$(magick $OUT/cap-$TAG.png -crop 4x75+1280+10 +repage -colorspace Gray -format '%[fx:standard_deviation]' info:)"
