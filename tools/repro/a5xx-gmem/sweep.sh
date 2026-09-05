#!/bin/bash
# Run one rig arm per argument and score both panel regions.
#   sweep.sh <env-line-or-FD_MESA_DEBUG-value-or-""> ...
# e.g. sweep.sh "FD5_HACK=0x3fb" "FD5_HACK=0x002"
# Needs PHONE/HOST, TK_LOGIN_PASSWORD, A5XX_WORK. Prints one line per arm:
#   <tag>: right N/40 (mean m) | left N/40
set -uo pipefail
cd "$(dirname "$0")/../../.." || exit
. tools/tk-lib.sh
for val in "$@"; do
  tag=$(printf '%s' "${val:-default}" | tr -c 'A-Za-z0-9' '_' | cut -c1-40)
  tools/tk-device.sh --need-booted bash -c ". tools/tk-lib.sh; tools/repro/a5xx-gmem/arm.sh $tag '$val' \$A5XX_WORK >/dev/null 2>&1 && tools/repro/a5xx-gmem/runrate.sh $tag >/dev/null 2>&1 && tar xf \$A5XX_WORK/rate-$tag.tar -C \$A5XX_WORK" 2>&1 | grep -v -i password | grep -E 'ABORT|invalid' && { echo "$tag: ARM INVALID"; continue; }
  mkdir -p "$A5XX_WORK/rate-$tag-left"
  for f in "$A5XX_WORK/rate-$tag"/q*.png; do cp "$f" "$A5XX_WORK/rate-$tag-left/p${f##*/q}"; done
  r=$(CROP=230x75+0+0 tools/repro/a5xx-gmem/ratecalc.sh "$A5XX_WORK/rate-$tag" | sed 's/.*: \([0-9]*\/[0-9]*\).*/\1/')
  l=$(tools/repro/a5xx-gmem/ratecalc.sh "$A5XX_WORK/rate-$tag-left" | sed 's/.*: \([0-9]*\/[0-9]*\).*/\1/')
  m=$(magick "$A5XX_WORK/rate-$tag/p005.png" -crop 230x75+0+0 +repage -format '%[fx:mean]' info:)
  echo "$tag: right $r (mean $m) | left $l"
done
