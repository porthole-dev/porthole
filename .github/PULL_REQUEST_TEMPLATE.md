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
- [ ] no trailers on any commit — no `Signed-off-by:`, no AI attribution

## Device coverage

<!-- Which device did you test on? "None, host-only change" is a fine answer. -->
