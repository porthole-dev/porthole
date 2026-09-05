---
id: webkit-recomposites-the-whole-viewport-because-damage-clipping-is-off-by-default
title: Epiphany recomposites the whole viewport every frame: WebKit collects damage but UseDamagingInformationForCompositing defaults off, and UnifyDamagedRegions makes a playing video's damage the whole viewport
scope: device:google-taimen
subsystem: browser
severity: finding
confidence: probable
evidence: 2026-09-04, webkitgtk 2.52.6 source (Source/WTF/Scripts/Preferences/UnifiedWebPreferences.yaml): PropagateDamagingInformation default true on GTK, UnifyDamagedRegions default true on GTK, UseDamagingInformationForCompositing default false on ALL platforms, all gated ENABLE(DAMAGE_TRACKING) (compiled in: the 2.52 fork exists for it). ThreadedCompositor.cpp: setFrameDamage() always sends the collected damage to the system compositor (phoc), but WebKit only clips its OWN TextureMapper compositing when DamagePropagationFlags::UseForCompositing is set, which comes only from useDamagingInformationForCompositing(). TextureMapperLayer.cpp collectDamageSelf(): a layer with a content buffer (the decoded video layer) calls damageWholeLayer() every frame ('Layers with content layer are fully damaged for now. FIXME: Remove that special case.'); with UnifyDamagedRegions the frame damage is the BoundingBox of all damage, so video + any other update = ~whole viewport, which is why enabling UseForCompositing alone was measured neutral 09-02.
refutes: the browser is slow because of a5xx per-draw cost or GPU clock; enabling UseDamagingInformationForCompositing is neutral so damage cannot help; the whole-viewport repaint is inherent to WebKit
first-learned: 2026-09-04
---

**The question** -- Epiphany is slow and hot on YouTube; the GPU stays at
515-710 MHz and the die climbs even with nothing but video playing. Where
does the per-frame GPU work come from, and is it fixable without a hack?

**The answer** -- WebKit recomposites the whole viewport every frame. Damage
tracking is compiled in and on (`PropagateDamagingInformation` true on GTK),
but it is used only to tell phoc which part of the window changed; WebKit's
own TextureMapper still composites every layer over the whole surface,
because `UseDamagingInformationForCompositing` defaults false on every
platform. And `UnifyDamagedRegions` defaults true, so the damage is a single
bounding box; a playing video's content layer is marked fully damaged every
frame (`collectDamageSelf`, the `FIXME: Remove that special case`), and the
bounding box of the video plus any scrubber/spinner update is essentially the
whole viewport -- which is why turning `UseForCompositing` on by itself was
measured neutral on 09-02.

**What this rules out** -- "a5xx per-draw/BO cost" and "GPU clock" as the
browser ceiling (both measured neutral 09-02), and "damage can't help". The
fix is two preference changes, upstream-shaped, not a hack: turn
`UseDamagingInformationForCompositing` on AND `UnifyDamagedRegions` off, so
WebKit clips its own compositing to the actual changed rectangles rather than
their bounding box. Then a playing video composites the video rect, not the
page. The content-layer `damageWholeLayer()` stays (a video frame genuinely
changes wholly); the win is not uniting it with the rest.

**How to confirm before the 5.5 h webkit build** -- `WEBKIT_SHOW_DAMAGE=1`
(TextureMapperDamageVisualizer) paints the damage region; a screenshot during
playback shows whether it is the whole viewport (BoundingBox, as predicted)
or just the video. `WEBKIT_SHOW_FPS=1` is the companion. The three prefs are
`status: testable`, exposed through the WebKitFeature API; a small Epiphany or
WebKitSettings shim can flip them at runtime for the A/B without rebuilding.

**Not yet done** -- the runtime A/B and the visualizer capture; whether
Rectangles mode floods phoc with too many rects (the reason Unified is the
GTK default) and whether the two damage uses need decoupling so the system
compositor still gets a unified rect while WebKit clips on rectangles.
