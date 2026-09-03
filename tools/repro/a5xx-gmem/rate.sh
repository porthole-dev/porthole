#!/bin/sh
# Sample the panel strip repeatedly. Region (logical coords, DPR3) is
# physical x 1023..1329, y 9..84: starts at the x=1024 bin edge and stops
# short of the battery icon, so a clean panel is flat and any nonzero
# variance is corruption. The defect is intermittent, so only a RATE over
# many samples means anything -- single captures have been misleading.
. /tmp/sess.sh
TAG=$1; N=${2:-40}
rm -rf /home/user/rate-$TAG; mkdir -p /home/user/rate-$TAG
i=0
while [ $i -lt $N ]; do
  timeout 20 grim -g "341,3 102x25" /home/user/rate-$TAG/p$(printf %03d $i).png 2>/dev/null
  timeout 20 grim -g "100,3 67x25" /home/user/rate-$TAG/q$(printf %03d $i).png 2>/dev/null   # left flat panel region x 300..500, tile column 0
  i=$((i+1)); sleep 2
done
echo "[$TAG] $(ls /home/user/rate-$TAG/*.png 2>/dev/null | wc -l) samples"
cd /home/user && tar cf /tmp/rate-$TAG.tar rate-$TAG
