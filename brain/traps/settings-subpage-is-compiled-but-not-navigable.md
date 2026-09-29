---
id: settings-subpage-is-compiled-but-not-navigable
title: Settings subpage is compiled but not navigable
scope: generic
subsystem: userspace
severity: trap
confidence: proven
evidence: https://github.com/porthole-dev/pmaports/commit/4af31d5; GNOME Settings 51.0-r55 compiled locally. Extracting its .gresource.cc_privacy ELF section with objcopy, then using gresource extract for /org/gnome/control-center/privacy/cc-privacy-panel.ui, found a visible nfc_row and an instantiated CcNfcPage. The preceding patch lacked the page child and marked its row invisible.
first-learned: 2026-09-30
---

**Symptom** — GNOME Settings contains NFC code and a page resource, but NFC
is absent from the privacy navigation.

**Cause** — In this GNOME Settings 51 fork, compiling the page was insufficient:
its navigation row had `visible: false`, and the privacy template did not
instantiate the `CcNfcPage` subpage. Finding NFC strings in the binary proved
neither navigation nor page construction.

**What to do** — Register the page type, instantiate it in the privacy template,
and expose its navigation row. Inspect the compiled privacy resource: it must
contain the page object and a visible row. An Alpine `gresource` built without
ELF support cannot inspect the executable directly; extract
`.gresource.cc_privacy` with `objcopy --dump-section` first.

This verifies packaged navigation, not an adapter or an NFC transaction.
Hardware behavior still requires the exact package running on the device.
