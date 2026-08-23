#!/usr/bin/env python3
# scope: device:google-taimen
"""Does the VFE write ANY byte to the capture buffer?

Nothing measured so far separates "the write master never issues an AXI
transaction" from "it issues them and they are discarded somewhere". The
distinction matters: the first is a VFE configuration problem, the second is a
bus/SMMU problem, and they have nothing in common.

This drives V4L2 directly instead of via v4l2-ctl, because v4l2-ctl only ever
shows you a buffer it managed to DEQUEUE -- and on this device no buffer ever
completes, so it always reports 0 bytes and tells you nothing about the memory.
Here the buffers are mmap'd, poisoned with a known pattern, queued, streamed,
and then read back WITHOUT dequeuing. Any byte that is no longer the poison
value was written by the hardware.

Run ON the phone, as root, after the media links and formats are set up.
"""
import ctypes, fcntl, mmap, os, struct, sys, time

VIDEO = sys.argv[1] if len(sys.argv) > 1 else "/dev/video0"
NBUF = 4
POISON = 0xA5
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

TYPE_MPLANE = 9
MEMORY_MMAP = 1

VIDIOC_REQBUFS = 0xC0145608
VIDIOC_QUERYBUF = 0xC0585609
VIDIOC_QBUF = 0xC058560F
VIDIOC_STREAMON = 0x40045612
VIDIOC_STREAMOFF = 0x40045613

BUF_SIZE = 88          # struct v4l2_buffer on 64-bit
PLANE_SIZE = 64        # struct v4l2_plane on 64-bit


def buf_pack(index, nplanes, planes_ptr):
    b = bytearray(BUF_SIZE)
    struct.pack_into("<II", b, 0, index, TYPE_MPLANE)
    struct.pack_into("<I", b, 60, MEMORY_MMAP)
    struct.pack_into("<Q", b, 64, planes_ptr)
    struct.pack_into("<I", b, 72, nplanes)
    return b


def main():
    fd = os.open(VIDEO, os.O_RDWR)

    req = bytearray(struct.pack("<IIIIB3x", NBUF, TYPE_MPLANE, MEMORY_MMAP, 0, 0))
    fcntl.ioctl(fd, VIDIOC_REQBUFS, req)
    count = struct.unpack_from("<I", req, 0)[0]
    print("reqbufs -> %d buffers" % count)

    maps = []
    for i in range(count):
        planes = ctypes.create_string_buffer(PLANE_SIZE)
        b = buf_pack(i, 1, ctypes.addressof(planes))
        bb = bytearray(b)
        fcntl.ioctl(fd, VIDIOC_QUERYBUF, bb)
        length, offset = struct.unpack_from("<II", planes.raw, 4)[0], \
            struct.unpack_from("<I", planes.raw, 8)[0]
        length = struct.unpack_from("<I", planes.raw, 4)[0]
        mm = mmap.mmap(fd, length, mmap.MAP_SHARED,
                       mmap.PROT_READ | mmap.PROT_WRITE, offset=offset)
        mm[:] = bytes([POISON]) * length
        maps.append((mm, length))
        print("  buf%d len=%d offset=0x%x poisoned with 0x%02x" %
              (i, length, offset, POISON))

    for i in range(count):
        planes = ctypes.create_string_buffer(PLANE_SIZE)
        bb = bytearray(buf_pack(i, 1, ctypes.addressof(planes)))
        fcntl.ioctl(fd, VIDIOC_QBUF, bb)

    # Scan at THREE points, because "bytes changed" alone cannot tell hardware
    # writes from cache maintenance:
    #
    #   after QBUF    -- vb2_dma_sg_prepare() has done sync_for_device (a clean
    #                    on arm64), so the poison must still be there.
    #   before STREAMOFF -- only the hardware can have changed anything by now.
    #   after STREAMOFF  -- __vb2_queue_cancel() runs vb2_dma_sg_finish(), i.e.
    #                    sync_for_cpu, which on arm64 INVALIDATES. Any dirty
    #                    poison line still in cache is discarded there, exposing
    #                    the zeroed page underneath.
    #
    # So a buffer that is poison before STREAMOFF and changed after it was never
    # touched by the VFE -- that difference is the whole experiment.
    def scan(when):
        total = 0
        rows = []
        for i, (mm, length) in enumerate(maps):
            data = mm[:]
            changed = sum(1 for c in data if c != POISON)
            first = next((k for k, c in enumerate(data) if c != POISON), None)
            total += changed
            rows.append("  buf%d: %d/%d changed%s" %
                        (i, changed, length,
                         "" if first is None else ", first at %d: %s" %
                         (first, data[first:first + 16].hex())))
        print("[%s] %d bytes differ from poison" % (when, total))
        for r in rows:
            print(r)
        return total

    pre = scan("after QBUF, before STREAMON")
    if pre:
        print("  <-- poison did not survive buffer mapping; this run is void")

    fcntl.ioctl(fd, VIDIOC_STREAMON, struct.pack("<i", TYPE_MPLANE))
    print("streaming for %.1fs (buffers are NOT dequeued on purpose)" % SECS)
    time.sleep(SECS)

    live = scan("streaming, before STREAMOFF")

    try:
        fcntl.ioctl(fd, VIDIOC_STREAMOFF, struct.pack("<i", TYPE_MPLANE))
    except OSError as e:
        print("streamoff: %s" % e)

    post = scan("after STREAMOFF")
    for mm, _ in maps:
        mm.close()

    if live:
        print("\n*** THE VFE WROTE %d BYTES while streaming -- transactions DO "
              "reach memory ***" % live)
    elif post:
        print("\nARTEFACT: nothing changed while streaming; %d bytes changed only "
              "at STREAMOFF. That is vb2's sync_for_cpu invalidate discarding the "
              "poison, NOT the hardware." % post)
    else:
        print("\nNOTHING was written -- the write master never lands a byte "
              "in memory at all")
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
