#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assemble a flashable rootfs image without a loop device.

WHAT THIS REPLACES
    Exactly three pmbootstrap steps -- blockdevice.create(), partition() and
    format(). Everything else install does (populating the chroot, apk, keys,
    services, mkinitfs) stays pmbootstrap's. This is not a fork of the
    installer; it is the three steps that need a block device.

WHY IT CAN BE DONE ROOTLESS
    The loop device is not being used to mount a filesystem. It is used to
    expose a PARTITIONED DISK so the kernel creates partition nodes that mkfs
    and mount can consume (see
    brain/findings/fuse2fs-cannot-replace-the-loop-device.md). Nothing needs
    that if the filesystems are built as files and placed at their offsets:
    `mkfs.ext4 -d` populates a filesystem from a directory with no mount at
    all, and `sfdisk` writes a partition table to a regular file.

    Verified in the workspace on 2026-09-08: ownership, setuid,
    non-root uids, security.capability and security.selinux all survive
    `mkfs.ext4 -d`.

WHY porthole CHOOSES THE UUIDs
    pmbootstrap reads them back with blkid from a filesystem it has just
    mounted, and writes them into the fstab and (via mkinitfs) the kernel
    cmdline. That makes "the image on the phone" and "the boot.img that names
    its UUIDs" two independently-derived facts which can disagree -- and when
    they do the phone comes up in the initramfs hunting for a root that is not
    there, burning a slot retry on each attempt. Choosing the number and
    writing both sides makes the disagreement impossible rather than detected.
