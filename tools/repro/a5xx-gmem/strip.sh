#!/bin/sh
# Strip detector. Captures the STATIC right-hand region repeatedly while the
# left side animates, so phoc composites the full screen every frame.
# Region spans x 864..1439 -- straddles the x=1024 bin-column boundary, so a
# defect confined to the third bin column is visible as an edge at 1024.
# Any difference between captures is corruption: the region cannot change.
. /tmp/sess.sh
TAG=${1:-base}
N=${2:-40}
rm -rf $HOME/strip-$TAG; mkdir -p $HOME/strip-$TAG
(cd $HOME && setsid python3 /tmp/tk-rangehttp.py 8080 $HOME >/tmp/http.log 2>&1 &) 2>/dev/null
sleep 2
for u in $(systemctl --user list-units 'app-*.scope' --no-legend | awk '{print $1}' | grep -i eph); do
    systemctl --user stop "$u" 2>/dev/null; done
pkill -x epiphany 2>/dev/null; sleep 3
rm -f "$HOME/.local/share/epiphany/session_state.xml"
F0=$(sudo -n dmesg | grep -c "gpu fault")
setsid systemd-run --user --scope --quiet --slice=app.slice -u "app-eph-strip.scope" \
  env WEBKIT_LAYERS_TILE_SIZE=1440x1024 WEBKIT_SKIA_CPU_PAINTING_THREADS=2 \
  epiphany "http://127.0.0.1:8080/a5xx-gmem-strip.html" >/tmp/eph.log 2>&1 </dev/null &
sleep 20
i=0
while [ $i -lt $N ]; do
  timeout 40 grim $HOME/strip-$TAG/s$(printf %03d $i).png 2>/dev/null
  i=$((i+1)); sleep 3
done
echo "[$TAG] captures=$(ls $HOME/strip-$TAG/*.png 2>/dev/null | wc -l) faults=+$(( $(sudo -n dmesg | grep -c 'gpu fault') - F0 ))"
du -sh $HOME/strip-$TAG
