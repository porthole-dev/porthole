---
id: private-container-dev-hides-loop-partitions
title: A privileged container cannot see new loop partition nodes
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: https://github.com/porthole-dev/pmaports/actions/runs/36664181826; https://github.com/porthole-dev/pmaports/actions/runs/36664874458; https://github.com/porthole-dev/pmbootstrap/commit/d4183da02b3b3030b0557e4520ade30c3311d53d
first-learned: 2026-09-30
---

**Symptom** — pmbootstrap finishes installing packages, creates a GPT image,
then fails with `File did not appear: /dev/loop0p2`. Docker is already privileged.

**Cause** — Kernel loop partitions exist in sysfs, but runner udev creates their
nodes outside the container's private `/dev`. Privilege alone does not share
that mount. A broader device-directory bind is unnecessary.

**What to do** — For the allocated image loop device only, create missing
partition nodes using the kernel's major/minor numbers from
`/sys/class/block/<partition>/dev`. Never apply this fallback to an explicitly
selected physical disk or replace existing nodes.

**Control** — The failing run above stopped before mounting the root partition.
With the narrow fix, the second run reached pmbootstrap's completed image result;
its later validation permission failure is a separate check. The regression
executes the actual node-creation branch: physical disks do nothing, a missing
image partition requests the exact sysfs numbers, and an existing node stays
untouched. This evidence establishes image assembly, not device bootability.
