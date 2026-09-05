---
id: wlroots-never-re-imports-a-texture-after-a-renderer-swap
title: wlroots drops every client texture on a renderer swap and never re-imports one, so static layer-surfaces stay blank after a GPU reset
scope: generic
subsystem: graphics
severity: finding
confidence: proven
evidence: wlroots 0.20.2 types/buffer/client.c and types/wlr_compositor.c read end to end 2026-09-04; the same code is unchanged on wlroots master
refutes: repainting every output after recreate_renderer is enough; the stale texture cache is phoc's; wlr_compositor_set_renderer re-imports textures; the dmabuf/shm buffers are gone after a reset
first-learned: 2026-09-04
---

**The question** — after the a540 faults and the msm driver resets it, phoc
recreates its renderer (`Re-creating renderer after GPU reset`, 32 times in
seven days on this device) and, with the repaint fix in place, correctly
repaints every output. It then draws **nothing** where the wallpaper, the
phosh panel and the on-screen keyboard were. A browser comes back on its own.
Where does the missing image live?

**The answer** — in a `wlr_client_buffer` whose texture wlroots deliberately
threw away and never puts back. Three functions, all in wlroots, and none of
them is wrong on its own:

- `client_buffer_handle_renderer_destroy()` (`types/buffer/client.c`) sets
  `client_buffer->texture = NULL` when the renderer that imported it dies.
  That is correct: it is what stops a dangling pointer.
- `wlr_compositor_set_renderer()` (`types/wlr_compositor.c`) swaps the
  renderer pointer and re-arms the destroy listener. **That is all it does.**
- `surface_apply_damage()` is the only path that ever calls
  `wlr_client_buffer_create()`, and it runs on a **client commit**.

So `wlr_surface_get_texture()` returns NULL for every existing surface until
that client commits again. A client that animates commits within a frame or
two and recovers by itself. A static layer-surface never commits at all, and
stays textureless for the rest of the session. The symptom is not a
compositor that stopped painting; it is a compositor that paints correctly and
has nothing to paint.

**What this rules out** —

- **"Damaging every output after `recreate_renderer()` fixes it."** It fixes
  the other half -- without it nothing repaints and the dead renderer's last
  framebuffer stays on screen -- and it cannot fix this half. A repaint that
  finds no texture draws no pixels.
- **"The stale cache is in the compositor, look in phoc."** phoc already calls
  `wlr_compositor_set_renderer()` and holds no texture of its own; its two
  `wlr_surface_get_texture()` call sites just read what wlroots hands them.
- **"Upstream will have fixed this by now."** `wlr_compositor_set_renderer()`
  is byte-for-byte the same on wlroots master as on 0.20.2. The bug is live
  upstream.
- **"The client's buffer is gone too, so there is nothing to re-import."** It
  is not. A dmabuf's fds and a shm pool's mapping both survive a GPU reset,
  and `client_buffer->source` is set to NULL only when the source buffer is
  actually destroyed. If `source` is non-NULL the pixels are still there.

**How it was established** — read, not measured: the three functions above,
in the wlroots 0.20.2 tree that phoc embeds
(`subprojects/wlroots-0.20.x`, fetched by the meson wrap at build time, so it
is in the release tarball but not in the aport directory). The fix that
follows from it is a lazy re-import in `wlr_surface_get_texture()` -- the one
path every consumer of a surface texture already goes through -- shipped as
`0004-wlroots-compositor-re-import-a-texture-lost-with-its-.patch` in
`temp/phoc`.

It would be overturned by finding another caller of
`wlr_client_buffer_create()`, or by a renderer whose textures outlive it.
Note that a renderer swap is **not** the same event as a GPU reset: see
[[the-msm-reset-debugfs-does-not-make-a-client-lose-its-context]] for why the
obvious trigger does not exercise this path.
