#!/bin/bash
set -uo pipefail
cd /home/user/src/taimen
source tools/tk-lib.sh
P=/home/user/.local/var/porthole-sandbox/packages/edge/aarch64
V=${1:?mesa version, e.g. 26.1.6-r8}
PKGS="mesa mesa-dbg mesa-dri-gallium mesa-egl mesa-gbm mesa-gl mesa-gles mesa-vulkan-freedreno"
tk_run "mkdir -p /tmp/mesa-apk && rm -f /tmp/mesa-apk/*.apk"
for p in $PKGS; do
  scp "${TK_SSH_OPTS[@]}" "$P/$p-$V.apk" "$PHONE:/tmp/mesa-apk/" || { echo "PUSH FAILED $p"; exit 1; }
done
TK_RUN_TIMEOUT=180 tk_run "sudo -n apk add --allow-untrusted /tmp/mesa-apk/*.apk 2>&1 | tail -12"
tk_run "apk info -v | grep -E '^mesa' | sort"
