---
id: 55-nfc
title: "Playbook: NFC"
scope: generic
subsystem: radio
severity: technique
confidence: proven
evidence: taimen docs/superpowers/specs/2026-09-10-nfc-toggle-and-portal-design.md; PN553 brought up on google-taimen 2026-09-10, tag read at Type 4A/ISO-DEP
first-learned: 2026-09-10
---

**Goal:** the NFC controller probes, the user can turn the radio on and off
without a terminal, and a card held to the back of the phone is detected.

**Done when:** `busctl call org.neard /org/neard/nfc0 org.neard.Adapter
StartPollLoop s Initiator` produces a `/org/neard/nfc0/tag0` object whose
`Uid` property reads back off a real card — not merely "the driver probed".

## Order

1. **Find the chip in the vendor devicetree, not in a spec sheet.** It is
   almost always an I2C slave with three GPIOs: interrupt, enable (VEN) and a
   firmware-download pin. Qualcomm vendor trees name them `qcom,nq-irq`,
   `qcom,nq-ven` and `qcom,nq-firm`; the mainline binding calls the last two
   `enable-gpios` and `firmware-gpios`. The vendor's own driver node is the
   authority on which pins.

2. **Check the chip has a mainline driver before anything else.** `ls
   drivers/nfc/` is the whole answer: `nxp-nci` (PN547/548/553 and friends),
   `pn544`, `pn533`, `s3fwrn5` (Samsung), `st-nci`/`st21nfca`/`st95hf` (ST),
   `nfcmrvl` (Marvell), `trf7970a` (TI), `fdp` (Intel). Most phone controllers
   speak NCI, so the driver is a thin transport shim over `CONFIG_NFC_NCI` and
   there is rarely anything to write.

3. **Kernel config: three symbols, and modules are fine.**

   ```
   CONFIG_NFC=m
   CONFIG_NFC_NCI=m
   CONFIG_NFC_NXP_NCI=m        # or your vendor's shim
   CONFIG_NFC_NXP_NCI_I2C=m    # the transport
   ```

   Nothing here needs to be built in: nothing in the boot path touches NFC.

4. **Write the node.** For an NXP part on I2C:

   ```dts
   &blsp2_i2c2 {
       status = "okay";

       nfc@28 {
           compatible = "nxp,nxp-nci-i2c";
           reg = <0x28>;

           interrupt-parent = <&tlmm>;
           interrupts = <92 IRQ_TYPE_LEVEL_HIGH>;

           enable-gpios = <&tlmm 12 GPIO_ACTIVE_HIGH>;
           firmware-gpios = <&tlmm 93 GPIO_ACTIVE_HIGH>;

           pinctrl-names = "default";
           pinctrl-0 = <&nfc_int_active &nfc_enable_active &nfc_firm_active>;
       };
   };
   ```

   The bindings under `Documentation/devicetree/bindings/net/nfc/` are short
   and worth reading in full — there are only nine of them.

   **Bias the interrupt line the right way in the pinctrl node**, or you get a
   permanent interrupt storm rather than a broken NFC stack —
   [[a-level-irq-with-a-pull-up-storms-when-its-chip-is-off]]. See the traps.

5. **Install neard, then check it can actually start.** See the trap below;
   on Alpine it could not, until 2026-09-10.

6. **Verify against hardware, not against `dmesg`.** A probed controller
   proves the I2C and the interrupt, and says nothing about the antenna:

   ```sh
   busctl get-property org.neard /org/neard/nfc0 org.neard.Adapter Protocols
   busctl call org.neard /org/neard/nfc0 \
       org.freedesktop.DBus.Properties Set ssv org.neard.Adapter Powered b true
   busctl call org.neard /org/neard/nfc0 org.neard.Adapter StartPollLoop s Initiator
   busctl tree org.neard          # a tag appears as /org/neard/nfc0/tag0
   busctl call org.neard /org/neard/nfc0/tag0 \
       org.freedesktop.DBus.Properties GetAll s org.neard.Tag
   ```

7. **Give the user a switch.** GNOME Settings has a `Privacy ▸ NFC` page as of
   the patch carried in `temp/gnome-control-center`: the switch binds straight
   to `org.neard.Adapter Powered`, and the page hides itself where neard
   reports no adapter, so it costs nothing on a device without the hardware.

## The traps

- **The distribution's neard package probably cannot start itself.** Upstream
  neard ships no `/usr/share/dbus-1/system-services/org.neard.service`, so the
  bus cannot activate it despite the unit already being `Type=dbus` with
  `BusName=org.neard`; and the unit's `[Install]` section contains only
  `Alias=`, so `systemctl enable neard` exits 0, creates no
  `multi-user.target.wants` symlink, and leaves the service disabled. Both
  come straight from upstream, so this is not one distribution's bug. Fixed in
  `temp/neard`; check for the activation file before believing an enable.

- **A `bias-pull-up` on a `LEVEL_HIGH` interrupt line is an interrupt storm,
  not an NFC bug.** Mainline `nxp-nci` holds VEN low until userspace calls
  `dev_up()`, so the controller is unpowered and drives nothing; a pull-up then
  holds the line asserted from `request_threaded_irq()` onwards, the handler's
  first I2C read NACKs, the driver latches `hard_fault` and returns
  `IRQ_HANDLED` forever without ever deasserting it. Measured on taimen at
  26 million interrupts in 27 minutes and 22% of a core, continuously, on a
  phone whose NFC nobody was using — [[a-level-irq-with-a-pull-up-storms-when-its-chip-is-off]].
  Use `bias-pull-down`, and check `/proc/interrupts` after your first boot with
  the node enabled.

- **A tag that is not detected is an antenna alignment problem until proven
  otherwise.** Two polling attempts against a card in the wrong place look
  exactly like a dead radio. Move the card — on a Pixel 2 XL the coil is under
  the upper-middle of the back — before concluding anything about the driver.
  This cost two arms on 2026-09-10 and the card worked first try once placed.

- **`Powered` cannot be set false while a tag is active.** The `Set` call fails
  with no error information. Remove the card or restart neard.

- **The adapter comes up unpowered and stays that way** until something sets
  `Powered`. Starting neard at boot therefore costs nothing in RF or battery,
  which is why enabling the unit is safe.

- **`Could not get Bluetooth default adapter … org.bluez.Manager` in neard's
  log is noise.** neard's handover agent probes for the BlueZ 4 API, which no
  current BlueZ has. It has no bearing on tag reading.

- **The session user can talk to neard without polkit**, because neard's own
  `org.neard.conf` allows `at_console="true"`. That is what lets a Settings
  switch write `Powered` with no helper. If your distribution ships a
  different policy you will get `AccessDenied` and need a rule of your own.

## What applications can do with it

Very little, safely, for now: neard is a **system**-bus service and there is no
`org.freedesktop.portal.NFC`, so a sandboxed application has no sanctioned
path to it. The design for one is written up in the taimen tree at
`docs/superpowers/specs/2026-09-10-nfc-toggle-and-portal-design.md`, and
`NFC-FOR-APPLICATIONS.md` beside it describes what an application can do
today. Do not have an application power the radio on by itself — that switch
belongs to the user.
