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
    boot, root = image.uuids(seed="taimen-1")
    assert boot != root, ("a boot and root filesystem sharing a UUID is two "
                          "partitions the initramfs cannot tell apart")


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


def test_a_512_sector_size_layout_is_unchanged():
    """sector_size is a new parameter; a device that never mentions it (every
    device but taimen, today) must get exactly the geometry this module has
    always produced -- the default here IS the assertion, not a convenience."""
    lay = image.layout(boot_mb=32, root_mb=64, arch="aarch64")
    assert lay["sector_size"] == 512
    assert lay["boot_start"] == 2048
    assert lay["boot_sectors"] == 32 * 1024 * 1024 // 512
    assert lay["root_start"] == 2048 + 32 * 1024 * 1024 // 512


def test_a_4096_sector_size_layout_puts_everything_at_the_right_byte():
    """taimen sets deviceinfo_rootfs_image_sector_size=4096; its initramfs
    attaches the assembled image with `losetup -b 4096`, so LBA 1 -- the GPT
    header -- is byte 4096 there, not byte 512. Measured on hardware
    2026-09-08: a table written as though sectors were still 512 bytes put
    the header at the wrong byte and the kernel's partition scanner found
    nothing ("failed to mount subpartitions").

    256 sectors of 4096 bytes is the SAME byte position as pmbootstrap's own
    2048-sector (512-byte) default -- 1 MiB, chosen for erase-block
    alignment -- not a different number chosen for this device."""
    lay = image.layout(boot_mb=8, root_mb=32, arch="aarch64", sector_size=4096)
    assert lay["sector_size"] == 4096
    assert lay["boot_start"] == 256
    assert lay["boot_start"] * 4096 == 2048 * 512, "same byte, different unit"
    assert lay["boot_sectors"] == 8 * 1024 * 1024 // 4096
    assert lay["root_start"] == lay["boot_start"] + lay["boot_sectors"]
    assert lay["total_bytes"] % image.ALIGN == 0


def test_the_total_is_4096_aligned_because_img2simg_asserts_otherwise():
    """Measured 2026-09-08: an unaligned image aborts img2simg with
    `Assertion failed: pad >= 0`. taimen's rootfs_image_sector_size=4096
    requires the same alignment independently."""
    lay = image.layout(boot_mb=32, root_mb=64, arch="aarch64")
    assert lay["total_bytes"] % image.ALIGN == 0


def test_align_is_4096_not_a_tuning_knob():
    """Measured 2026-09-08: img2simg aborts with `Assertion failed: pad >= 0`
    on anything smaller, and taimen's deviceinfo_rootfs_image_sector_size=4096
    requires the same value independently. Nothing else pins the number."""
    assert image.ALIGN == 4096


def test_the_tail_leaves_room_for_the_backup_gpt():
    """`% ALIGN == 0` alone is tautological -- total_bytes is computed as
    `(...) // ALIGN * ALIGN`, so it holds even if that division floors
    instead of ceils. Flooring truncates into the padding the secondary GPT
    needs (32 sectors of table + 1 header) instead of rounding up past it:
    for a default-parameter layout the raw end is always 2 sectors into an
    8-sector (4096-byte) alignment unit, so flooring drops to 32 tail
    sectors -- one short -- while ceiling correctly lands on 40."""
    for boot_mb, root_mb, arch in ((32, 64, "aarch64"), (512, 1250, "x86_64")):
        lay = image.layout(boot_mb, root_mb, arch)
        tail = (lay["total_bytes"] // image.SECTOR
                 - (lay["root_start"] + lay["root_sectors"]))
        assert tail >= 33, f"only {tail} tail sectors for {(boot_mb, root_mb, arch)}"


def test_the_root_partition_type_is_the_arch_specific_dps_guid():
    """b921b045 is SD_GPT_ROOT_ARM64 in pmb/core/dps.py. Writing the generic
    Linux GUID instead would boot but stops systemd's discoverable-partition
    logic recognising it."""
    assert image.layout(1, 1, "aarch64")["root_type"].lower().startswith("b921b045")
    assert image.layout(1, 1, "x86_64")["root_type"].lower().startswith("4f68bce3")


