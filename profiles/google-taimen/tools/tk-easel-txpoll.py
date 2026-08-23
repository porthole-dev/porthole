#!/usr/bin/env python3
# scope: device:google-taimen
"""Poll Easel TX PHY_STATUS to see whether the bypass forwards data bursts."""
import mmap, os, struct, sys
DEV="/sys/bus/pci/devices/0000:01:00.0/resource2"; PERIPH=0x04000000
TX={0:0x04010000,1:0x04011000}
dev=int(sys.argv[1]) if len(sys.argv)>1 else 1
n=int(sys.argv[2]) if len(sys.argv)>2 else 150000
fd=os.open(DEV,os.O_RDWR|os.O_SYNC)
m=mmap.mmap(fd,os.path.getsize(DEV),mmap.MAP_SHARED,mmap.PROT_READ)
off=TX[dev]+0x110-PERIPH
lanes=[0,0,0,0]; clk=0; seen={}
for _ in range(n):
    v=struct.unpack_from("<I",m,off)[0]
    seen[v]=seen.get(v,0)+1
    for i,b in enumerate((6,8,10,12)):
        if not (v>>b)&1: lanes[i]+=1
    if not (v>>4)&1: clk+=1
print("TX%d samples=%d  clock OUT of stop (driving HS): %d (%.1f%%)"%(dev,n,clk,100.0*clk/n))
for i in range(4):
    print("  data lane %d transmitting: %d (%.2f%%)"%(i,lanes[i],100.0*lanes[i]/n))
print("  values: %s"%" ".join("0x%x:%d"%(k,v) for k,v in sorted(seen.items())[:6]))
