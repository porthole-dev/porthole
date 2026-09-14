---
id: fpc-enrolment-is-gated-behind-a-keymaster-signed-auth-token
title: The FPC trustlet commits an enrolment only with a Gatekeeper/keymaster-signed auth token, which postmarketOS cannot produce
scope: soc:msm8998
subsystem: firmware
severity: finding
confidence: proven
evidence: 2026-09-14, taimen, kernel 7.2.2 r47, fpctzappfingerprint. A real finger enrolled cleanly through the trustlet: BEGIN_ENROL=0, eight capture+ENROL steps accepted (status 0x10f then 0x113, final 0x0 "0 remaining"), then END_ENROL {0x0b,0x02} answered -201 (0xffffff37). fprintd's fpctee driver never sends AUTHORIZE_ENROL {0x03,0x03}. A no-finger probe (taimen bringup/fpc-tee-chardev-test/enrol_auth_probe.py): END_ENROL is -201 whether bare, after CHECK_ENROL_TIMEOUT, or after AUTHORIZE_ENROL; AUTHORIZE_ENROL with a structurally valid but unsigned 69-byte HAT is also -201. IDENTIFY {0x0b,0x03} works (returns finger id 0 = no match on an empty DB) and needs no token. Decompiled trustlet: the HAT verifier (ta.c FUN_00003010) HMACs HAT[0..0x25] with a 32-byte key and compares to HAT[0x25..0x45], and is fail-closed. The HAL (fpc_tee_init_hw_auth) fetches that key from the keymaster64 trustlet (KEYMASTER_GET_AUTH_TOKEN_KEY, cmd 0x205) and installs it with FPC_SET_KEY_DATA; Gatekeeper signs HATs with the same key, which never leaves TrustZone.
refutes: a mainline FPC fingerprint can be enrolled once the trustlet loads and captures; END_ENROL -201 is a driver bug; the auth token is optional; skipping AUTHORIZE_ENROL lets enrolment through; verify/identify and enrol share the same gate
first-learned: 2026-09-14
---

**Everything below enrolment-commit works; the commit is the wall.** On the
Pixel 2 XL the FPC trustlet loads, initialises the sensor, captures a real
finger, and accepts every enrolment sample -- and then refuses `END_ENROL`
with `-201`. The refusal is not ours: fprintd's driver has built a complete
template by then. It is the trustlet's hardware-authorization gate.

The FPC secure-enrolment design (identical in Sony's open
`vendor-sony-oss-fingerprint` HAL) is: `preEnroll` gets a challenge
(`GET_ENROL_CHALLENGE {0x03,0x02}`), Android's Gatekeeper signs a 69-byte
hardware auth token (HAT) carrying that challenge, `AUTHORIZE_ENROL
{0x03,0x03}` verifies the signed token, and only an authorized session may
`END_ENROL`. The HMAC key the trustlet verifies with is fetched from the
`keymaster64` trustlet and installed with `FPC_SET_KEY_DATA`; Gatekeeper
holds the same key. Both live in TrustZone and neither exposes the raw key.

postmarketOS has no Gatekeeper, so it cannot produce a validly-signed HAT,
so `AUTHORIZE_ENROL` cannot pass, so `END_ENROL` cannot commit. Setting the
keymaster key would only *enable* verification, not bypass it. The gate is
fail-closed (the verifier returns `-201` when the token is absent, unsigned,
or wrongly signed).

What this does NOT block: loading the trustlet, sensor init, capture,
`IDENTIFY`, and the template database listeners -- all proven. Matching a
finger against an *already-enrolled* template needs no token. But templates
can only be created by an authorized enrolment, so on a device that never
enrolled under Android there is nothing to match against.

The only real path to enrolment is to satisfy the auth flow: drive the
device's own `keymaster64` and a Gatekeeper trustlet to mint a signed HAT --
i.e. stand up the hardware-auth stack Android provides. That is a large,
separate effort, not a fingerprint-driver change. A smaller experiment that
would sharpen (not lift) the finding: install the keymaster key with
`FPC_SET_KEY_DATA` and confirm `AUTHORIZE_ENROL` still rejects an unsigned
token -- our kernel driver would first need to load `keymaster64`, which it
does not today.

Related: [[a-qsee-trustlet-stores-files-through-two-listeners]],
[[a-trustzone-command-can-succeed-and-do-nothing]].