def test_the_boot_partition_type_is_the_esp_guid():
    """The only prior GUID assertion covered root -- ESP_GUID replaced with
    zeros, or layout() returning root_type for boot_type too, both left
    every existing test green. The literal is hardcoded rather than compared
    against image.ESP_GUID: comparing a mutated constant to itself is
    tautological and catches nothing."""
    lay = image.layout(32, 64, "aarch64")
    assert lay["boot_type"] == "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
    assert lay["boot_type"] != lay["root_type"]
    script = image.sfdisk_script(lay)
    assert "type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B" in script
    assert f"type={lay['root_type']}" in script


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


class FakeRunner:
    """Records argv instead of running it, so assembly order is testable with
    no container, no e2fsprogs and no disk.

    Answers verify()'s two disk-geometry calls (sfdisk -l, the GPT-signature
    dd) truthfully by default -- echoing back whatever --sector-size was
    asked for, and a matching "EFI PART" -- so every existing test that does
    not care about sector-size geometry keeps passing those checks without
    having to know they exist. A subclass testing something else should
    fall through to `super().__call__(...)`'s return value, not hardcode
    its own "", or it silently loses these defaults too."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, stdin=""):
        self.calls.append((list(argv), stdin))
        if argv[:2] == ["sfdisk", "-l"]:
            size = argv[argv.index("--sector-size") + 1]
            return (f"Sector size (logical/physical): {size} bytes / "
                    f"{size} bytes\n")
        if argv and argv[0] == "dd" and "bs=1" in argv:
            return "EFI PART"
        return ""

    def program(self, name):
        return [c for c in self.calls if c[0] and c[0][0] == name]


def test_mkfs_is_told_the_uuid_the_label_and_the_source_directory():
    argv = image.mkfs_argv("/tmp/root.ext4", 64, image.ROOT_LABEL,
                           "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "/mnt/root")
    assert "-U" in argv and "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in argv
    assert "-L" in argv and image.ROOT_LABEL in argv
    assert "-d" in argv and "/mnt/root" in argv
    assert argv[-1] == "64M", "size is mke2fs's last argument"


def test_the_root_filesystem_asks_for_more_inodes():
    """pmb#2568: without -i 8192 an install runs out of inodes and reports it
    as out of space."""
    argv = image.mkfs_argv("/tmp/root.ext4", 64, image.ROOT_LABEL, "u", "/d")
    assert "-i" in argv and "8192" in argv


def test_assembly_writes_the_table_before_it_writes_the_filesystems():
    """dd with conv=notrunc into a file sfdisk has not written yet leaves a
    disk whose table is missing and whose contents look fine."""
    run = FakeRunner()
    lay = image.layout(32, 64, "aarch64")
    image.assemble(run, "/b", "/r", "/out.img", lay, "bu", "ru")
    names = [c[0][0] for c in run.calls]
    assert names.index("sfdisk") < names.index("dd")


def test_every_dd_carries_conv_notrunc():
    """Without it the second dd truncates the disk to its own length and the
    first partition is gone."""
    run = FakeRunner()
    image.assemble(run, "/b", "/r", "/out.img",
                   image.layout(32, 64, "aarch64"), "bu", "ru")
    for argv, _ in run.program("dd"):
        assert "conv=notrunc" in argv


def test_assembly_writes_the_table_and_the_filesystems_in_the_layouts_own_geometry():
    """Not just layout() -- assemble() must build sfdisk and dd in the SAME
    sector size `lay` describes, or the table and the filesystem placement
    disagree about where a byte is, which is invisible until the device
    refuses to boot."""
    run = FakeRunner()
    lay = image.layout(8, 32, "aarch64", sector_size=4096)
    image.assemble(run, "/b", "/r", "/out.img", lay, "bu", "ru")
    sfdisk_argv = run.program("sfdisk")[0][0]
    assert "--sector-size" in sfdisk_argv, sfdisk_argv
    assert sfdisk_argv[sfdisk_argv.index("--sector-size") + 1] == "4096"
    for argv, _ in run.program("dd"):
        assert "bs=4096" in argv, argv


def test_the_filesystems_are_written_at_their_declared_offsets():
    """Checks which SOURCE lands at which offset, not just that the two
    offsets appear somewhere: a set comparison of {boot_start, root_start}
    would still pass a mutant that swapped boot and root, writing each
    filesystem into the other's slot -- a disk that partitions correctly
    and boots nothing."""
    run = FakeRunner()
    lay = image.layout(32, 64, "aarch64")
    image.assemble(run, "/b", "/r", "/out.img", lay, "bu", "ru")
    seek_by_src = {}
    for argv, _ in run.program("dd"):
        src = next(a.split("=", 1)[1] for a in argv if a.startswith("if="))
        seek = int(next(a.split("=", 1)[1] for a in argv
                        if a.startswith("seek=")))
        seek_by_src[src] = seek
    assert seek_by_src["/out.img.boot"] == lay["boot_start"]
    assert seek_by_src["/out.img.root"] == lay["root_start"]


def test_verification_rejects_an_image_whose_uuid_is_not_the_one_we_chose():
    """The whole point of choosing it. If the filesystem does not carry the
    number we wrote into the fstab and the cmdline, the phone will not find
    its root -- and that must be caught here, on the host, where it is free."""
    class Wrong(FakeRunner):
        def __call__(self, argv, stdin=""):
            default = super().__call__(argv, stdin)
            if argv and argv[0] == "dumpe2fs":
                return ("Filesystem volume name:   pmOS_root\n"
                        "Filesystem UUID:          00000000-0000-0000-0000-000000000000\n")
            return default

    lay = image.layout(32, 64, "aarch64")
    problems = image.verify(Wrong(), "/out.img", lay, "bu",
                            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert problems and any("uuid" in p.lower() for p in problems)


# A real 4096 b/s device's initramfs attaches the assembled image with
# `losetup -b 4096`, so LBA 1 -- the GPT header -- is byte 4096 there. A
# table written as though sectors were still 512 bytes puts the header at
# byte 512 instead, and the kernel's partition scanner finds nothing:
# "failed to mount subpartitions" on a phone whose kernel had otherwise
# booted correctly. See lib/porthole_image.py's layout()/assemble() for the
# fix. FakeRunner already answers verify()'s sfdisk -l / GPT-signature dd
# truthfully by default (echoing back --sector-size); these three tests
# override that default in turn, then confirm the default itself is right.
def test_verification_flags_a_sector_size_sfdisk_did_not_honour():
    class WrongSectorSize(FakeRunner):
        def __call__(self, argv, stdin=""):
            default = super().__call__(argv, stdin)
            if argv[:2] == ["sfdisk", "-l"]:
                return "Sector size (logical/physical): 512 bytes / 512 bytes\n"
            return default

    lay = image.layout(8, 32, "aarch64", sector_size=4096)
    problems = image.verify(WrongSectorSize(), "/out.img", lay, "bu", "ru")
    assert any("sector size" in p.lower() for p in problems), problems


def test_verification_flags_a_gpt_signature_at_the_wrong_byte():
    class WrongSignature(FakeRunner):
        def __call__(self, argv, stdin=""):
            default = super().__call__(argv, stdin)
            if argv and argv[0] == "dd" and "bs=1" in argv:
                return ""  # nothing at the byte a 4096 b/s device reads
            return default

    lay = image.layout(8, 32, "aarch64", sector_size=4096)
    problems = image.verify(WrongSignature(), "/out.img", lay, "bu", "ru")
    assert any("gpt" in p.lower() or "efi part" in p.lower()
              for p in problems), problems


def test_verification_passes_a_disk_whose_geometry_is_actually_right():
    """THE POSITIVE CONTROL: the two checks above only prove they CAN fire.
    Without this, a check that always reports a problem would look
    identical to a working one."""
    lay = image.layout(8, 32, "aarch64", sector_size=4096)
    problems = image.verify(_uuids_runner(), "/out.img", lay, "uu", "uu")
    assert problems == [], problems


# A real chroot went into the phone with no /home/<user>/.ssh/authorized_keys
# and a surviving /in-pmbootstrap marker -- the image installed, booted, and
# was unreachable. See tools/ph-build.sh's _ph_assemble_image for the fix and
# what put both there. `debugfs -R "stat ..."` was tried first and dropped: a
# missing path's "File not found by ext2_lookup" lands on debugfs's STDERR,
# which this runner contract never sees (a nonzero exit never happens, so
# nothing raises, and the message is just gone) -- read that as "not
# missing" and the check always passes, whether or not the file is really
# there. `ls -l`, checked below, puts its listing on stdout regardless.
def _uuids_runner(extra=None):
    """FakeRunner whose dumpe2fs always matches, so only the check under
    test can put anything in `problems`."""
    class R(FakeRunner):
        def __call__(self, argv, stdin=""):
            default = super().__call__(argv, stdin)
            if argv and argv[0] == "dumpe2fs":
                return ("Filesystem volume name:   " +
                        (image.BOOT_LABEL if "boot" in argv[-1]
                         else image.ROOT_LABEL) + "\n"
                        "Filesystem UUID:          uu\n")
            if argv and argv[:2] == ["debugfs", "-R"] and extra:
                return extra(argv[2])
            return default
    return R()


def test_verification_flags_a_root_with_no_authorized_keys():
    lay = image.layout(32, 64, "aarch64")
    problems = image.verify(_uuids_runner(lambda cmd: ""),
                            "/out.img", lay, "uu", "uu", user="user")
    assert any("authorized_keys" in p for p in problems), problems


def test_verification_flags_a_surviving_in_pmbootstrap_marker():
    def ls(cmd):
        if cmd == "ls -l /":
            return "     2  40755 (2)  0  0  1024  1-Jan-2026 0:00 in-pmbootstrap\n"
        if "authorized_keys" in cmd or ".ssh" in cmd:
            return "    50  100600 (2)  10000  10000  188  1-Jan-2026 0:00 authorized_keys\n"
        return ""
    lay = image.layout(32, 64, "aarch64")
    problems = image.verify(_uuids_runner(ls),
                            "/out.img", lay, "uu", "uu", user="user")
    assert any("in-pmbootstrap" in p for p in problems), problems


def test_verification_passes_a_root_that_actually_has_both_right():
    """THE POSITIVE CONTROL: without this, a check that always reports a
    problem (as the debugfs-stderr version above briefly did, caught only by
    running it for real) would look identical to a working one -- every
    other test here only proves the check CAN fire, not that it can also
    stay quiet."""
    def ls(cmd):
        if cmd == "ls -l /":
            return "    13  40755 (2)  0  0  1024  1-Jan-2026 0:00 boot\n"
        return "    50  100600 (2)  10000  10000  188  1-Jan-2026 0:00 authorized_keys\n"
    lay = image.layout(32, 64, "aarch64")
    problems = image.verify(_uuids_runner(ls),
                            "/out.img", lay, "uu", "uu", user="user")
    assert problems == [], problems


class Skip(Exception):
    """The suite's skip signal; _runner understands it."""


