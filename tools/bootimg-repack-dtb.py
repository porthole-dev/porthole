#!/usr/bin/env python3
# scope: generic
"""Swap the appended DTB inside an Android boot image, fixing up the header.

taimen's boot.img carries the DTB appended to the kernel (kernel_size =
len(vmlinuz) + len(dtb)), and the DTB ends exactly at the kernel region
boundary -- so a larger DTB cannot be spliced in place. This rebuilds the image
instead: it reuses the *existing* kernel bytes (so the kernel is guaranteed
identical to the one whose modules are installed), appends the new DTB, and
recomputes kernel_size and the header id[] SHA1.

The id[] is SHA1 over (kernel, kernel_size, ramdisk, ramdisk_size, second,
second_size) -- verified byte-for-byte against the unmodified image before
relying on it.

With --kernel the vmlinuz is replaced too, which is what a change to built-in
code (CONFIG_ARM_SMMU=y, say) needs -- a `make modules` cannot carry it and the
pmbootstrap export is a much heavier step. The installed modules stay usable
because vermagic is "6.0.0 SMP preempt ..."; the build counter (#12 vs #11)
lives in UTS_VERSION and is not part of it.

Usage: repack-dtb.py IN.img NEW.dtb OUT.img [--kernel Image.gz]
"""
import argparse
import hashlib
import struct
import sys

DTB_MAGIC = b'\xd0\x0d\xfe\xed'


def pad_to(n, page):
    return (n + page - 1) // page * page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dtb_path")
    ap.add_argument("out")
    ap.add_argument("--kernel", default=None,
                    help="replace the vmlinuz as well (e.g. .output/arch/arm64/boot/Image.gz)")
    args = ap.parse_args()
    src, dtb_path, out = args.src, args.dtb_path, args.out

    d = bytearray(open(src, 'rb').read())
    if d[:8] != b'ANDROID!':
        sys.exit("not an Android boot image")

    ks, ka, rs, ra, ss, sa, tags, pgsz = struct.unpack_from('<8I', d, 8)

    ko = pgsz
    ro = ko + pad_to(ks, pgsz)
    so = ro + pad_to(rs, pgsz)
    kernel = bytes(d[ko:ko + ks])
    ramdisk = bytes(d[ro:ro + rs])
    second = bytes(d[so:so + ss])

    # Split the appended DTB off the kernel image.
    off = kernel.rfind(DTB_MAGIC)
    if off < 0:
        sys.exit("no appended DTB found in the kernel region")
    old_dtb_len = struct.unpack_from('>I', kernel, off + 4)[0]
    if off + old_dtb_len != len(kernel):
        print("warning: appended DTB does not end at the kernel boundary "
              "(off=%d len=%d kernel=%d)" % (off, old_dtb_len, len(kernel)))
    vmlinuz = kernel[:off]
    old_vmlinuz_len = len(vmlinuz)

    if args.kernel:
        new_vmlinuz = open(args.kernel, 'rb').read()
        if new_vmlinuz[:4] == DTB_MAGIC:
            sys.exit("%s looks like a DTB, not a kernel" % args.kernel)
        if new_vmlinuz.rfind(DTB_MAGIC) >= 0:
            print("warning: %s already contains a DTB magic; appending anyway"
                  % args.kernel)
        vmlinuz = new_vmlinuz

    new_dtb = open(dtb_path, 'rb').read()
    if new_dtb[:4] != DTB_MAGIC:
        sys.exit("%s is not a DTB" % dtb_path)
    print("vmlinuz %d -> %d bytes, dtb %d -> %d bytes" %
          (old_vmlinuz_len, len(vmlinuz), old_dtb_len, len(new_dtb)))

    new_kernel = vmlinuz + new_dtb
    new_ks = len(new_kernel)

    struct.pack_into('<I', d, 8, new_ks)

    h = hashlib.sha1()
    for blob, size in ((new_kernel, new_ks), (ramdisk, rs), (second, ss)):
        h.update(blob)
        h.update(struct.pack('<I', size))
    d[576:576 + 32] = h.digest().ljust(32, b'\0')

    img = bytes(d[:pgsz])
    img += new_kernel.ljust(pad_to(new_ks, pgsz), b'\0')
    img += ramdisk.ljust(pad_to(rs, pgsz), b'\0')
    if ss:
        img += second.ljust(pad_to(ss, pgsz), b'\0')

    open(out, 'wb').write(img)
    print("wrote %s (%d bytes, kernel_size %d -> %d)" %
          (out, len(img), ks, new_ks))

    # Re-read and re-verify rather than trusting the buffer.
    v = open(out, 'rb').read()
    vks = struct.unpack_from('<I', v, 8)[0]
    vko = pgsz
    vro = vko + pad_to(vks, pgsz)
    hv = hashlib.sha1()
    hv.update(v[vko:vko + vks]); hv.update(struct.pack('<I', vks))
    hv.update(v[vro:vro + rs]);  hv.update(struct.pack('<I', rs))
    hv.update(b'');              hv.update(struct.pack('<I', ss))
    if hv.digest() != v[576:576 + 20]:
        sys.exit("VERIFY FAILED: id[] does not match the repacked payload")
    if v[vko:vko + vks][-len(new_dtb):] != new_dtb:
        sys.exit("VERIFY FAILED: new DTB is not at the end of the kernel")
    if v[vko:vko + len(vmlinuz)] != vmlinuz:
        sys.exit("VERIFY FAILED: vmlinuz is not at the start of the kernel")
    print("verified: id[] matches, vmlinuz and DTB both in place")


if __name__ == '__main__':
    main()
