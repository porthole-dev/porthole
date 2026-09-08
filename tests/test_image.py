#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The rootless image assembler.

pmbootstrap builds the rootfs image by attaching a loop device, letting the
kernel expose a partition table, and running mkfs on the partition nodes. A
rootless user namespace cannot have a loop device, so the workspace passed
--no-image -- and `pmbootstrap install --no-image` returns BEFORE
create_fstab() and mkinitfs(), so the chroot got no fstab and the initramfs
never learned this install's UUIDs.

porthole assembles the image instead, and CHOOSES the UUIDs rather than
reading them back out of a filesystem it just mounted. That is the important
half: a chosen number can be written into both the fstab and the cmdline, so
the two cannot disagree. Reading it back is why a stale rootfs and a fresh
boot.img could silently disagree and drop the phone into an initramfs debug
shell.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

import porthole_image as image  # noqa: E402

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                     r"[0-9a-f]{4}-[0-9a-f]{12}$")


def test_sizes_follow_pmbootstraps_own_formula():
    """get_subpartitions_size: root = folder_size * 1.20 + 50 + extra. Copied
    deliberately -- an image sized by a different rule than the one that has
    shipped for years is a new failure mode nobody has seen."""
    boot, root = image.sizes(chroot_bytes=1000 * 1024 * 1024)
    assert boot == 512
    assert root == round(1000 * 1.20 + 50)


def test_extra_space_is_added_after_the_multiplier_not_before():
    _, a = image.sizes(chroot_bytes=1000 * 1024 * 1024, extra_mb=100)
    _, b = image.sizes(chroot_bytes=1000 * 1024 * 1024, extra_mb=0)
    assert a - b == 100


def test_uuids_are_well_formed_and_distinct():
    boot, root = image.uuids()
    assert UUID_RE.match(boot) and UUID_RE.match(root)
    assert boot != root


def test_a_seed_makes_the_uuids_reproducible():
    """So a build can be re-run and produce the same image, and so a test can
    assert on an exact value."""
    assert image.uuids(seed="taimen-1") == image.uuids(seed="taimen-1")
    assert image.uuids(seed="taimen-1") != image.uuids(seed="taimen-2")


def test_the_fstab_names_the_uuids_we_chose():
    fs = image.fstab("11111111-2222-3333-4444-555555555555",
                     "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert "UUID=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee / ext4" in fs
    assert "UUID=11111111-2222-3333-4444-555555555555 /boot ext4" in fs
    assert "nodev,nosuid,noexec" in fs, "boot keeps pmbootstrap's own options"


def test_the_layout_places_root_immediately_after_boot():
    lay = image.layout(boot_mb=32, root_mb=64, arch="aarch64")
    assert lay["boot_start"] == 2048
    assert lay["boot_sectors"] == 32 * 1024 * 1024 // 512
    assert lay["root_start"] == lay["boot_start"] + lay["boot_sectors"]


def test_the_total_is_4096_aligned_because_img2simg_asserts_otherwise():
    """Measured 2026-09-08: an unaligned image aborts img2simg with
    `Assertion failed: pad >= 0`. taimen's rootfs_image_sector_size=4096
    requires the same alignment independently."""
    lay = image.layout(boot_mb=32, root_mb=64, arch="aarch64")
    assert lay["total_bytes"] % image.ALIGN == 0


def test_the_root_partition_type_is_the_arch_specific_dps_guid():
    """b921b045 is SD_GPT_ROOT_ARM64 in pmb/core/dps.py. Writing the generic
    Linux GUID instead would boot but stops systemd's discoverable-partition
    logic recognising it."""
    assert image.layout(1, 1, "aarch64")["root_type"].lower().startswith("b921b045")
    assert image.layout(1, 1, "x86_64")["root_type"].lower().startswith("4f68bce3")


def test_an_unknown_arch_is_refused_rather_than_defaulted():
    """Guessing a partition GUID writes a table that looks right and is not."""
    try:
        image.layout(1, 1, "sparc64")
        assert False, "an unknown arch was silently given some GUID"
    except KeyError as exc:
        assert "sparc64" in str(exc)


def test_the_sfdisk_script_declares_gpt_and_both_partitions():
    script = image.sfdisk_script(image.layout(32, 64, "aarch64"))
    assert script.splitlines()[0] == "label: gpt"
    assert script.count("start=") == 2
    assert "pmOS_boot" in script and "pmOS_root" in script


def test_a_layout_the_assembler_does_not_cover_is_refused_by_name():
    """Spec §6.5. The assembler covers plain boot+root. A device wanting
    full-disk encryption, btrfs subvolumes, a ChromeOS cgpt kernel partition
    or a PReP boot partition must go to the host -- and must be TOLD so,
    because an assembler that silently ignored `full_disk_encryption` would
    ship an unencrypted rootfs to someone who asked for an encrypted one."""
    for unsupported in ({"full_disk_encryption": True},
                        {"filesystem": "btrfs"},
                        {"cgpt_kpart_start": "8192"},
                        {"create_prep_boot": True}):
        try:
            image.layout(32, 64, "aarch64", **unsupported)
            assert False, f"{unsupported} was silently accepted"
        except image.Unsupported as exc:
            assert "--host" in str(exc), "the refusal must name the way out"


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
