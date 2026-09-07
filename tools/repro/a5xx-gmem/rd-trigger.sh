#!/bin/bash
# Trigger FD_RD_DUMP=enable,trigger dumps from a running process, by PROCESS NAME.
#   rd-trigger.sh phoc 6        # dump the next 6 submits of phoc
#
# Why not `echo 6 > $FD_RD_DUMP_PATH/phoc_trigger`: a process that opens the
# GPU twice (phoc does: renderer + allocator) creates two fd_rd_output objects,
# each open()s the trigger with O_TRUNC, and when one device is closed its
# fini UNLINKS the file. The device that actually submits can then be reading
# a deleted inode while the path on disk belongs to the one that never
# submits -- writing the path does nothing (2026-09-03, cost half an hour).
# Writing through /proc/<pid>/fd/<n> reaches every open instance.
set -uo pipefail
. "$(dirname "$0")/../../ph-lib.sh"
NAME=$1; N=${2:-1}
tk_run "p=\$(pgrep -x $NAME | head -1); [ -n \"\$p\" ] || { echo 'no such process'; exit 1; }; \
  fds=\$(ls -l /proc/\$p/fd 2>/dev/null | awk '/_trigger/{print \$9}'); [ -n \"\$fds\" ] || { echo 'no trigger fd: FD_RD_DUMP=enable,trigger not in its env'; exit 2; }; \
  for fd in \$fds; do echo $N > /proc/\$p/fd/\$fd; done; echo \"triggered $N on \$(echo \$fds | wc -w) fd(s)\""
