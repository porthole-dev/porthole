---
id: a-trustzone-command-can-succeed-and-do-nothing
title: A TrustZone command can return status 0 and do nothing -- the tell is how long it took
scope: device:google-taimen
subsystem: interrupts
severity: finding
confidence: proven
evidence: 2026-09-12, kernel 7.2.2 #44. fpc1020_tz_send_locked() passed FPC1020_TZ_ENV_SIZE (64) where fpc1020_tz_xfer_locked() wants the cmdbuf's size. Isolated through the ioctl path on an open chardev, varying one field at a time -- INIT len=64 word3=0: status 0 in 4ms; len=4096 word3=0: status 0 in 745ms; len=64 word3=4096: status 0 in 2ms; len=4096 word3=4096: status 0 in 754ms. Before the fix, the fingerprint interrupt (msmgpio 86, IRQ_TYPE_EDGE_RISING) read 0 counts across 7h15m of uptime with gpio86 stuck high under bias-pull-down. After it, on a fresh boot: open("/dev/fpc_tee") 895ms, gpio86 high -> low, 80 counts during bring-up. Fix: linux-ws 07cc59f45223.
refutes: the fingerprint sensor does not raise FP_INT_N on touch; the sensor's capacitive front end is damaged; the touch tests of 2026-09-11 and 2026-09-12 morning were negative results; a TA command that returns status 0 has done its work; a nonzero-status check is enough to catch a malformed TA request; fprintd enumerating the device proves the sensor is initialised
first-learned: 2026-09-12
---

**The question** — a fingerprint sensor confirmed working under stock Android
produces no interrupt on any touch, through a stack that reports health at
every layer: trustlet loaded, transport verified, INIT sent, capture loop
returning its documented "no image yet" progress code, fprintd enumerating the
device and starting an enrolment. Roughly eighty real touches across three
separate instruments, over two days, all negative. Where is the gap?

**The answer** — there was no gap to find at the sensor. The driver's INIT
declared the wrong request length, the trustlet accepted it, **answered status
0, and skipped the hardware bring-up entirely.** The sensor never left reset,
so FP_INT_N stayed asserted from the driver probe's own `hw_reset()` -- and the
line is armed `IRQ_TYPE_EDGE_RISING`, so once it is high **no touch can ever
produce another edge**. Every touch test in the campaign was run through an
interrupt that was structurally incapable of firing.

The only observable difference between the broken call and the working one was
**duration**: 4 ms versus ~750 ms. Same return code, same status word, same
absence of log output. A `dev_warn` guarding on a nonzero status -- which the
driver had -- cannot see this, because the status is zero.

**What to take from it**

- A secure-world call that returns success has not necessarily done anything.
  When a command is supposed to touch hardware, **time it**, and know roughly
  what the real thing costs. That number is the only assertion available when
  every status word says 0.
- An edge-triggered line that is already asserted is a dead instrument, not a
  quiet one. Before trusting any "no interrupt arrived" result, read the raw
  level (`/sys/kernel/debug/gpio` prints every pin's level, not just requested
  ones) and the kernel's own count. A count of exactly zero over hours of
  uptime is not evidence of silence -- it is evidence the instrument never
  armed.
- Whatever clears such a line may be reachable only from the secure world. Here
  the deassert needs an SPI transaction only the trustlet can issue, so nothing
  in Linux could have recovered it, and no amount of re-requesting the IRQ with
  a different trigger would have helped.
