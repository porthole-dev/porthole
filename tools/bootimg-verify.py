#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 32 see source
"""Verify a boot.img actually carries the kernel/DTB you just built.

This exists because the failure it catches is invisible: `pmbootstrap build
--envkernel` aborts on a stale umount (exit 32) *after* writing the .apk but
*before* refreshing APKINDEX, so `install` resolves the kernel from a day-old
index and silently packs an old vmlinuz behind a fresh boot.img mtime. Every
timestamp looks right. Only the contents disagree.

Compares the DTB appended to the boot image against the one in .output, and the
packaged kernel config against .output/.config.

Usage:
  bootimg-verify.py BOOT_IMG --dtb BUILT.dtb [--config PACKAGED_CONFIG --ref-config .output/.config]

Exit status is 0 only when every requested check passes, so it can gate a flash.
"""
import argparse
import hashlib
import struct
import sys
import zlib


def appended_dtb(path):
    d = open(path, 'rb').read()
    if d[:8] != b'ANDROID!':
        sys.exit("%s: not an Android boot image" % path)
    ks, _ka, _rs, _ra, _ss, _sa, _tags, pgsz = struct.unpack_from('<IIIIIIII', d, 8)
    kernel = d[pgsz:pgsz + ks]
    if kernel[:2] != b'\x1f\x8b':
        sys.exit("%s: kernel is not gzip; cannot locate appended DTB" % path)
    o = zlib.decompressobj(16 + zlib.MAX_WBITS)
    o.decompress(kernel)
    dtb = o.unused_data
    if dtb[:4] != b'\xd0\x0d\xfe\xed':
        sys.exit("%s: no appended DTB after the gzip payload" % path)
    return dtb


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bootimg')
    ap.add_argument('--dtb', required=True, help="the .dtb make just produced")
    ap.add_argument('--config', help="config shipped inside the rootfs chroot's /boot")
    ap.add_argument('--ref-config', help=".output/.config to compare it against")
    a = ap.parse_args()

    ok = True

    packed = appended_dtb(a.bootimg)
    built = open(a.dtb, 'rb').read()
    if sha(packed) == sha(built):
        print("PASS  DTB matches (%d bytes, %s)" % (len(packed), sha(packed)[:16]))
    else:
        ok = False
        print("FAIL  DTB MISMATCH -- the boot.img does not contain the DTB you built")
        print("        in boot.img : %d bytes  %s" % (len(packed), sha(packed)[:16]))
        print("        just built  : %d bytes  %s" % (len(built), sha(built)[:16]))
        print("      Almost always the stale-APKINDEX bug: the apk exists but is not")
        print("      indexed, so install picked an older one. Re-run the build so that")
        print("      `pmbootstrap index` runs CHAINED to `build --envkernel` (the chroot")
        print("      must still be mounted; a standalone index after shutdown fails with")
        print("      'No private key found').")

    if a.config and a.ref_config:
        got = open(a.config, 'rb').read()
        ref = open(a.ref_config, 'rb').read()
        if sha(got) == sha(ref):
            print("PASS  packaged config matches .output/.config")
        else:
            ok = False
            print("FAIL  packaged config differs from .output/.config -- same stale-apk cause")

    print("\n%s" % ("ALL CHECKS PASSED - safe to flash" if ok
                    else "DO NOT FLASH - the image is not what you built"))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
