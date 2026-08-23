#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok
"""Re-run the CSIPHY v5.0.1 init sequence by hand, WHILE the sensor is already
transmitting, and report whether the PHY then sees anything.

Why this is worth doing: camss configures the CSIPHY during the s_stream walk,
which runs video -> vfe -> ispif -> csid -> csiphy -> sensor. The PHY is
therefore brought up while the MIPI lines are still idle, and the sensor only
starts afterwards. If this PHY latches line state at enable, or needs the clock
lane already toggling when it comes out of reset, the driver's ordering would
leave it stuck exactly the way this one is -- configured perfectly and deaf.

Doing it from userspace, against a confirmed-live sensor, tests the ORDERING
without touching the driver.

Sequence and values are downstream's msm_csiphy_2phase_lane_config_v50() read
through msm_csiphy_5_0_1_hwreg.h; the per-lane values are identical to
mainline's sdm845 gen2 table.

Run ON the phone, as root, with camss holding the pipeline open.
"""
import mmap, os, struct, sys, time

PHY = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x0CA35000
SETTLE = int(sys.argv[2], 0) if len(sys.argv) > 2 else 14
PAGE = 0x1000

# lane block base -> is_clock_lane; order as downstream walks the lane mask
LANES = [(0x000, False), (0x700, True), (0x200, False), (0x400, False), (0x600, False)]


def main():
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, PAGE, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=PHY)
    r = lambda o: struct.unpack_from("<I", m, o)[0]
    w = lambda o, v: struct.pack_into("<I", m, o, v)

    def status():
        return [r(0x8B0 + 4 * i) for i in range(11)]

    print("before: ctrl5=%02x ctrl6=%02x status=%s" %
          (r(0x814), r(0x818), " ".join("%02x" % s for s in status())))

    # full reset, now that the lines are live
    w(0x800, 0x1)
    time.sleep(0.01)
    w(0x800, 0x0)
    time.sleep(0.01)

    w(0x814, 0xD5)          # cmn_ctrl5: 4 data lanes + clock (vendor's own value)
    w(0x818, 0x01)          # cmn_ctrl6: COMMON_PWRDN_B
    w(0x81C, 0x02)          # cmn_ctrl7: D-PHY (0x06 would be C-PHY)
    w(0x728, 0x04)          # lnck_ctrl10
    w(0x70C, 0xA5)          # lnck_ctrl3  -- v5.0.1, NOT v5.0's 0x16

    for base, is_clk in LANES:
        w(base + 0x008, SETTLE)
        w(base + 0x02C, 0x01)
        w(base + 0x034, 0x0F)
        w(base + 0x01C, 0x0A)
        w(base + 0x014, 0x60)
        w(base + 0x03C, 0xB8)
        w(base + 0x000, 0x80 if is_clk else 0x91)
        w(base + 0x004, 0x0C)
        w(base + 0x010, 0x52)
        w(base + 0x038, 0xFE)

    # unmask all eleven interrupts, downstream's production values
    for i, v in enumerate([0xFF, 0xFF, 0xFB, 0xFF, 0x7F, 0xFF,
                           0xFF, 0xEF, 0xFF, 0xFF, 0xFF]):
        w(0x800 + 4 * (11 + i), v)

    for k in range(6):
        time.sleep(0.3)
        st = status()
        print("  t+%.1fs status=%s%s" % (0.3 * (k + 1),
              " ".join("%02x" % s for s in st),
              "   *** SIGNAL ***" if any(st) else ""))

    m.close()
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
