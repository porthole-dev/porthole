#!/bin/bash
# CROP=WxH+X+Y (optional) scores a sub-rect, e.g. CROP=230x75+0+0 excludes the battery icon that enters the strip while charging.
D=$1; T=$(basename $D | sed 's/rate-//')
n=0; bad=0; mx=0
for f in $D/p*.png; do
  s=$(magick "$f" ${CROP:+-crop $CROP +repage} -colorspace Gray -format "%[fx:standard_deviation]" info:)
  n=$((n+1))
  awk -v s="$s" 'BEGIN{exit !(s>0.05)}' && bad=$((bad+1))
  awk -v s="$s" -v m="$mx" 'BEGIN{exit !(s>m)}' && mx=$s
done
echo "$T: $bad/$n samples corrupt ($(awk -v b=$bad -v n=$n 'BEGIN{printf "%.0f", 100*b/n}')%), max stddev $mx"
