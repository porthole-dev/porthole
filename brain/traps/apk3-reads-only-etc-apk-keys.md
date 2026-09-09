---
id: apk3-reads-only-etc-apk-keys
title: apk-tools 3 trusts only /etc/apk/keys, and the keys packages install to /usr/share/apk/keys -- so a fresh rootfs can install nothing at all
scope: generic
subsystem: packaging
severity: trap
confidence: proven
evidence: taimen 2026-09-09, postmarketOS edge rootfs built by pmbootstrap in the porthole sandbox, apk-tools 3.0.8-r0. Every repository answered 'UNTRUSTED signature' and '5 unavailable ... 1244 distinct packages available' (the installed db only). /etc/apk/keys was EMPTY; /usr/share/apk/keys held 19 keys including build.postmarketos.org.rsa.pub and, in the aarch64/ subdirectory, alpine-devel@lists.alpinelinux.org-616ae350.rsa.pub -- which is exactly the key .SIGN.RSA. names in the current edge/main APKINDEX. alpine-keys and postmarketos-keys were both installed. After 'cp /usr/share/apk/keys/*.pub /usr/share/apk/keys/aarch64/*.pub /etc/apk/keys/': OK, 37356 distinct packages available.
first-learned: 2026-09-09
---

**Symptom** — every repository is UNTRUSTED and the phone can install nothing:

    # apk update
    WARNING: updating and opening .../postmarketos/main/aarch64/APKINDEX.tar.gz: UNTRUSTED signature
    WARNING: updating and opening .../alpine/edge/main/aarch64/APKINDEX.tar.gz: UNTRUSTED signature
    5 unavailable, 0 stale; 1244 distinct packages available

That last line is the thing that makes this hard to see: it looks like a
success line, and the count is plausible. It is the count of packages in the
*installed database*. Every repository failed.

**Cause** — `apk-tools 3` reads trusted keys from `/etc/apk/keys` only. The
`alpine-keys` and `postmarketos-keys` packages install to
`/usr/share/apk/keys/` and `/usr/share/apk/keys/<arch>/`. Both packages being
installed therefore proves nothing, and `ls /etc/apk/keys` is the check.

**Fix** —

    sudo cp /usr/share/apk/keys/*.pub /usr/share/apk/keys/$(apk --print-arch)/*.pub /etc/apk/keys/

Note the arch subdirectory: on aarch64 that is where `-616ae350`, the key that
actually signs today's `edge/main`, lives. Copying only the top level leaves
Alpine still untrusted while postmarketOS starts working, which looks like a
mirror problem.

**What this rules out** — that UNTRUSTED means a bad mirror, a wrong clock, or
a tampered index. The date was correct and the mirrors were the official ones;
`tar tzvf APKINDEX.tar.gz` named `.SIGN.RSA.alpine-devel@...-616ae350.rsa.pub`,
a key that was on the device the whole time, in the wrong directory.

**What it costs if you miss it** — the device silently cannot take a security
update or install a tool, and the failure only surfaces the first time someone
tries. Here that was two weeks after the reflash, in the middle of an unrelated
investigation, because a measurement needed `grim`.

**And once it works, check what an upgrade would do.** A device carrying forked
packages has just had its repositories turned back on: `apk upgrade --simulate`
first. On taimen it wanted to replace the local mesa 26.1.6-r14 -- which
carries the a5xx GMEM fixes -- with upstream 26.2.2-r0, because upstream's
version is genuinely newer. Pin those with `apk add 'mesa=26.1.6-r14'` (and
each forked subpackage) or the fixes leave on the next upgrade.