def _sandbox_reachable() -> bool:
    """Probe ONCE, cheaply: podman on PATH and porthole-sandbox answering a
    trivial exec. This is the only place a missing workspace may become a
    skip -- `_in_sandbox` itself must not, see its docstring."""
    import shutil
    import subprocess
    if not shutil.which("podman"):
        return False
    out = subprocess.run(["podman", "exec", "porthole-sandbox", "true"],
                         capture_output=True, text=True)
    return out.returncode == 0


def _require_sandbox() -> None:
    if not _sandbox_reachable():
        raise Skip("podman is not installed, or porthole-sandbox is not "
                   "running")


def _in_sandbox(argv, stdin=""):
    """Run one command inside porthole-sandbox and return its stdout.

    Callers must call _require_sandbox() first. From here on, a nonzero
    exit is a real failure, never a skip: podman exec's exit code cannot
    tell "no container" from "the command that just ran -- mkfs, sfdisk,
    dd, debugfs -- failed for a real reason". Treating both as
    "workspace unavailable" is the bug this replaced: a mutated assemble()
    that left the image one byte short of ALIGN made img2simg abort inside
    the container, and that abort was reported as a skip, not a failure --
    21/22, exit 0, on a broken assembler.
    """
    import subprocess
    cmd = ["podman", "exec", "-i", "porthole-sandbox"] + [str(a) for a in argv]
    out = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(
            f"{' '.join(str(a) for a in argv)} failed ({out.returncode}) "
            f"in the sandbox: {out.stderr.strip()[:300]}")
    return out.stdout


