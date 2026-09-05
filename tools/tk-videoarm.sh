#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as the session user (tk-webeval.py, tk-gesture-bench.py,
#        tk-touch.py, tk-ui.py in /tmp; /tmp/sess.sh); Epiphany; grim, lswt;
#        a local clip.
# env: TK_VIDEO_FILE (default ~/vp9_1440p60.webm), TK_VIDEO_SECONDS
# exits: 0 measured · 1 the arm is void -- the video never advanced
# tk-videoarm.sh LABEL [ENV...] -- one PLAYBACK arm on a LOCAL clip.
#
# tk-webarm.sh measures playback on YouTube, which drags three sources of
# variance into every number: the network, YouTube's ABR picking a different
# resolution per arm (it served 720p to one arm and 1440p to another on the same
# night), and a page full of JavaScript. For comparing two builds of the
# compositor none of that is wanted. This plays a local file in a page with
# nothing else on it, so the only thing left moving between arms is the code
# under test.
#
# The clip is VP9 1440p60 -- the panel's own resolution and refresh, and inside
# WEBKIT_GST_VIDEO_DECODING_LIMIT, so venus decodes it in hardware.
#
#   tk-videoarm.sh base
#   tk-videoarm.sh pipe2 "WEBKIT_COMPOSITOR_MAX_FRAMES_IN_FLIGHT=2"
set -u
L=${1:?usage: tk-videoarm.sh LABEL [ENV...]}; X=${2:-}
CLIP=${TK_VIDEO_FILE:-$HOME/Videos/vtest/v1440.mp4}
# An existing page next to the clip wins over the one generated below: this
# device already has /home/user/Videos/vtest/*.html, made when these clips were,
# and they are known to play. TK_VIDEO_URL overrides everything.
URL=${TK_VIDEO_URL:-}
[ -n "$URL" ] || { alt="${CLIP%.*}.html"; [ -f "$alt" ] && URL="file://$alt"; }
SECS=${TK_VIDEO_SECONDS:-12}
[ -f "$CLIP" ] || { echo "[$L] no clip at $CLIP -- arm void"; exit 1; }

# The clip has to sit BESIDE the page: a file:// document may not reach another
# directory (WebKit treats each file:// document as its own origin), and the web
# process's sandbox does not bind $HOME anyway. Symptom of getting this wrong is
# a <video> that stays paused with videoWidth 0 and no error at all.
mkdir -p /tmp/tk-vid
[ -e "/tmp/tk-vid/clip.${CLIP##*.}" ] || cp "$CLIP" "/tmp/tk-vid/clip.${CLIP##*.}"
cat > /tmp/tk-vid/vid-$L.html <<EOF
<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<style>html,body{margin:0;background:#000}video{width:100vw;display:block}</style>
<video src="clip.${CLIP##*.}" autoplay muted loop playsinline></video>
EOF

for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do
	systemctl --user stop "$u"
done
sleep 2
rm -f ~/.local/share/epiphany/session_state.xml "/tmp/wlv-$L.log"

setsid systemd-run --user --scope --quiet --slice=app.slice \
	-u "app-gnome-org.gnome.Epiphany-v$$.scope" \
	env WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_SKIA_CPU_PAINTING_THREADS=2 \
	WEBKIT_GST_VIDEO_DECODING_LIMIT=2560x1440@60 WEBKIT_LAYERS_TILE_SIZE=1440x1024 \
	WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222 WAYLAND_DEBUG=1 \
	$X epiphany "${URL:-file:///tmp/tk-vid/vid-$L.html}" >"/tmp/ephv-$L.log" 2>"/tmp/wlv-$L.log" </dev/null &

# Prove it is PLAYING before measuring anything: currentTime advancing is the
# only statement of that which cannot be faked by a poster frame.
# Muted autoplay is normally allowed, but ask anyway with a user gesture: a
# refused play() and a clip that has not loaded look identical from outside.
t1=""; for i in $(seq 1 30); do
	sleep 2
	[ "$i" = 4 ] && TK_GESTURE=1 python3 /tmp/tk-webeval.py 'var v=document.querySelector("video"); v.muted=true; v.play(); 1' >/dev/null 2>&1
	t1=$(python3 /tmp/tk-webeval.py 'var v=document.querySelector("video"); v?v.currentTime:-1' 2>/dev/null | tail -1)
	case "$t1" in ''|-1|*[!0-9.]*) ;; *) [ "${t1%%.*}" -ge 1 ] 2>/dev/null && break ;; esac
done
sleep 3
t2=$(python3 /tmp/tk-webeval.py 'var v=document.querySelector("video"); v?v.currentTime:-1' 2>/dev/null | tail -1)
echo "[$L] $(python3 /tmp/tk-webeval.py 'var v=document.querySelector("video"); JSON.stringify({w:v.videoWidth,h:v.videoHeight,paused:v.paused})' 2>/dev/null | tail -1)  t $t1 -> $t2"
python3 - "${t1:-0}" "${t2:-0}" <<'PY' || exit 1
import sys
try:
    if float(sys.argv[2]) - float(sys.argv[1]) < 1.0:
        sys.exit("[arm void] currentTime did not advance -- nothing was playing")
except ValueError:
    sys.exit("[arm void] currentTime unreadable")
PY

sudo -n python3 /tmp/tk-gesture-bench.py watch "$SECS" --client "/tmp/wlv-$L.log" 2>&1 | tail -7
echo "  gpu $(( $(cat /sys/class/devfreq/5000000.gpu/cur_freq) / 1000000 )) MHz   die $(cat /sys/class/thermal/thermal_zone*/temp | sort -n | tail -1) mC"
echo "  dropped/total: $(python3 /tmp/tk-webeval.py 'var q=document.querySelector("video").getVideoPlaybackQuality(); q.droppedVideoFrames+"/"+q.totalVideoFrames' 2>/dev/null | tail -1)"
