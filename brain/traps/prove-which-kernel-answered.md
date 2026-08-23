---
id: prove-which-kernel-answered
title: After any boot test, prove which kernel answered
scope: generic
subsystem: boot
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §4
first-learned: 2026-07-26
---

"Something answered ssh" is not evidence that the kernel under test is the one
answering.

A watchdog reset, a retry-counter drop, or a failed slot flip can put the
*previous* kernel back, and from the host it looks exactly like success. You
then attribute the old kernel's behaviour to your new one.

Two commands, every time:

```sh
cat /proc/version      # which build
cat /proc/cmdline      # did your tokens actually survive?
```

**A cmdline token you cannot read back in `/proc/cmdline` was never applied.**
Same for a module parameter absent from `/sys/module/<mod>/parameters/`.

Related: [[a-module-parameter-that-does-not-exist-is-ignored]],
[[stale-dev-package-outranks-your-build]], [[never-judge-a-boot-by-the-screen]].
