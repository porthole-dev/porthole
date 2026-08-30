---
id: two-pmbootstrap-builds-destroy-each-other
title: Two concurrent pmbootstrap builds share one buildroot and silently destroy each other
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-08-30: a webkit2gtk build running since 10:51 died at 11:28:58 with 'clang++: error: no such file or directory: .../TextMetrics.idl' and 'fileparse(): need a valid pathname'. The file was genuinely absent from the chroot. A second agent had started 'pmbootstrap build --lax gst-plugins-good' in the same workspace; abuild cleans $srcdir before unpacking, so /home/pmos/build/src/ held gst-plugins-good-1.28.5 and the staged APKBUILD read pkgname=gst-plugins-good. 37 minutes of an 8233-object build lost, and the failure named the compiler rather than the collision.
first-learned: 2026-08-30
---

**One workspace has one buildroot chroot per arch, and `abuild` cleans
`$srcdir` before it unpacks. So a second `pmbootstrap build` started while the
first is compiling deletes the first one's source tree mid-flight.** There is no
lock. Nothing warns. Both agents believe they are alone.

What it looks like from inside the victim -- a webkit build 28% through 8233
objects:

    clang++: error: no such file or directory: '.../WebCore/html/TextMetrics.idl'
    clang++: error: no input files
    fileparse(): need a valid pathname at .../generate-bindings.pl line 92
    cannot open .../included_idl_files_WebCoreBindings.tmp for reading
    ninja: subcommands failed

Every one of those accuses the toolchain. The natural reading is "crossdirect is
mangling the IDL preprocessor invocation", which is a plausible and completely
wrong theory that costs an afternoon. The file is simply gone:

    ls .../src/webkitgtk-2.48.1/Source/WebCore/html/TextMetrics.idl
    -> No such file

    ls .../home/pmos/build/src/
    -> gst-plugins-good-1.28.5          <- somebody else's package
    cat .../home/pmos/build/APKBUILD
    -> pkgname=gst-plugins-good

**The diagnosis is two commands.** Before believing any "missing source file" or
"no input files" failure, ask what is actually staged in the buildroot and
whether another pmbootstrap is running:

    ls <workdir>/chroot_buildroot_<arch>/home/pmos/build/src/
    pgrep -af pmbootstrap

If the answer is a package you are not building, you were not the victim of a
compiler bug.

**Why this is easy to hit here.** The workspace is deliberately shared -- one
persistent container, one pmbootstrap work dir -- and agents run long builds in
the background and then go do something else. Two agents working the same port
at once is the normal case, not the exotic one. The phone already has a mutex
(`tools/tk-device.sh`, exit 75 for "lock unavailable") precisely because one
physical device cannot serve two callers. The buildroot has exactly the same
property and no such guard.

**FIXED 2026-08-30 in `porthole pkg`.** The same flock idiom as the device
mutex, taken on the pmbootstrap work dir for the length of a build, with the
holder recorded in a `.holder` sidecar so the second caller is told who is
building what rather than queueing blind:

    $ porthole pkg build phoc
    porthole: the buildroot is busy: webkit2gtk-6.0 pid=154591 since=13:52:27
      -> two pmbootstrap builds share one buildroot and delete each other's
         source tree. Wait, or --wait SECONDS.
    $ echo $?
    75

Exit 75, the same retryable code the device mutex uses, because "the buildroot
is busy" is a reason to come back and not a reason to think the package is
broken. `--wait SECONDS` queues deliberately.

Two more holes were closed with it, both of which recreate the collision on
their own: a killed or timed-out `porthole pkg build` used to leave pmbootstrap
running INSIDE the container (killing a `podman exec` client does not kill what
it exec'd), and it now kills the container-side process too. And a `pgrep -f
"pmbootstrap.*build"` guard written into a shell one-liner MATCHES ITS OWN
COMMAND LINE, so an agent polling that way waits for itself forever and never
starts. Take the lock; do not grep for the neighbour.

**This still bites anything that calls pmbootstrap directly** -- `porthole
sandbox shell --command 'pmbootstrap build ...'` takes no lock, because it is a
bare command runner. Use `porthole pkg build`, which is what the lock is
attached to.
