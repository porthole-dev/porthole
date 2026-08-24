---
id: a-board-name-is-not-a-soc-name
title: A vendor's reference board is not the SoC, and tools will accept it silently
scope: generic
subsystem: bringup
severity: trap
confidence: proven
evidence: `porthole new-device --device google-cheetah --soc cloudripper` scaffolded an unseeded profile with no complaint (2026-08-24). gs201-cheetah-common.dtsi includes eleven gs201-cloudripper-*.dtsi files: cloudripper is the reference board that cheetah and panther derive from, and gs201 is the silicon.
first-learned: 2026-08-24
---

Vendor kernel trees are organised by **board**, not by SoC. Google's gs201 tree
has `cloudripper` (the reference board), `cheetah` and `panther` (the shipping
Pixel 7 Pro and Pixel 7), and `pantah` (the pair). Qualcomm trees do the same
thing with MTP and QRD names. Only one of those names is the silicon.

Picking the wrong one is easy, and the failure is quiet: you ask a tool to seed
from "the closest device on this SoC", it finds nothing because no such SoC
exists, and it hands you an empty profile that looks exactly like the empty
profile you would get from correct-but-unported silicon.

Two habits make this cost nothing:

1. **The SoC is what the kernel's `arch/*/boot/dts` directory and the
   `soc-<vendor>-<name>` package are named after.** If pmaports has no
   `soc-` package with your string in it, and no kernel dtsi is named for it,
   it is probably a board.
2. **Never accept "no results" as an answer without a message.** porthole now
   warns loudly when `--soc` matches nothing and states in the summary that no
   sibling seeding happened — an unseeded profile must never be
   indistinguishable from a seeded one.

Note the sharp edge on the obvious fix: a successor SoC is *one character* from
its predecessor by design (`google-gs201` vs `google-gs101`), so "this looks
like a typo of a known SoC" cannot distinguish a typo from the new silicon you
are actually porting. Refusing near-misses rejects exactly the ports the tool
exists for. Warn, name the near match, and **do not seed** — a named SoC that
turns out not to exist must never fall back to inferring one from the vendor.
