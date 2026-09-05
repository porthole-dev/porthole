#!/bin/bash
# Full-screen captures; two regions analysed separately.
#   PANEL : x 1024..1439, y 0..120    -- phosh statusbar right side (the reported glitch)
#   WEB   : x  864..1439, y 200..2500 -- browser static region, straddles x=1024 bin edge
D=$1; cd "$D" || exit 1
ref=$(ls s*.png | head -1)
r(){ magick "$ref" "$2" -compose difference -composite -colorspace Gray -threshold 10% -crop $1 +repage -format "%[fx:int(mean*w*h)]" info:; }
np=0; nw=0; n=0
for f in s*.png; do
  [ "$f" = "$ref" ] && continue
  n=$((n+1))
  p=$(r "416x120+1024+0" "$f"); w=$(r "576x2300+864+200" "$f")
  [ "$p" != "0" ] && { np=$((np+1)); echo "  PANEL $f: $p px"; }
  [ "$w" != "0" ] && { nw=$((nw+1)); echo "  WEB   $f: $w px  cols: $(magick "$ref" "$f" -compose difference -composite -colorspace Gray -threshold 10% -crop 576x2300+864+200 +repage -scale 18x1! -depth 8 txt:- | awk -F'[,:( ]+' 'NR>1{printf "%d ", strtonum("0x" substr($4,2,2))}')"; }
done
echo "SUMMARY over $n captures: panel differs $np, web differs $nw"
