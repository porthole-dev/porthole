---
id: apr-service-inherits-a-protection-domain-the-board-deletes
title: A new APR service inherits a protection domain the board deletes
scope: soc:qcom
subsystem: audio
severity: trap
confidence: proven
evidence: taimen 2026-08-25 -- service@9/a/b present in /sys/firmware/devicetree/base/soc@0/remoteproc@17300000/glink-edge/apr/ with reg and qcom,protection-domain, while /sys/bus/aprbus/devices held only 4:3 4:4 4:7 4:8. Fixed by matching the /delete-property/ the board dts already applies to q6core/q6afe/q6asm/q6adm. Mechanism: of_register_apr_devices() in drivers/soc/qcom/apr.c.
first-learned: 2026-08-25
---

**Symptom** — you add an APR service node to the SoC dtsi, the DTB contains it,
`/sys/firmware/devicetree/base/.../apr/service@N` is there with the right `reg`,
and no device ever appears under `/sys/bus/aprbus/devices`. Your driver never
probes. Nothing is logged, because nothing failed.

**Cause** — `of_register_apr_devices()` runs twice with different rules. Called
with no service path (from `apr_probe`) it registers only children that have
**no** `qcom,protection-domain`; called again from the PDR callback it registers
only children whose PD has just come up. A node with a PD that never comes up is
therefore skipped by both passes, silently.

Boards whose remoteproc firmware does not host the PD delete the property to get
the first pass to take the service. A node you add to the SoC dtsi inherits the
property and does not inherit that deletion. On msm8998-google-taimen the board
dts carries four `/delete-property/ qcom,protection-domain;` overrides with a
comment explaining that the locator answers but never lists `avs/audio`.

**What to do** — compare the **live** properties of your node against a working
sibling, not the DTS against itself:

    for s in /sys/firmware/devicetree/base/.../apr/service@*; do
        printf '%s ' "$(basename "$s")"
        tr '\0' ',' < "$s/qcom,protection-domain" 2>/dev/null || echo '(none)'
    done

If the working services print `(none)` and yours prints a PD, add the same
`/delete-property/` override for your node.

The general shape is worth carrying past APR: a DT node that is present and
correct, and a device that is still absent, means something is *filtering*
nodes, not rejecting them. Find the loop that iterates the children and read
what it skips.

See also [[dtbo-must-match-the-kernel]] and [[timestamps-cannot-prove-a-build-is-fresh]] --
both are the same lesson: check the artefact that is running, not the one you
believe you built.
