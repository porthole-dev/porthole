#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: a `perf script -F sym` dump; run anywhere
# env: -
# exits: 0 ok
"""wkbuckets.py PERF-REPORT-FILE -- what phase of WebKit owns the main thread.

A flat `perf report` of WebKit is useless: the work is spread over thousands of
small functions and nothing clears 2%. Bucketing the SAME lines by what each
symbol belongs to answers the question the flat list cannot -- style, layout,
JS, raster or compositing.

Feed it the output of:

    perf report -i PERF.DATA --stdio --no-children --sort symbol --percent-limit 0

and NOT `perf script -F sym`, which prints blank lines on this build.
"""
import collections
import re
import sys

# First match wins, so order is the priority: the specific before the general.
BUCKETS = [
    ("style",       r"WebCore::Style|::resolveStyle|StyleResolver|CSSSelector|SelectorChecker|::styleForElement|MatchedProperties|CSSValue|StyleBuilder"),
    ("layout",      r"WebCore::Layout|::layout\(|RenderBlock|RenderBox|RenderFlex|RenderTable|RenderInline|RenderText|LineLayout|InlineFormatting|::computeLogical"),
    ("js",          r"JSC::|::jsc_|llint_|LLInt|DFG::|FTL::|Yarr::|operationJS"),
    ("raster",      r"^Sk|::Sk|skia|SkOpts|GrDirectContext|SkRasterPipeline|SkBlitter|SkDraw|SkCanvas"),
    ("compositing", r"TextureMapper|Nicosia|CoordinatedGraphics|ThreadedCompositor|GraphicsLayer|::flushCompositingState|TiledBacking|::updateBacking"),
    ("paint",       r"::paint\(|PaintInfo|GraphicsContext|DisplayList|::paintContents"),
    ("dom",         r"WebCore::Element|WebCore::Node|WebCore::Document|WebCore::HTML|WebCore::Text|ContainerNode|::dispatchEvent"),
    ("images",      r"ImageDecoder|BitmapImage|::decodeFrame|CairoImage|JPEG|PNG|WebP|::nativeImage"),
    ("scrolling",   r"Scrolling|ScrollAnimator|::scrollTo|EventHandler|::handleTouch|::handleWheel"),
    ("malloc",      r"fastMalloc|fastFree|bmalloc|::allocateSlow|pas_|malloc|free$|operator new"),
    ("memops",      r"^memcpy$|^memset$|^memmove$|^memcmp$|^strlen$|^__memcpy"),
    ("wtf",         r"WTF::"),
    ("gl/mesa",     r"fd5_|fd_|ir3_|^tu_|nir_|glDraw|eglSwap|dri_|^u_|pipe_"),
]
COMPILED = [(n, re.compile(p)) for n, p in BUCKETS]

counts = collections.Counter()
kernel = collections.Counter()
unknown = collections.Counter()
total = 0.0
# "     2.18%  [k] _raw_spin_unlock_irq" -- percentage, DSO marker, symbol.
# NB (.*) then rstrip, never (.*?)\s*$ -- the lazy form backtracks catastrophically
# on perf's space-padded, thousand-character demangled C++ symbols.
ROW = re.compile(r"^\s*(\d+\.\d+)%\s+\[([.k])\]\s+(.*)$")
for line in open(sys.argv[1], errors="replace"):
    m = ROW.match(line)
    if not m:
        continue
    pct, dso, sym = float(m.group(1)), m.group(2), m.group(3).rstrip()
    total += pct
    if dso == "k":
        kernel[sym[:60]] += pct
        counts["KERNEL"] += pct
        continue
    for name, rx in COMPILED:
        if rx.search(sym):
            counts[name] += pct
            break
    else:
        counts["other"] += pct
        unknown[sym[:70]] += pct

if not total:
    sys.exit("no perf report rows matched -- wrong input format?")
print(f"accounted={total:.1f}% of samples")
for name, n in counts.most_common():
    print(f"  {100.0 * n / total:5.1f}%  {name}")
print("\ntop unbucketed symbols:")
for sym, n in unknown.most_common(25):
    print(f"  {100.0 * n / total:5.1f}%  {sym}")
print("\ntop kernel symbols:")
for sym, n in kernel.most_common(8):
    print(f"  {100.0 * n / total:5.1f}%  {sym}")
