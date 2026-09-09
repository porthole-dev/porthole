---
id: chromium-segfaults-because-a-phone-sends-no-xkb-keymap
title: Chromium segfaults on every launch under phoc because a phone with no physical keyboard is sent no wl_keyboard.keymap
scope: generic
subsystem: browser
severity: finding
confidence: proven
evidence: six SIGSEGV coredumps, one backtrace naming xkb_state_update_mask, and a WAYLAND_DEBUG trace showing enter+modifiers with no keymap event
refutes: the Alpine chromium runs here and is merely desktop-shaped; the crash is the VA-API path failing to init; the crash is in the GPU process; the crash is the musl sandbox; use_v4l2_codec alone is enough to get hardware decode in a usable browser
first-learned: 2026-09-09
---

**The question** — Chromium on this phone dies instantly. Alpine's
`chromium-152.0.7977.82-r0` from edge/community exits 139 the moment it is
launched, on every attempt, with a window that never appears. Is it the GPU,
the sandbox, VA-API, musl, or the build?

**The answer** — none of those. It is the **keyboard**, and it is a property of
the device class rather than of the build.

`WAYLAND_DEBUG=1` shows the whole seat exchange:

    wl_seat#30.capabilities(6)                       # keyboard|touch, no pointer
     -> wl_seat#30.get_keyboard(new id wl_keyboard#9)
    wl_keyboard#9.enter(842, wl_surface#36, array[0])
    wl_keyboard#9.modifiers(843, 0, 0, 0, 0)

There is **no `wl_keyboard.keymap` event**. phoc advertises the keyboard
capability -- an on-screen keyboard needs it -- but this device has no physical
keyboard, so no keymap is ever sent. Chromium therefore never builds an
`xkb_state`, and the first `modifiers` event dereferences it:

    Stack trace of thread 11979:
    #0  xkb_state_update_mask (libxkbcommon.so.0 + 0x23b34)   si_code: SEGV_MAPERR
    #1  n/a (chromium + 0x92e608c)
    ...
    #11 n/a (libglib-2.0.so.0 + 0x56e60)

GTK tolerates a null keymap; Chromium does not. That asymmetry is the whole
reason Epiphany has always been fine here while Chromium is unusable, and it is
why this looks like "Chromium is broken on postmarketOS" rather than what it
is: a desktop assumption that every seat with a keyboard capability has a
keymap.

**What this rules out** —

- *"Alpine's chromium works, it is just desktop-shaped."* It does not work. It
  segfaults on launch, six times out of six.
- *"It is the VA-API path."* `vaInitialize failed: unknown libva error` does
  appear immediately before the crash and is a red herring.
  `--disable-features=AcceleratedVideoDecoder` does not save it.
- *"It is the GPU process."* `--disable-gpu` still exits 139. So does
  `--disable-features=Vulkan`.
- *"It is the musl sandbox."* `--no-sandbox` still exits 139.
- *"It is xkb data missing."* `/usr/share/X11/xkb/rules/evdev` is present. The
  keymap is never *requested* to be built, because the event never arrives.
- *"Building with use_v4l2_codec=true gets you hardware decode."* It gets you a
  decoder in a browser that cannot open a window. The keymap crash is upstream
  of every video question and has to be fixed first.

The core is fine, which is what localises it to the GUI path:
`chromium --version` exits 0, and `chromium --headless --dump-dom about:blank`
exits 0.

**How it was established** — `coredumpctl info` on the crash
(`/usr/lib/chromium/chromium`, SIGSEGV, SEGV_MAPERR) gives frame #0 directly;
Alpine builds at `symbol_level=0` so the chromium frames are bare offsets, but
frame #0 is in libxkbcommon and needs no symbols. `WAYLAND_DEBUG=1` then shows
the missing event. The two together are the proof: a null state, and the reason
it is null.

**What would overturn it** — a `wl_keyboard.keymap` line appearing in that
trace, or Chromium surviving with the keymap still absent. The direct
confirmation to run when a device is available is to hold a uinput keyboard
open (the `ph-key.py` ioctl idiom, `UI_DEV_CREATE` then sleep) so wlroots has a
real keyboard on the seat, and launch Chromium again: a keymap should appear
and the crash should go. That experiment was set up but not completed -- the
phone suspended first -- so this note's confidence rests on the coredump and
the protocol trace, not on it.

**The fix, when someone takes it on** — null-guard the xkb state in
`ui/ozone/platform/wayland/host/wayland_keyboard.cc` before
`xkb_state_update_mask`, carried as a patch in the chromium aport. It is small
and genuinely upstreamable: every handheld running wlroots has this shape.
Fixing phoc to always send a keymap would also work and is arguably more
correct, but it fixes one compositor rather than every Chromium-based app.

Related: [[va-api-cannot-wrap-a-stateful-v4l2-decoder]],
[[hardware-decode-works-in-webkit-the-ceiling-is-webkits-process-count]].
