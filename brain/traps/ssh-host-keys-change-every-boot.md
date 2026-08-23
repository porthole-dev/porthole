---
id: ssh-host-keys-change-every-boot
title: Host keys change on essentially every boot, which constrains both correctness and speed
scope: generic
subsystem: setup
severity: fact
confidence: proven
evidence: porthole lib/porthole.sh TK_SSH_OPTS
first-learned: 2026-07-25
---

A device under bring-up regenerates its ssh host keys on essentially every boot
— the rootfs is reflashed, or the keys live on a tmpfs, or first-boot generation
runs again.

Two consequences, and the second one is the non-obvious one.

**Correctness.** `StrictHostKeyChecking=no` plus a `/dev/null` known-hosts file
is mandatory, not laziness. With `BatchMode=yes` a real `known_hosts` does not
merely prompt — it fails outright, and every tool in the box stops working after
the next reflash.

**Speed.** SSH connection multiplexing (`ControlMaster=auto`) is the single
biggest speed win available: a warm round trip is ~15 ms against ~200 ms for a
fresh handshake, and these tools run in tight loops. But a master socket that
outlives a reboot is a **live handle to a dead sshd** — the next command
inherits the dead channel and hangs until `ControlPersist` expires instead of
failing fast.

So multiplexing is only safe if **every reboot path tears the master down
first** (`ssh -O exit`). porthole does this in `tk_request_reboot`,
`tk_request_bootloader`, `tk_rearm_and_boot`, and in `tk_wait_ssh` the moment it
observes a changed `boot_id`. `PORTHOLE_NO_MUX=1` disables the whole thing,
which is the first thing to try when diagnosing a strange hang.

Related: [[poll-never-sleep]], [[frozen-is-not-hung]].
