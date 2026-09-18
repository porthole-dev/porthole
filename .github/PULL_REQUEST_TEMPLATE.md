## What this changes

## Why

<!-- The body of a commit here should explain why, not what. -->

## Checklist

- [ ] `make ci` passes (not just `make check` -- CI runs more)
- [ ] new/changed tools declare `scope:`, `needs:`, `env:`, `exits:`
- [ ] `make test` is green, which runs the secrets scan over every tracked
      file. It catches host paths, `<user>@<ipv4>`, labelled serials and IMEIs,
      key material, tokens, factory MAC addresses and device screenshots.
- [ ] **read the diff yourself for what no regex can see**: a bare serial with
      no label, the SSID of a network you do not own, a location. The list is
      `docs/HANDOFF-contribution-rules.md` section 4.5; redact to a stable
      placeholder rather than deleting the evidence.
- [ ] device-specific probes live in `profiles/<codename>/tools/`, not `tools/`
- [ ] non-trivial logic leaves one runnable check behind
- [ ] **if an agent wrote or reviewed this**: it says which claims it verified by
      execution and which it only read (`state-what-you-verified`). The failure
      mode is not rudeness, it is a confident review of code nobody ran.
- [ ] a lesson that generalises became a `brain/` note with its evidence
- [ ] if an assistant helped, that is disclosed with `Assisted-by:` — no AI
      co-author or sign-off, no "generated with" line and no session URL on any
      commit **or in this body**. #51 and #52 published them here while the
      hook held the message. A `Signed-off-by:` is **not** required on a branch
      of this repository: a Code-Owner review and the merge certify those. It
      **is** required on a pull request from a fork, and on a series bound
      upstream.
- [ ] **the work was finished before this was opened.** A finding written
      partway through is a draft — the a540 note was reversed by its own next
      measurement. Assess, then file.
- [ ] `git config core.hooksPath .githooks` is set in the clone this came from

## Device coverage

<!-- Which device did you test on? "None, host-only change" is a fine answer. -->
