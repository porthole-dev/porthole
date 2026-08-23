#!/usr/bin/env python3
# scope: soc:msm8998
"""Read what the msm8998 OSM actually delivered.

Register map from the vendor driver ref/downstream-wahoo/drivers/clk/msm/
clock-osm.c: offsets there are relative to the OSM base (0x179c0000 domain 0,
0x179c2000 domain 1), which is 0x1000 below the "freq-domain" window the
mainline qcom-cpufreq-hw driver maps.

  0x1004 ENABLE            0x1150 INDEX      0x1154 FREQ    0x1158 VOLT
  0x1c00 WDOG_DOMAIN_PSTATE_STATUS          <- the OSM's OWN current p-state
  0x1f00 CYCLE_COUNTER_CTRL 0x1f04 CYCLE_COUNTER_STATUS
  0x1f10 DCVS_PERF_STATE_DESIRED            <- what the driver asked for
  0x1f14 DEVIATION_INTR_STAT  0x1f2c MET_INTR_STAT

The supply side is read too -- CPRH_STATUS and the APM controller status -- so a
parked OSM can be told apart from an OSM waiting on a rail that never moved.
"""
import mmap, os, struct, sys, time

OSM = {0: 0x179C0000, 1: 0x179C2000}
PLL = {0: 0x17916000, 1: 0x17816000}
RCG = {0: 0x17911000, 1: 0x17811000}   # cfg at +0x54, cmd at +0x50
# The supply side, which nothing in this port has ever read.  CPRh controller
# bases come from msm8998.dtsi apc_cprh reg = <0x179c8000>, <0x179c4000>; the
# APM control/status pairs from the vendor DT qcom,apm-mode-ctl /
# qcom,apm-ctrl-status.  drivers/pmdomain/qcom/cpr3.c declares reg_ctl/reg_status
# and never reads them: if the OSM is parked because the rail did not move, this
# is where it says so.
CPRH = {0: 0x179C8000, 1: 0x179C4000}
APM = {0: (0x179D0004, 0x179D000C), 1: (0x179D0010, 0x179D0018)}
CPRH_VERSION_4P5 = 0x40050000

maps = {}


def mp(base, size=0x2000):
    if base not in maps:
        fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
        maps[base] = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ,
                               offset=base)
        os.close(fd)
    return maps[base]


def rd(base, off, size=0x2000):
    return struct.unpack_from("<I", mp(base, size), off)[0]


def main():
    for d in (0, 1):
        b = OSM[d]
        print("== domain %d (osm base 0x%08x) ==" % (d, b))
        print("  ENABLE            +1004 = 0x%08x" % rd(b, 0x1004))
        print("  PERF_STATE_DESIRED+1f10 = %d" % rd(b, 0x1F10))
        print("  WDOG_PSTATE_STATUS+1c00 = 0x%08x" % rd(b, 0x1C00))
        print("  DEVIATION_STAT    +1f14 = 0x%08x" % rd(b, 0x1F14))
        print("  MET_STAT          +1f2c = 0x%08x" % rd(b, 0x1F2C))
        print("  CC_CTRL           +1f00 = 0x%08x" % rd(b, 0x1F00))
        # bit0-2 CC/PS/DCVS boost, bit3-5 PC_RET/WFX/DCVS droop.
        # Vendor wahoo runs 0x7 (droop FSMs never enabled on msm8998).
        print("  PDN_FSM_CTRL      +1070 = 0x%08x" % rd(b, 0x1070))
        # SEQ_REG1_MSM8998_V2: the vendor writes apm_threshold_vc here, mainline
        # writes num_entries. Only readable once the OSM sequence has run.
        print("  SEQ_REG1          +1048 = %d (0x%08x)  [addr 0x%08x]"
              % (rd(b, 0x1048), rd(b, 0x1048), b + 0x1048))
        for i in (0, 1, 2, 3, 21, 22, 23):
            p = i * 32
            print("  LUT[%2d] idx=%d freq=0x%08x volt=0x%08x" %
                  (i, rd(b, 0x1150 + p), rd(b, 0x1154 + p), rd(b, 0x1158 + p)))
        print("  PLL_MODE   0x%08x = 0x%08x" % (PLL[d], rd(PLL[d], 0)))
        print("  PLL_L_VAL  0x%08x = %d" % (PLL[d] + 4, rd(PLL[d], 4)))
        print("  PLL_USER_C 0x%08x = 0x%08x" % (PLL[d] + 0xC, rd(PLL[d], 0xC)))
        print("  PLL_STATUS 0x%08x = 0x%08x" % (PLL[d] + 0x2C, rd(PLL[d], 0x2C)))
        print("  RCG_CMD    0x%08x = 0x%08x" % (RCG[d] + 0x50, rd(RCG[d], 0x50)))
        print("  RCG_CFG    0x%08x = 0x%08x" % (RCG[d] + 0x54, rd(RCG[d], 0x54)))
        # The OSM does not change a corner itself: its sequencer executes a
        # program to do it. If the program counter is parked, or moves and
        # comes back to the same place, the request was accepted and never
        # carried out -- which is exactly "PERF_STATE_DESIRED set, p-state 0".
        pc0 = rd(b, 0x1C74)
        time.sleep(0.05)
        pc1 = rd(b, 0x1C74)
        print("  SEQ_PC     +1c74 = 0x%08x -> 0x%08x %s"
              % (pc0, pc1, "(parked)" if pc0 == pc1 else "(moving)"))
        print("  SEQ_BUSY   +1c78 = 0x%08x" % rd(b, 0x1C78))
        print("  PSTATE_RB  +1f10 = %d   (what the driver last asked for)"
              % rd(b, 0x1F10))

    for d in (0, 1):
        b = CPRH[d]
        rev = rd(b, 0, 0x4000)
        # cpr3.c:1846 picks the offsets from the hardware revision.
        ctl, sts = (0x3A80, 0x3A84) if rev >= CPRH_VERSION_4P5 else (0x3AA0, 0x3AA4)
        print("== cprh %d (base 0x%08x) ==" % (d, b))
        print("  CPR_VERSION       +0000 = 0x%08x" % rev)
        print("  CPRH_CTL          +%04x = 0x%08x" % (ctl, rd(b, ctl, 0x4000)))
        print("  CPRH_STATUS       +%04x = 0x%08x  [0x%08x]"
              % (sts, rd(b, sts, 0x4000), b + sts))
        mctl, mstat = APM[d]
        print("  APM_MODE_CTL   0x%08x = 0x%08x"
              % (mctl, rd(mctl & ~0xFFF, mctl & 0xFFF, 0x1000)))
        print("  APM_CTRL_STAT  0x%08x = 0x%08x   (0=MX, bit1=APC)"
              % (mstat, rd(mstat & ~0xFFF, mstat & 0xFFF, 0x1000)))

    # Delivered-clock estimate: the OSM cycle counter ticks at the real CPU
    # clock divided by the programmed XO ratio (10), so cycles/s * 10 = Hz.
    for d in (0, 1):
        b = OSM[d]
        a = rd(b, 0x1F04)
        t0 = time.time()
        time.sleep(1.0)
        c = rd(b, 0x1F04)
        dt = time.time() - t0
        delta = (c - a) & 0xFFFFFFFF
        print("domain %d cycle counter: %d -> %d, delta %d in %.3fs"
              " => %.1f MHz raw, %.1f MHz x10" %
              (d, a, c, delta, dt, delta / dt / 1e6, delta * 10 / dt / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
