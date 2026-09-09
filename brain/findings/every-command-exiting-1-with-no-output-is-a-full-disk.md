---
id: every-command-exiting-1-with-no-output-is-a-full-disk
title: Every command exiting 1 with empty output is a full disk, not a broken harness
scope: generic
subsystem: host
severity: finding
confidence: proven
evidence: a Write to the scratch dir returned EDQUOT while `df /var/home` showed 91 GiB free; /tmp was a 20 GiB tmpfs holding 15 GiB of agent scratch, 7.4 GiB of it one coredump
refutes: the Bash tool is broken; a backgrounded curl kills the tool; restarting the agent fixes it
first-learned: 2026-09-09
---

**The question** — every shell command suddenly returns exit 1 with completely
empty stdout AND empty stderr. `echo alive` does it. `/usr/bin/true` does it,
sandboxed and unsandboxed. Reading files still works. Restarting the agent
does not help. Is the tooling broken?

**The answer** — no. The disk is full, or a quota is exhausted. An agent
harness writes each command's output to a temp file; when it cannot write that
file, every command reads back as exit 1 with nothing on either stream. The
command itself ran perfectly.

**The probe, and it is the only one that still works:** write a small file to
the scratch directory and read the errno. `EDQUOT` or `ENOSPC` settles it in
one call. Do this FIRST, before forming any theory about the harness.

**What this rules out** —

- *"The Bash tool is broken."* It is not. It is faithfully reporting a write it
  could not make.
- *"A backgrounded curl kills the tool."* This was recorded in two handoffs and
  a memory as a reproducible trigger, because both outages began right after a
  backgrounded `curl` failed leaving a 0-byte output file. **curl exit 23 is
  literally "write error"** — curl was the first process to notice the full
  disk, not the cause of anything. The correlation was real and the causation
  was backwards.
- *"Restart the agent."* Nothing about this is session state. A genuinely fresh
  session fails identically.
- *"`df` says there is space, so it is not the disk."* `df` is charged to the
  disk; a quota is charged to you. A quota'd filesystem reports terabytes free
  through statvfs and still returns EDQUOT. Worse, the free filesystem and the
  full one are often different mounts: here `$HOME` had 91 GiB free the entire
  time while `/tmp` — a 20 GiB **tmpfs**, so RAM — was full.

**How it was established** — three sessions were lost to this on 2026-09-09.
The first two ended without a diagnosis and wrote handoffs blaming the
harness. In the third, a `Write` to the scratch directory returned `EDQUOT`,
which named it immediately. `/tmp` held 15 GiB of agent session scratch, 7.4
GiB of it a single coredump from an investigation that had closed three days
earlier. Freeing it restored the shell with no restart.

`porthole doctor` now carries the check that would have caught this on day
one: a 4 KiB write probe per work-dir filesystem, reported beside the free
space, with the errno choosing the remedy. It also found this repo's own test
suite leaking a temp tree per run into that same tmpfs — 155 of them.

What would overturn it: a case where the write probe succeeds and commands
still come back exit 1 with both streams empty. That would be a genuine
harness fault, and it has not been seen.
