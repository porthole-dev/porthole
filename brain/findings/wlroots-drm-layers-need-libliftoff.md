---
id: wlroots-drm-layers-need-libliftoff
title: wlroots does DRM output layers only through libliftoff
scope: generic
subsystem: display
severity: finding
confidence: proven
evidence: backend/drm/drm.c:812 returns early with 'libliftoff is disabled'; backend/drm/atomic.c has no reference to layers; meson.options has libliftoff at 'auto' and phoc never pulled it in; adding -Dwlroots:libliftoff=enabled put so:libliftoff.so.0 in the apk and WLR_DRM_FORCE_LIBLIFTOFF=1 then modeset cleanly on msm8998
refutes: the wlr_output_layer symbols are in the binary, so the compositor can use hardware planes
first-learned: 2026-09-05
---

**The question** — my compositor wants to put a client's buffer on a hardware
overlay plane. `nm` says the `wlr_output_layer_*` symbols are right there in the
binary, `/sys/kernel/debug/dri/0/state` says seven planes are idle, the backend
is atomic, and every layer still comes back `accepted = false`. Why?

**The answer** — because the symbols are not the implementation. `wlr_output_layer`
and `wlr_output_state_set_layers()` live in generic `types/output/`, so they are
in *every* wlroots build. The DRM backend implements them **only** in
`backend/drm/libliftoff.c`. `backend/drm/atomic.c` contains no reference to
layers at all, and `drm.c` has:

    static bool drm_connector_set_pending_layer_fbs(...) {
            ...
            if (!crtc->liftoff) {
                    return true; // libliftoff is disabled
            }

which returns success having done nothing, so every layer stays unaccepted and
the compositor silently composites everything on the GPU forever.

Two things are needed, and neither is the default:

  - build with libliftoff — `meson.options` declares it `type: 'feature',
    value: 'auto'`, so a build host without the library disables it silently.
    Pass it explicitly (`-Dwlroots:libliftoff=enabled` for an embedded wlroots)
    so a missing library fails the build instead of shipping an inert one.
  - select the interface at runtime with **`WLR_DRM_FORCE_LIBLIFTOFF=1`**.
    Without it `drm_backend_init()` picks `atomic_iface`, which has no layers.
    `env_parse_bool()` reads `"0"` as false, so that value is the off switch.

**What this rules out** — "the symbols are present, so the feature is present".
Also "the compositor must be computing the wrong geometry", "the DPU must be
refusing the format", and "the kernel is rejecting the atomic commit": with the
atomic interface the kernel is never asked about a layer in the first place, and
`drm.debug=0x10` shows the commit carrying `[NOFB]` on every overlay plane with
no failure line anywhere.

**How it was established** — read in wlroots 0.20.x, then confirmed on a
Pixel 2 XL (msm8998, mainline 7.2). Enabling the option put
`depend = so:libliftoff.so.0` in the phoc apk, which is the cheap check that the
`auto` feature actually resolved; forcing the interface then modeset cleanly at
1440x2880 and put a client surface on `plane-7`. What would overturn it: a
wlroots release that teaches `atomic.c` to program overlay planes directly, at
which point the forced interface becomes unnecessary rather than wrong.
