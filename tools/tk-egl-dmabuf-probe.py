#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (python3 + libEGL + libgbm; no compiler, no root)
# env: -
# exits: 0 ok · 1 no platform initialised
"""What dmabuf modifiers does EGL really advertise, per platform?

The waylandsink-5fps hunt (2026-09-01) needed exactly this and had to be
improvised three times: mesa reported explicit LINEAR on every platform while
the compositor advertised implicit-only to clients, which located the filter
in wlroots rather than mesa. A compositor bug and a mesa bug look identical
from a client; this tool splits them in one run, with no compiler on the
device (pure ctypes).

Probes the GBM (render node), EGL-device and surfaceless platforms --
compositors use the device/surfaceless paths, allocators use GBM -- and for
each prints the dma_buf_import_modifiers extension presence and the modifier
list for NV12, XR24 and AR24.

  tk-egl-dmabuf-probe.py [/dev/dri/renderD128]
"""
import ctypes
import os
import sys

FORMATS = (("NV12", 0x3231564e), ("XR24", 0x34325258), ("AR24", 0x34325241))
EGL_EXTENSIONS = 0x3055

egl = ctypes.CDLL("libEGL.so.1")
egl.eglGetProcAddress.restype = ctypes.c_void_p
egl.eglQueryString.restype = ctypes.c_char_p

def proc(name, restype, *argtypes):
    p = egl.eglGetProcAddress(name.encode())
    return ctypes.CFUNCTYPE(restype, *argtypes)(p) if p else None

get_platform_display = proc("eglGetPlatformDisplayEXT", ctypes.c_void_p,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
query_modifiers = proc("eglQueryDmaBufModifiersEXT", ctypes.c_uint,
                       ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                       ctypes.POINTER(ctypes.c_uint64),
                       ctypes.POINTER(ctypes.c_uint),
                       ctypes.POINTER(ctypes.c_int))

def probe(dpy, label):
    maj, mnr = ctypes.c_int(), ctypes.c_int()
    if not dpy or not egl.eglInitialize(ctypes.c_void_p(dpy),
                                        ctypes.byref(maj), ctypes.byref(mnr)):
        print(f"{label}: init failed")
        return False
    exts = (egl.eglQueryString(ctypes.c_void_p(dpy), EGL_EXTENSIONS) or b"").decode()
    has = "EGL_EXT_image_dma_buf_import_modifiers" in exts.split()
    print(f"{label}: EGL {maj.value}.{mnr.value}, modifiers-ext={has}")
    # "mesa advertises nothing" is one of the two answers this tool exists to
    # give, and it is exactly the case where eglGetProcAddress hands back NULL
    # -- so calling through the pointer anyway turned the answer into a
    # TypeError traceback on the device where it mattered most.
    if query_modifiers is None:
        print("  no eglQueryDmaBufModifiersEXT entry point -- "
              "this EGL advertises no modifiers at all")
        return True
    for name, code in FORMATS:
        n = ctypes.c_int(0)
        ok = query_modifiers(dpy, code, 0, None, None, ctypes.byref(n))
        mods = (ctypes.c_uint64 * max(n.value, 1))()
        ext = (ctypes.c_uint * max(n.value, 1))()
        query_modifiers(dpy, code, n.value, mods, ext, ctypes.byref(n))
        print(f"  {name}: ok={ok} n={n.value} " +
              " ".join(hex(m) for m in mods[:n.value]))
    return True

def main():
    node = sys.argv[1] if len(sys.argv) > 1 else "/dev/dri/renderD128"
    any_ok = False
    if get_platform_display is None:
        print("no eglGetPlatformDisplayEXT -- this EGL cannot be probed by "
              "platform, and the compositor/mesa split is what this tool is for")
        sys.exit(1)
    try:
        gbm = ctypes.CDLL("libgbm.so.1")
        gbm.gbm_create_device.restype = ctypes.c_void_p
        fd = os.open(node, os.O_RDWR)
        dev = gbm.gbm_create_device(fd)
        any_ok |= probe(get_platform_display(0x31D7, dev, None), f"gbm({node})")
    except OSError as e:
        print(f"gbm: {e}")
    qdev = proc("eglQueryDevicesEXT", ctypes.c_uint, ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int))
    # Skipped, not fatal: surfaceless below is the platform a compositor
    # actually uses, and dying here would take that answer with it.
    if qdev is None:
        print("egl-device: no eglQueryDevicesEXT -- skipping this platform")
    else:
        n = ctypes.c_int()
        qdev(0, None, ctypes.byref(n))
        devs = (ctypes.c_void_p * max(n.value, 1))()
        qdev(n.value, devs, ctypes.byref(n))
        for i in range(n.value):
            any_ok |= probe(get_platform_display(0x313F, devs[i], None),
                            f"egl-device[{i}]")
    any_ok |= probe(get_platform_display(0x31DD, None, None), "surfaceless")
    sys.exit(0 if any_ok else 1)

if __name__ == "__main__":
    main()
