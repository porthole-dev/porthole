#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
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


def cmdline(path):
    """The kernel command line packed into an Android boot image. Pure."""
    d = open(path, 'rb').read(4096)
    if d[:8] != b'ANDROID!':
        sys.exit("%s: not an Android boot image" % path)
    return d[64:64 + 512].split(b'\0')[0].decode('utf-8', 'replace')


def cmdline_uuid(text, key):
    """`pmos_root_uuid` (or boot) out of a command line, or "". Pure.

    A pure function of a string so the decision can be wrong in a test rather
    than on a phone -- which is exactly what happened before it existed.
    """
    for token in text.split():
        name, sep, value = token.partition('=')
        if sep and name == key:
            return value
    return ""


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bootimg')
    ap.add_argument('--dtb', required=True, help="the .dtb make just produced")
    ap.add_argument('--config', help="config shipped inside the rootfs chroot's /boot")
    ap.add_argument('--ref-config', help=".output/.config to compare it against")
    ap.add_argument('--expect-root-uuid', metavar='UUID',
                    help="the pmos_root_uuid the DEVICE actually has; refuse "
                         "to flash an image naming a different one")
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

    # An image whose pmos_root_uuid names a filesystem this phone does not
    # have does not boot, and does not fail in a way that mentions UUIDs: the
    # initramfs's find_partition() is deliberate about it --
    #
    #     if [ -n "$uuid" ]; then
    #         partition="$(blkid --uuid "$uuid")"
    #         if [ -z "$partition" ]; then
    #             # Don't fall back to anything if the given UUID wasn't found
    #             return
    #
    # so a wrong UUID does NOT fall back to the pmOS_root LABEL. PMOS_ROOT
    # stays empty, the loop device is detached, and after ten seconds you get
    # "ERROR: failed to mount subpartitions!" and a debug shell -- a message
    # about subpartitions for a problem that has nothing to do with them.
    #
    # This happened on 2026-09-09. `build fast` exports the boot image from the
    # WORKSPACE's rootfs chroot, and that chroot was a different install from
    # the one on the phone, so the cmdline named the workspace's rootfs:
    # image said 6023b6d7-..., the phone had e883c7c9-.... Kernel and ramdisk
    # were byte-identical in size to the working image; only the UUID differed.
    # The code above this call assumed the exported image "already names a
    # filesystem that exists", which is true only when the image and the phone
    # came from the same install, and nothing checked that they did.
    if a.expect_root_uuid:
        want = a.expect_root_uuid.strip()
        got = cmdline_uuid(cmdline(a.bootimg), 'pmos_root_uuid')
        if not got:
            print("PASS  image names no pmos_root_uuid (label fallback applies)")
        elif got == want:
            print("PASS  pmos_root_uuid matches the device (%s)" % got)
        else:
            ok = False
            print("FAIL  pmos_root_uuid MISMATCH -- this image will not boot "
                  "this phone")
            print("        in boot.img : %s" % got)
            print("        on the phone: %s" % want)
            print("      The image was built against a different rootfs install.")
            print("      It will stop in the initramfs debug shell saying")
            print("      'failed to mount subpartitions', which is NOT the real")
            print("      cause -- find_partition() refuses the pmOS_root label")
            print("      fallback when a pmos_root_uuid is given and missing.")

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
