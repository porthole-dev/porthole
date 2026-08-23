#!/usr/bin/env python3
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok
"""Read Easel's MIPI RX/TOP state through BAR2 -- a third, independent
receiver's opinion on whether the sensor is transmitting (HANDOFF §41g).

The SoC's CSIPHY sits behind Easel's TX. Easel's RX sits directly on the
sensor's lanes, so PHY_RX / INT_ST_PHY here answer "is the sensor putting
anything on the wires" without a scope.

Requires easel-mipi loaded (BARs assigned, window at 0x04000000).
Run ON the phone, as root.
"""
import mmap
import os
import struct
import sys

DEV = "/sys/bus/pci/devices/0000:01:00.0/"
PERIPH = 0x04000000
RX_BASE = {0: 0x04012000, 1: 0x04013000, 2: 0x04014000}
TX_BASE = {0: 0x04010000, 1: 0x04011000}
TOP = 0x04015000

RX_REGS = [("N_LANES", 0x04), ("CSI2_RESETN", 0x08), ("INT_ST_MAIN", 0x0c),
           ("PHY_SHUTDOWNZ", 0x40), ("DPHY_RSTZ", 0x44), ("PHY_RX", 0x48),
           ("INT_ST_PHY_FATAL", 0xe0), ("INT_ST_PHY", 0x110)]
TOP_REGS = [("TX0_MODE", 0x00), ("TX0_DPHY_CFG", 0x18), ("TX1_MODE", 0x28),
            ("TX1_DPHY_CFG", 0x40), ("RX0_MODE", 0x50), ("RX0_DPHY_CFG", 0x64),
            ("RX1_MODE", 0x74), ("RX1_DPHY_CFG", 0x78), ("RX2_MODE", 0x88),
            ("CSI_CLK_CTRL", 0x104)]
TX_REGS = [("CSI2_RESETN", 0x04), ("PHY_RSTZ", 0xe0), ("PHY_IF_CFG", 0xe4),
           ("LPCLK_CTRL", 0xe8)]


def main():
    fd = os.open(DEV + "resource2", os.O_RDWR | os.O_SYNC)
    size = os.path.getsize(DEV + "resource2")
    m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ)

    def rd(addr):
        return struct.unpack_from("<I", m, addr - PERIPH)[0]

    print("=== MIPI TOP ===")
    for name, off in TOP_REGS:
        print("  %-14s = 0x%08x" % (name, rd(TOP + off)))

    for dev in (int(sys.argv[1]) if len(sys.argv) > 1 else 1,):
        print("=== MIPI RX%d ===" % dev)
        for name, off in RX_REGS:
            print("  %-18s = 0x%08x" % (name, rd(RX_BASE[dev] + off)))
        print("=== MIPI TX%d ===" % dev)
        for name, off in TX_REGS:
            print("  %-18s = 0x%08x" % (name, rd(TX_BASE[dev] + off)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