def test_a_failure_inside_the_container_fails_the_suite_not_skips_it():
    """The fix, proven rather than asserted: once the container is known to
    be reachable, a command that fails inside it must fail the test -- not
    be reported as the workspace being unavailable. Skip that check and the
    e2e test can go green while the assembler is broken; see
    _in_sandbox's docstring for the mutation that did exactly that."""
    _require_sandbox()
    try:
        _in_sandbox(["false"])
    except Skip:
        raise AssertionError(
            "a command that failed for a real reason inside a reachable "
            "container was reported as the workspace being unavailable")
    except RuntimeError:
        return
    raise AssertionError("a nonzero exit inside the container did not "
                         "raise at all")


def _debugfs_entry(listing: str, name: str) -> dict:
    """Parse one line of `debugfs -R "ls -l"` output for the entry named
    `name`: inode, mode(links), uid, gid, size, date, time, name -- split on
    whitespace and matched by field, not by substring, so a uid that happens
    to equal another column's value cannot be mistaken for it."""
    for line in listing.splitlines():
        parts = line.split()
        if parts and parts[-1] == name:
            return {"mode": parts[1], "uid": parts[3], "gid": parts[4]}
    raise AssertionError(f"{name!r} not found in debugfs listing:\n{listing}")


def test_a_real_assembled_image_carries_the_uuid_the_fstab_names():
    """The end-to-end claim, run for real: build a two-partition disk in the
    workspace with no loop device, then read the root filesystem back out of
    it and confirm it is the one we asked for.

    Everything this asserts was measured by hand on 2026-09-08 before the
    module existed; this is that spike, kept."""
    _require_sandbox()
    boot_uuid, root_uuid = image.uuids(seed="test-e2e")
    lay = image.layout(boot_mb=8, root_mb=32, arch="aarch64")

    _in_sandbox(["sh", "-c",
                 "rm -rf /tmp/asm && mkdir -p /tmp/asm/boot /tmp/asm/root/etc "
                 "/tmp/asm/root/usr/bin && echo k > /tmp/asm/boot/vmlinuz && "
                 "install -m 4755 /bin/busybox /tmp/asm/root/usr/bin/su && "
                 "mkdir -p /tmp/asm/root/home/user/.ssh && "
                 "echo 'ssh-ed25519 AAAAtest x' "
                 "> /tmp/asm/root/home/user/.ssh/authorized_keys && "
                 "chown -R 1000:1000 /tmp/asm/root/home/user && "
                 "install -m 755 /bin/busybox "
                 "/tmp/asm/root/usr/bin/captest && "
                 "setcap cap_net_raw+ep /tmp/asm/root/usr/bin/captest"])
    # Written via stdin, not interpolated into a shell command: a Python
    # repr() piped through !r into `sh -c` is a Python quoting convention
    # landing inside a POSIX one, and the two do not agree on every
    # character. `cat` writing whatever came in on stdin has no quoting to
    # get wrong.
    _in_sandbox(["sh", "-c", "cat > /tmp/asm/root/etc/fstab"],
                stdin=image.fstab(boot_uuid, root_uuid))

    image.assemble(_in_sandbox, "/tmp/asm/boot", "/tmp/asm/root",
                   "/tmp/asm/disk.img", lay, boot_uuid, root_uuid)
    # user="user": the same directory the non-root-uid check below already
    # needs, doing double duty as the configured device user. Runs the NEW
    # authorized_keys/in-pmbootstrap check against the real debugfs binary,
    # not FakeRunner -- which is what it takes to catch a check that reads
    # the wrong output stream (debugfs sends a missing path's error to
    # STDERR; found and fixed by running exactly this check for real against
    # a fixture that does NOT have the problem, and watching it wrongly
    # fire anyway).
    assert image.verify(_in_sandbox, "/tmp/asm/disk.img", lay,
                        boot_uuid, root_uuid, user="user") == []

    # Ownership, setuid, a non-root uid and a file capability must all
    # survive mkfs.ext4 -d, or the rootfs boots with a broken /home, an su
    # that cannot elevate, and a binary like ping that cannot open a raw
    # socket -- found out only on the phone.
    _in_sandbox(["dd", "if=/tmp/asm/disk.img", "of=/tmp/asm/p2.img",
                 "bs=512", f"skip={lay['root_start']}",
                 f"count={lay['root_sectors']}", "status=none"])
    listing = _in_sandbox(["debugfs", "-R", "ls -l /usr/bin", "/tmp/asm/p2.img"])
    su = _debugfs_entry(listing, "su")
    assert su["mode"] == "104755", "the setuid bit did not survive"
    assert su["uid"] == "0" and su["gid"] == "0", "su must stay root-owned"

    home = _in_sandbox(["debugfs", "-R", "ls -l /home", "/tmp/asm/p2.img"])
    user = _debugfs_entry(home, "user")
    assert user["uid"] == "1000" and user["gid"] == "1000", \
        "a non-root uid did not survive"

    ea = _in_sandbox(["debugfs", "-R", "ea_list /usr/bin/captest",
                      "/tmp/asm/p2.img"])
    assert "security.capability" in ea, (
        "a file capability did not survive mkfs.ext4 -d -- a binary like "
        "ping that needs cap_net_raw would fail only once it is on the "
        "phone")


