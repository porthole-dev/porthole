---
id: a-fresh-kernel-cannot-ram-boot-against-installed-modules
title: A freshly built kernel cannot RAM-boot against the modules already on the device
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "`fastboot boot` of a tree-built Image.gz on google-taimen; /proc/version reported `#3 ... 14:29:24 UTC` where the flashed slot is `#90`; dmesg carried `failed to validate module [uinput] BTF: -22` and `modprobe: ERROR: could not insert 'uinput': Invalid argument`; /dev/loop* absent; `[pmOS-rd]: losetup: /dev/sda13: failed to set up loop device` then `ERROR: failed to mount subpartitions!` then `Entering debug shell`"
first-learned: 2026-08-27
---

**Symptom** — you RAM-boot a kernel you just built and the phone comes up
*almost*. It pings, the USB gadget enumerates, ssh is refused. No panic, no
oops, nothing on netconsole. It reads exactly like a bad kernel, and the
obvious next move -- suspect the change you just made -- is wrong.

Check `/proc/version` before anything else. If the build number is not the one
on the flashed slot, you are here.

**Cause** — a RAM boot replaces the kernel and *nothing else*. The initramfs
and `/lib/modules` on the device still belong to the kernel that was flashed.
Rebuilding moves the build id and the BTF, so every module is refused:

    failed to validate module [uinput] BTF: -22
    modprobe: ERROR: could not insert 'uinput': Invalid argument

On a device whose initramfs needs a module to *mount root*, that is fatal
rather than merely degraded. taimen keeps its pmOS install as a subpartition
image inside `/dev/sda13`, so the initramfs must `losetup` it, so it needs
`loop.ko`. Without it: `failed to mount subpartitions!` and then the debug
shell -- which is a perfectly friendly place, once you know you are in it
(see [[initramfs-is-not-frozen]]).

The widespread half-belief that this is a *CONFIG-change* hazard is what makes
it bite. A config edit does move every module's `module_layout` CRC, and that
is true and separate. But BTF and the build id move on **every** kernel
rebuild, config touched or not. There is no such thing as a source-only kernel
change that keeps the installed modules loadable.

**What to do** — a DTS-only RAM boot is unaffected and stays the cheap rung;
it does not rebuild the kernel. For anything that rebuilds `Image.gz`, use the
rung that regenerates the initramfs and pushes matching modules
(`porthole build fast`). If the device cannot RAM-boot at all, say so once in
the profile and let the tool refuse:

    PORTHOLE_RAMBOOT_NEEDS_MODULES=loop

and `porthole build boot --kernel` will stop before it strands the phone,
naming the rung to use instead.

Related: [[initramfs-is-not-frozen]], [[ab-retry-counter-is-a-countdown-not-a-glitch]].
