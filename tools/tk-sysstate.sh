#!/bin/sh
# scope: generic
# needs: on-device (run it on the device, e.g. piped over ssh)
# env: TK_PUSH
# exits: 0 ok · non-zero on failure
# Print one compact line of system state per second, forever.
#
# Run ON THE DEVICE, streamed to the host with tk-stream.sh. This exists for
# failures that produce NO kernel output at all -- the taimen hard-hangs on
# phosh session start with no oops, no panic and no RCU stall, so `dmesg -w`
# captures nothing and the only evidence left is whatever state reached the host
# in the last second before the SoC stopped answering.
#
# Deliberately tiny: every extra field is another thing that might not get
# flushed over ssh before the device dies. Nothing here opens a debugfs file --
# in particular NOT /sys/kernel/debug/dri/0/gpu, which wedges the GPU.
#
# Usage (from the host):
#   TK_PUSH=tools/tk-sysstate.sh tk-stream.sh OUT sh /tmp/tk-sysstate.sh
while :; do
    up=$(cut -d. -f1 /proc/uptime)
    load=$(cut -d' ' -f1 /proc/loadavg)
    mem=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
    procs=$(ls -d /proc/[0-9]* 2>/dev/null | wc -l)
    irq=$(grep -i ftm4 /proc/interrupts | awk '{print $2}')
    top=$(ps -eo comm,pcpu 2>/dev/null | sort -k2 -nr | head -2 | tail -1 | tr -s ' ')
    echo "t=${up}s load=${load} memavail=${mem}kB procs=${procs} ftm4_irq=${irq} top=[${top}]"
    sleep 1
done