def test_the_assembled_image_converts_to_sparse():
    """taimen sets deviceinfo_flash_sparse=true, so this is the form that
    actually reaches the phone. img2simg aborts on an unaligned image, which
    is why layout() pads to 4096."""
    _require_sandbox()
    boot_uuid, root_uuid = image.uuids(seed="test-sparse")
    lay = image.layout(boot_mb=8, root_mb=32, arch="aarch64")
    _in_sandbox(["sh", "-c", "rm -rf /tmp/sp && mkdir -p /tmp/sp/b /tmp/sp/r"])
    image.assemble(_in_sandbox, "/tmp/sp/b", "/tmp/sp/r", "/tmp/sp/d.img",
                   lay, boot_uuid, root_uuid)
    out = _in_sandbox(["sh", "-c",
                       "img2simg /tmp/sp/d.img /tmp/sp/d.sparse && "
                       "stat -c %s /tmp/sp/d.sparse"])
    assert int(out.strip()) > 0


def _extract_ph_build_function(name: str) -> str:
    """One shell function's body, verbatim, out of tools/ph-build.sh."""
    text = (pathlib.Path(__file__).resolve().parent.parent
           / "tools/ph-build.sh").read_text()
    m = re.search(rf"\n{re.escape(name)}\(\) \{{\n(.*?\n)\}}\n", text, re.S)
    assert m, f"{name} not found in tools/ph-build.sh"
    return m.group(1)