"""
from __future__ import annotations

import hashlib
import uuid as _uuid

# img2simg asserts `pad >= 0` on an image whose length is not a multiple of
# the sparse block size; measured 2026-09-08. taimen's
# deviceinfo_rootfs_image_sector_size=4096 wants the same alignment anyway.
ALIGN = 4096

SECTOR = 512

# From pmb/core/dps.py, which follows the Discoverable Partitions Spec. Copied
# rather than imported: pmbootstrap is an external checkout and porthole must
# build an image on a host that has only the workspace.
ESP_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
ROOT_GUID = {
    "aarch64": "B921B045-1DF0-41C3-AF44-4C6F280D3FAE",
    "armv7": "69DAD710-2CE4-4E3C-B16C-21A1D49ABED3",
    "armhf": "69DAD710-2CE4-4E3C-B16C-21A1D49ABED3",
    "x86_64": "4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709",
    "x86": "44479540-F297-41B2-9AF7-D131D5F0458A",
    "riscv64": "72EC70A6-CF74-40E6-BD49-4BDA08E8F224",
}

BOOT_LABEL, ROOT_LABEL = "pmOS_boot", "pmOS_root"


def sizes(chroot_bytes: int, boot_mb: int = 512, extra_mb: int = 0):
    """(boot_mb, root_mb). pmbootstrap's get_subpartitions_size, verbatim.

    The 1.20 multiplier and the +50 are theirs: "the size calculation is not
    as trivial as one may think, and depending on the file system etc it seems
    to be just impossible to get it right". Reproducing the formula rather
    than inventing one keeps images the same size they have always been.
    """
    root_mb = chroot_bytes / (1024 * 1024)
    root_mb *= 1.20
    root_mb += 50 + extra_mb
    return boot_mb, round(root_mb)


def uuids(seed: str | None = None):
    """(boot_uuid, root_uuid), chosen here rather than read back.

    `seed` makes them reproducible, so re-running a build produces the same
    image and a test can assert an exact value. Without one they are random,
    which is what a real install wants: a fresh filesystem should not inherit
    the identity of the one it replaces.
    """
    if seed is None:
        return str(_uuid.uuid4()), str(_uuid.uuid4())
    digest = hashlib.sha256(seed.encode()).digest()
    return (str(_uuid.UUID(bytes=digest[:16], version=4)),
            str(_uuid.UUID(bytes=digest[16:32], version=4)))


def fstab(boot_uuid: str, root_uuid: str, root_fs: str = "ext4",
          boot_fs: str = "ext4") -> str:
    """The fstab pmbootstrap's create_fstab would have written.

    Same shape and the same mount options, because mkinitfs reads this to
    build the cmdline and postmarketos-base expects what it has always seen.
    """
    lines = ["# <file system> <mount point> <type> <options> <dump> <pass>",
             f"UUID={root_uuid} / {root_fs} defaults 0 0"]
    options = "nodev,nosuid,noexec"
    if boot_fs in ("fat16", "fat32"):
        boot_fs = "vfat"
        options += ",umask=0077,nosymfollow,codepage=437,iocharset=ascii"
    lines.append(f"UUID={boot_uuid} /boot {boot_fs} {options} 0 0")
    return "\n".join(lines) + "\n"


class Unsupported(Exception):
    """A layout this assembler does not build, named rather than approximated.

    The failure mode being avoided is silence: an assembler that ignored
    `full_disk_encryption` would hand someone who asked for an encrypted
    rootfs an unencrypted one, and nothing would say so until the phone came
    up without asking for a passphrase.
    """


# Layouts pmbootstrap builds and this assembler deliberately does not. taimen
# needs none of them; a device that does uses the host path, where pmbootstrap
# remains the producer (spec §6.5).
_UNSUPPORTED = {
    "full_disk_encryption": "full-disk encryption needs cryptsetup on a block "
                            "device",
    "cgpt_kpart_start": "ChromeOS cgpt layouts do not follow the discoverable "
                        "partitions spec",
    "create_prep_boot": "a PReP boot partition needs grub-install against a "
                        "real block device",
}


def layout(boot_mb: int, root_mb: int, arch: str, start_sector: int = 2048,
           filesystem: str = "ext4", **device):
    """Where each partition sits in the assembled disk. Pure.

    2048 is pmbootstrap's own default boot_part_start, and a device that needs
    another value sets deviceinfo_boot_part_start -- which reaches here as
    `start_sector` rather than being guessed.
    """
    for key, why in _UNSUPPORTED.items():
        if device.get(key):
            raise Unsupported(
                f"porthole cannot assemble this image: {why}. Build it on the "
                f"host with --host, where pmbootstrap makes it the way it "
                f"always has")
    if filesystem == "btrfs":
        raise Unsupported(
            "porthole assembles ext4 only; btrfs wants subvolumes created "
            "through a mount. Build it on the host with --host")
    root_type = ROOT_GUID[arch]          # KeyError, deliberately: guessing a
                                         # GUID writes a table that looks right
    boot_sectors = boot_mb * 1024 * 1024 // SECTOR
    root_sectors = root_mb * 1024 * 1024 // SECTOR
    root_start = start_sector + boot_sectors
    # +34 sectors for the secondary GPT (32 table + 1 header, +1 slack).
    end = root_start + root_sectors + 34
    total = ((end * SECTOR) + ALIGN - 1) // ALIGN * ALIGN
    return {"boot_start": start_sector, "boot_sectors": boot_sectors,
            "root_start": root_start, "root_sectors": root_sectors,
            "total_bytes": total, "boot_type": ESP_GUID,
            "root_type": root_type}


def sfdisk_script(lay: dict) -> str:
    """The script `sfdisk <image>` consumes. It writes a table to a plain
    file happily; only READING one back needs a block device."""
    return (
        "label: gpt\n"
        f"start={lay['boot_start']}, size={lay['boot_sectors']}, "
        f"type={lay['boot_type']}, name={BOOT_LABEL}\n"
        f"start={lay['root_start']}, size={lay['root_sectors']}, "
        f"type={lay['root_type']}, name={ROOT_LABEL}\n")


def mkfs_argv(out, size_mb: int, label: str, uuid: str, source_dir,
              inode_ratio: int = 8192) -> list:
    """`mkfs.ext4 -d` populates a filesystem from a directory with no mount.

    -F because the target is a file rather than a block device, -q because
    the build's own progress line is the output that matters.

    inode_ratio defaults to 8192, not 0: pmb#2568 -- without `-i 8192` an
    install runs out of inodes and reports it as out of space, and that has
    to hold for a caller that does not think to pass it, not just the one
    that remembers to.
    """
    argv = ["mkfs.ext4", "-F", "-q", "-L", label, "-U", uuid]
    if inode_ratio:
        argv += ["-i", str(inode_ratio)]
    argv += ["-d", str(source_dir), str(out), f"{size_mb}M"]
    return argv


def assemble(runner, boot_dir, root_dir, out, lay: dict, boot_uuid,
             root_uuid) -> None:
    """Build the two filesystems and place them in a partitioned disk.

    ORDER IS LOAD-BEARING. The table is written first: `dd conv=notrunc`
    into a file `sfdisk` has not touched yet leaves a disk with no table and
    contents that look perfectly fine.

    conv=notrunc on every dd is equally load-bearing: dd's default is to
    truncate its OUTPUT file to the length of what it just wrote, so a
    second dd without it would cut the disk down to the tail partition's own
    length and erase the one written before it.

    Sizes are always derived from `lay`, not taken from a caller: sfdisk
    already carved out `boot_sectors`/`root_sectors` for this partition, and
    a filesystem built to any other size either overflows its slot or
    leaves it partly empty -- both silent.
    """
    boot_mb = lay["boot_sectors"] * SECTOR // (1024 * 1024)
    root_mb = lay["root_sectors"] * SECTOR // (1024 * 1024)
    boot_img, root_img = f"{out}.boot", f"{out}.root"

    runner(mkfs_argv(boot_img, boot_mb, BOOT_LABEL, boot_uuid, boot_dir))
    runner(mkfs_argv(root_img, root_mb, ROOT_LABEL, root_uuid, root_dir,
                     inode_ratio=8192))
    runner(["truncate", "-s", str(lay["total_bytes"]), str(out)])
    runner(["sfdisk", "-q", str(out)], stdin=sfdisk_script(lay))
    for src, start in ((boot_img, lay["boot_start"]),
                       (root_img, lay["root_start"])):
        runner(["dd", f"if={src}", f"of={out}", f"bs={SECTOR}",
                f"seek={start}", "conv=notrunc", "status=none"])
    runner(["rm", "-f", boot_img, root_img])


_UUID_LINE = "Filesystem UUID:"
_LABEL_LINE = "Filesystem volume name:"


def verify(runner, out, lay: dict, boot_uuid, root_uuid, user=None) -> list:
    """Read each partition back out of the assembled disk and check it.

    Offline, before anything is written to a phone, because flash_rootfs
    writes hundreds of MB that is not undoable and a refusal after it has
    run leaves a half-flashed device.

    Extracted with dd rather than read through a loop device, so
    verification works in exactly the place assembly does -- no root, no
    loop device, same as the assembler that produced the image.

    `user`, when given, checks the root partition for the two things that
    made an assembled image install and boot and then be UNREACHABLE: no
    `~<user>/.ssh/authorized_keys` (nothing to log in with) and a surviving
    `/in-pmbootstrap` marker (the device would misdetect itself as a build
    chroot). Found on hardware on 2026-09-08 -- this is the difference
    between catching it here, in seconds, and catching it after a 20-minute
    flash.
    """
    problems = []
    for name, start, sectors, want_uuid, want_label in (
            ("boot", lay["boot_start"], lay["boot_sectors"], boot_uuid,
             BOOT_LABEL),
            ("root", lay["root_start"], lay["root_sectors"], root_uuid,
             ROOT_LABEL)):
        part = f"{out}.verify-{name}"
        runner(["dd", f"if={out}", f"of={part}", f"bs={SECTOR}",
                f"skip={start}", f"count={sectors}", "status=none"])
        text = runner(["dumpe2fs", "-h", part])
        got_uuid = _field(text, _UUID_LINE)
        got_label = _field(text, _LABEL_LINE)
        if got_uuid.lower() != str(want_uuid).lower():
            problems.append(
                f"{name}: the filesystem carries UUID {got_uuid or '(none)'} "
                f"but the fstab and cmdline name {want_uuid} -- the phone "
                f"would come up in the initramfs hunting for a root that is "
                f"not there")
        if got_label != want_label:
            problems.append(
                f"{name}: label is {got_label or '(none)'}, expected "
                f"{want_label}")
        if name == "root" and user:
            # `ls -l`, not `stat`: debugfs sends a nonexistent `stat` target's
            # "File not found by ext2_lookup" to STDERR, which this runner's
            # contract (stdout only, on a nonzero-exit debugfs never
            # produces) never sees -- silently, since debugfs itself still
            # exits 0. `ls -l` puts its listing on stdout regardless, so
            # presence is read off the listing directly rather than off an
            # error message that might not even be visible.
            ssh_ls = runner(["debugfs", "-R",
                             f"ls -l /home/{user}/.ssh", part])
            if "authorized_keys" not in _names(ssh_ls):
                problems.append(
                    f"root: no authorized_keys for {user} -- the phone "
                    f"would install and boot with no way to log in")
            root_ls = runner(["debugfs", "-R", "ls -l /", part])
            if "in-pmbootstrap" in _names(root_ls):
                problems.append(
                    "root: /in-pmbootstrap is still there -- pmbootstrap on "
                    "the device would misdetect itself as a build chroot")
        runner(["rm", "-f", part])
    return problems


def _field(text: str, prefix: str) -> str:
    for line in (text or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _names(ls_output: str) -> set:
    """Entry names out of `debugfs -R "ls -l ..."` output. The name is the
    last whitespace-separated field of each line; an empty or error-only
    listing (directory does not exist) yields an empty set, same as an
    empty directory would -- both correctly read as "not present"."""
    return {line.split()[-1] for line in (ls_output or "").splitlines()
            if line.split()}
