#!/bin/bash
# scope: soc:qcom
# UFS / block / swap measurement for taimen. Run ON THE DEVICE as root:
#
#   TK_AGENT=<you> tools/tk-device.sh ssh "$PHONE" 'sudo -n bash -s' \
#       < tools/tk-storage-bench.sh
#
# What it answers, in order:
#   1. what gear/rate/lane the UFS link actually negotiated, idle and busy
#   2. what a scale event COSTS, from the driver's own ftrace timing
#      (ufs:ufshcd_profile_clk_scaling records the duration in us)
#   3. whether pinning the clock+gear (clkscale_enable=0) buys anything --
#      the A/B that decides if any UFS fix is worth shipping
#   4. the rest of the path: elevator, readahead, zram, swap, reclaim, PSI
#
# The ONLY device state it touches is clkscale_enable, the three ufs
# tracepoints and the page cache, and it restores all of them. It is
# otherwise read-only: /dev/sda is only ever read.
#
# ponytail: dd in a shell loop rather than fio. fio is not installed, and
# the 4k loop is dominated by dd's own fork -- it is a RELATIVE number, only
# valid compared against the pinned run in the same invocation. Install fio
# if an absolute IOPS figure is ever needed.
set -u

UFS=/sys/bus/platform/drivers/ufshcd-qcom/1da4000.ufshc
DF=/sys/class/devfreq/1da4000.ufshc
T=/sys/kernel/tracing
EVENTS="ufs/ufshcd_profile_clk_scaling ufs/ufshcd_clk_gating"

[ -d "$UFS" ] || { echo "no ufshc at $UFS"; exit 1; }

st() {
	echo "   gear=$(cat $UFS/power_info/gear) rate=$(cat $UFS/power_info/rate)" \
	     "lane=$(cat $UFS/power_info/lane) link=$(cat $UFS/power_info/link_state)" \
	     "cur=$(cat $DF/cur_freq) min=$(cat $DF/min_freq) max=$(cat $DF/max_freq)"
}

# Cold read of real rootfs files: closest cheap stand-in for an app launch.
coldread() {
	sync; echo 3 > /proc/sys/vm/drop_caches
	local s e
	s=$(date +%s%N)
	echo "$FILES" | while read -r f; do cat "$f" > /dev/null 2>&1; done
	e=$(date +%s%N)
	echo "   cold-read $NFILES files / $BYTES B: $(( (e-s)/1000000 )) ms"
}

seqread() { dd if=/dev/sda of=/dev/null bs=1M count=256 iflag=direct 2>&1 | tail -1; }

rnd4k() {
	local s e i=0
	s=$(date +%s%N)
	while [ $i -lt 200 ]; do
		dd if=/dev/sda of=/dev/null bs=4k count=1 \
		   skip=$(( (i*7919) % 900000 )) iflag=direct 2>/dev/null
		i=$((i+1))
	done
	e=$(date +%s%N)
	echo "   200x4k O_DIRECT: $(( (e-s)/1000000 )) ms"
}

restore() {
	echo 1 > $UFS/clkscale_enable 2>/dev/null
	for e in $EVENTS; do [ -d $T/events/$e ] && echo 0 > $T/events/$e/enable; done
	echo 0 > $T/tracing_on 2>/dev/null
	: > $T/trace 2>/dev/null
}
trap restore EXIT

echo "### kernel $(uname -r)  uptime $(cat /proc/uptime)"
echo "### UFS static"
echo "   clkscale_enable=$(cat $UFS/clkscale_enable) clkgate_enable=$(cat $UFS/clkgate_enable)"
for k in clkgate_delay_ms auto_hibern8 wb_on rpm_lvl spm_lvl; do
	[ -e $UFS/$k ] && echo "   $k=$(cat $UFS/$k 2>/dev/null)"
done
echo "   spec_version=$(cat $UFS/device_descriptor/specification_version)" \
     "queue_depth=$(cat $UFS/device_descriptor/queue_depth)"
echo "### idle"; st

FILES=$(find /usr/lib /usr/bin -maxdepth 2 -type f -size +32k 2>/dev/null | head -400)
NFILES=$(echo "$FILES" | wc -l)
BYTES=$(echo "$FILES" | xargs -r du -cb 2>/dev/null | tail -1 | cut -f1)

echo "### A: default (clock scaling on)"
echo 0 > $T/tracing_on; : > $T/trace
for e in $EVENTS; do [ -d $T/events/$e ] && echo 1 > $T/events/$e/enable; done
echo 1 > $T/tracing_on
coldread; st
seqread; st
rnd4k; st
echo 0 > $T/tracing_on
echo "   scale events: $(grep -c clk_scaling $T/trace 2>/dev/null)" \
     "gating events: $(grep -c clk_gating $T/trace 2>/dev/null)"
grep clk_scaling $T/trace 2>/dev/null | tail -20

echo "### B: clkscale_enable=0 (clock and gear pinned at max)"
echo 0 > $UFS/clkscale_enable; sleep 1; st
coldread
seqread
rnd4k
st

echo "### restore"
restore
echo "   clkscale_enable=$(cat $UFS/clkscale_enable)"; st
echo "### devfreq trans_stat"; cat $DF/trans_stat

echo "### block"
for d in /sys/block/sd*/queue; do
	echo "-- $d"
	for k in scheduler read_ahead_kb nr_requests rotational max_sectors_kb \
	         nomerges rq_affinity iostats discard_max_bytes; do
		[ -e $d/$k ] && echo "   $k: $(cat $d/$k 2>/dev/null)"
	done
done
echo "### zram"
for d in /sys/block/zram*/; do
	echo "-- $d"
	for k in comp_algorithm disksize mm_stat; do
		[ -e "$d$k" ] && echo "   $k: $(cat "$d$k")"
	done
done
echo "### swap"; cat /proc/swaps; free -m
echo "### vm"
for k in swappiness page-cluster vfs_cache_pressure min_free_kbytes \
         watermark_scale_factor dirty_ratio dirty_background_ratio; do
	echo "   $k=$(cat /proc/sys/vm/$k)"
done
echo "### mounts"; grep -E ' (ext4|f2fs|btrfs|vfat) ' /proc/mounts
echo "### reclaim"; grep -E 'pgmajfault|pswpin|pswpout|workingset_refault|oom_kill|allocstall' /proc/vmstat
echo "### psi"; head -2 /proc/pressure/io; head -2 /proc/pressure/memory
echo "### errors"; dmesg | grep -iE 'out of memory|oom-kill|ufshcd.*err|scsi.*error|I/O error' | tail -20
echo "### DONE"