def _extract_assemble_image_python() -> str:
    """The python heredoc _ph_assemble_image feeds to `python3 -`, with the
    single leading tab `<<-'PY'` strips removed from each line.

    Pulled out of the real shell source rather than reimplemented: a test
    that reimplements the cleanup logic under test would still pass when the
    shipped copy regresses. This is the same thing a reviewer did by hand,
    running the extracted body with verify() shadowed to force a refusal,
    to find the bug this test guards."""
    body = _extract_ph_build_function("_ph_assemble_image")
    m = re.search(r"<<-'PY'[^\n]*\n(.*?)\n\tPY\n", body, re.S)
    assert m, "python heredoc not found in _ph_assemble_image"
    return "\n".join(line[1:] if line.startswith("\t") else line
                     for line in m.group(1).splitlines())


def _host_assembler_tools() -> bool:
    import shutil
    return all(shutil.which(t) for t in
              ("mkfs.ext4", "sfdisk", "dd", "truncate", "dumpe2fs"))


def test_a_refused_image_is_not_left_where_flash_would_find_it():
    """_ph_assemble_image's own cleanup, run for real.

    porthole_image.assemble()/verify() have no cleanup of their own -- that
    is correctly _ph_assemble_image's job, since only the caller knows `out`
    is the CANONICAL path pmbootstrap export's symlinks() links from and
    flash_rootfs reads. Before the fix this guards, a verify() refusal (or a
    run() failure inside assemble(), between truncate creating `out` and the
    closing rm) left a complete-looking image sitting at exactly that path --
    the same hazard the .stale-images move guards against, arriving from a
    new direction. Nothing else checks whether anything survives at that path
    at all.

    Runs against the HOST's own mkfs.ext4/sfdisk/dd/truncate/dumpe2fs rather
    than through porthole-sandbox: none of them touch a block device or need
    a container (they build and read plain files), and staging a fixture
    chroot across the podman boundary just to reuse tools already on this
    host would be pure overhead for what this test checks. Skipped, not
    failed, if this host is missing any of them.
    """
    if not _host_assembler_tools():
        raise Skip("mkfs.ext4/sfdisk/dd/truncate/dumpe2fs not all on PATH")
    import os
    import subprocess
    import tempfile

    script = _extract_assemble_image_python()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        chroot = tmp / "chroot"
        (chroot / "etc").mkdir(parents=True)
        (chroot / "boot").mkdir()
        (chroot / "usr").mkdir()
        (chroot / "usr/file").write_bytes(b"x" * 50_000)
        out = tmp / "out" / "test.img"
        out.parent.mkdir()

        # A repo whose lib/porthole_image.py is the real module with verify()
        # overridden to always refuse -- the reviewer's own repro. Python
        # resolves the later definition, so this replaces it cleanly.
        repo_root = tmp / "repo"
        (repo_root / "lib").mkdir(parents=True)
        real = (pathlib.Path(__file__).resolve().parent.parent
               / "lib/porthole_image.py").read_text()
        (repo_root / "lib/porthole_image.py").write_text(
            real +
            "\n\ndef verify(runner, out, lay, boot_uuid, root_uuid):\n"
            '    return ["forced failure for the cleanup test"]\n')

        # _ph_assemble_image's only pmbootstrap call is `chroot -r --
        # mkinitfs`; nothing in this test needs it to do anything real. The
        # unmount step after it reads the real /proc/mounts rather than
        # calling pmbootstrap, and this fixture has nothing mounted under
        # it, so that step is a no-op here too.
        bindir = tmp / "bin"
        bindir.mkdir()
        stub = bindir / "pmbootstrap"
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"

        proc = subprocess.run(
            ["python3", "-", str(chroot), str(out), str(repo_root), "aarch64"],
            input=script, capture_output=True, text=True, env=env)

        assert proc.returncode != 0, (
           "a verify() refusal must fail _ph_assemble_image, not exit 0\n"
           f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
        left = sorted(p.name for p in out.parent.iterdir())
        assert left == [], (
           "a refused image must not be left at the canonical path "
           f"flash_rootfs reads from -- found {left}")


def test_a_live_proc_refuses_before_mkfs_ext4_ever_runs():
    """mkfs.ext4 -d recurses the whole chroot and cannot read a live procfs:
    'Permission denied while opening auxv to copy', measured against a real
    chroot on 2026-09-08 where the unmount had not actually cleared
    /proc/1/auxv. That opaque mkfs.ext4 error is exactly what the
    <chroot>/proc emptiness guard exists to head off with a message that
    names what is still there instead.

    No real mkfs.ext4/sfdisk needed, and PATH deliberately carries neither.
    The unmount step scans the real /proc/mounts for paths under the
    fixture chroot and finds none there (nothing in this test is actually
    mounted), so it is a no-op and the fixture's /proc/1/auxv survives --
    proving the guard fires on its own, before image.sizes()/image.assemble()
    ever run. If the guard did not catch this first, the next thing to fail
    would be `mkfs.ext4: not found`, a different error this test also checks
    for.
    """
    import subprocess
    import tempfile

    script = _extract_assemble_image_python()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        chroot = tmp / "chroot"
        for d in ("etc", "boot", "usr", "proc/1", "sys", "dev"):
            (chroot / d).mkdir(parents=True)
        (chroot / "proc/1/auxv").write_bytes(b"")
        out = tmp / "out" / "test.img"
        out.parent.mkdir()

        bindir = tmp / "bin"
        bindir.mkdir()
        stub = bindir / "pmbootstrap"
        stub.write_text("#!/bin/sh\nexit 0\n")  # does nothing -- /proc stays live
        stub.chmod(0o755)

        repo_root = pathlib.Path(__file__).resolve().parent.parent

        proc = subprocess.run(
            [sys.executable, "-", str(chroot), str(out), str(repo_root),
            "aarch64"],
            input=script, capture_output=True, text=True,
            env={"PATH": str(bindir)})

        assert proc.returncode != 0, (
           "a live /proc must fail _ph_assemble_image, not exit 0\n"
           f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
        assert "proc" in proc.stderr and "still has entries" in proc.stderr, (
           f"expected the guard's own message naming what is still "
           f"mounted, got: {proc.stderr}")
        # The opaque failure this guard heads off: `mkfs.ext4` itself was
        # never even reached, let alone run and denied permission by a live
        # /proc -- distinct from our own message, which names mkfs.ext4 by
        # way of explanation.
        assert "Permission denied" not in proc.stderr, (
           f"the guard let mkfs.ext4 run over a live /proc instead of "
           f"refusing first: {proc.stderr}")
        assert not out.exists()


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
