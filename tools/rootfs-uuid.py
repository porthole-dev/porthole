#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
"""Read the filesystem UUIDs out of a pmOS rootfs image, without mounting it.

Why: boot.img hard-codes pmos_boot_uuid= / pmos_root_uuid= in its cmdline. If
`pmbootstrap install` remints those but you flash only boot, the initramfs hunts
for a filesystem that is not on disk and drops you back into the initramfs. This
lets you compare the two before flashing instead of finding out on the device.

Parses the GPT and reads each partition's ext4 superblock directly.

Usage:
  rootfs-uuid.py IMAGE                      # print the UUIDs it contains
  rootfs-uuid.py IMAGE --bootimg BOOT_IMG   # and check they match the cmdline
"""
import argparse
import struct
import sys
import uuid

EXT_MAGIC_OFF = 0x38      # within the 1024-byte superblock
EXT_UUID_OFF = 0x68
EXT_LABEL_OFF = 0x78


def gpt_partitions(data):
    """Yield (index, name, start_byte, size_byte). Tries 512 then 4096 sectors."""
    for ss in (512, 4096):
        if data[ss:ss + 8] != b'EFI PART':
            continue
        first_lba, num, entsz = struct.unpack_from('<QII', data, ss + 72)
        base = first_lba * ss
        out = []
        for i in range(num):
            e = data[base + i * entsz: base + (i + 1) * entsz]
            if len(e) < 56 or e[:16] == b'\0' * 16:
                continue
            start, end = struct.unpack_from('<QQ', e, 32)
            name = e[56:128].decode('utf-16-le').rstrip('\0')
            out.append((i + 1, name, start * ss, (end - start + 1) * ss))
        if out:
            return ss, out
    return None, []


def ext_uuid(data, off):
    sb = data[off + 1024: off + 2048]
    if len(sb) < 0x80 or struct.unpack_from('<H', sb, EXT_MAGIC_OFF)[0] != 0xEF53:
        return None, None
    u = str(uuid.UUID(bytes=sb[EXT_UUID_OFF:EXT_UUID_OFF + 16]))
    label = sb[EXT_LABEL_OFF:EXT_LABEL_OFF + 16].rstrip(b'\0').decode('ascii', 'replace')
    return u, label


def bootimg_uuids(path):
    d = open(path, 'rb').read()
    if d[:8] != b'ANDROID!':
        sys.exit("%s: not an Android boot image" % path)
    cmd = (d[64:64 + 512].split(b'\0')[0] + d[608:608 + 1024].split(b'\0')[0]).decode()
    got = {}
    for tok in cmd.split():
        for key in ('pmos_boot_uuid', 'pmos_root_uuid'):
            if tok.startswith(key + '='):
                got[key] = tok.split('=', 1)[1]
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--bootimg')
    a = ap.parse_args()

    data = open(a.image, 'rb').read()
    ss, parts = gpt_partitions(data)
    if not parts:
        sys.exit("%s: no GPT found (is this the combined rootfs image?)" % a.image)

    print("%s  (GPT, %d-byte sectors)" % (a.image, ss))
    found = {}
    for idx, name, off, size in parts:
        u, label = ext_uuid(data, off)
        print("  p%d %-10s %8.1f MiB  %s%s"
              % (idx, name or '-', size / 1048576, u or '(not ext4)',
                 "  label=%s" % label if label else ""))
        if u:
            found[idx] = u

    if not a.bootimg:
        return 0

    want = bootimg_uuids(a.bootimg)
    print("\n%s cmdline:" % a.bootimg)
    for k, v in want.items():
        print("  %s=%s" % (k, v))

    have = set(found.values())
    missing = [k for k, v in want.items() if v not in have]
    print()
    if not want:
        print("INCONCLUSIVE - boot.img carries no pmos_*_uuid")
        return 0
    if missing:
        print("MISMATCH - not present in the image: %s" % ", ".join(missing))
        print("You must flash the ROOTFS as well as boot, or the initramfs will")
        print("not find root and will drop to a shell.")
        return 1
    print("MATCH - every UUID the cmdline wants exists in this image.")
    print("Flashing boot alone is safe, PROVIDED this same image is on the device.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
