---
id: mod-rung-pushes-an-unstripped-module-into-a-stripped-set
title: porthole build mod pushes an unstripped tree module into a stripped packaged set -- on venus-core that bootloops taimen
scope: generic
subsystem: kernel
severity: trap
confidence: proven
evidence: taimen 2026-09-01, two bootloops with two different venus-core source contents, both ending only in a physical Power+VolDown; restoring the packaged .ko booted in 63 s
first-learned: 2026-09-01
---

**Check the size of what `mod` just installed against its siblings before you
reboot.** An order-of-magnitude difference means the two came from different
build paths, and on `venus-core` that combination bootloops taimen with no
console and no pstore.

The two push paths do not produce the same artefact:

- `porthole build fast` runs `tkpush-modules`, which extracts modules from the
  **built apk** -- so they are stripped, exactly as the aport ships them.
- `porthole build mod` builds from your **tree** and pushes the raw `.ko`. It
  drops `.BTF` (`tools/tk-strip-btf.py`, see
  [[a-tree-built-module-carries-btf-the-running-kernel-rejects]]) but it does
  **not** strip debug info.

Measured on taimen, same kernel, same directory:

```
venus-core.ko  from the apk (fast rung)     315448 bytes
venus-core.ko  from the tree (mod rung)    3431808 bytes    ~11x
venus-dec.ko / venus-enc.ko                byte-identical between the two
```

**What it costs.** Rebooting onto the tree-built `venus-core.ko` put the phone
into a boot loop: the USB gadget re-enumerated on a ~21-28 s cycle, TCP came up
far enough to answer `Connection refused` on port 22, and the phone reset before
sshd or the port-2323 rescue listener ever accepted. 100 s of 1-second polling
on both ports caught no window, so there is **no remote way back** -- it needs a
physical Power+VolDown into fastboot. `/sys/fs/pstore` was empty afterwards, so
the reset leaves no post-mortem either.

It happened **twice, with two different source contents** for that file -- once
with a local change and once with the file reverted to HEAD and rebuilt. That
is what rules the source change out and the build path in.

**The way back, if you are already in it.** From fastboot, RAM-boot a rescue
image with the offender blacklisted; nothing needs flashing:

```sh
tools/bootimg-cmdline.py patch <exported boot.img> \
    --add modprobe.blacklist=venus_core,venus_dec,venus_enc -o rescue-boot.img
tools/boot-probe.sh rescue-boot.img "rescue"
```

The exported image lives at
`<workdir>/chroot_rootfs_<device>/boot/boot.img`. Then restore the packaged
module from the previous set that `tkpush-modules` keeps:

```sh
cp -a /lib/modules/$(uname -r).old/kernel/.../venus-core.ko \
      /lib/modules/$(uname -r)/kernel/.../venus-core.ko && depmod -a
```

That booted in 63 s with venus loaded, /dev/video6+7 present and no errors.

**What is NOT established.** Which property of the tree-built module is fatal.
It is not the documented BTF gate: that one fails the *load* with `ELOOP`,
rendered as "Symbolic link loop", and the driver never probes. This resets the
SoC instead. Stripping, section layout and a sibling ABI skew are all still
open. An `insmod` that reported `Unknown symbol in module` during recovery was
an artefact of the blacklisted rescue boot, where venus's dependencies were not
loaded and `insmod` does not resolve them -- do not read it as evidence.

**Until that is known**, prefer `fast` over `mod` for anything in the venus
stack on this device, and for any module whose siblings came from the apk.
