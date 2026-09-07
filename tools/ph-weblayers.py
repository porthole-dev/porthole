#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device; ph-webvq.py beside it; browser launched with WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222
# env: TK_INSPECTOR
# exits: 0 ok
"""Dump the composited layer tree of the current page through the remote inspector.

The compositor thread's per-frame cost tracks the layer tree, not the video:
this prints every composited layer with its size, whether it has its own
backing store, and WebKit's own reason for compositing it -- the number that
says why one page composites at 60 Hz and another at 20.
"""
import importlib.util, json, os, re, sys, urllib.request, collections
spec = importlib.util.spec_from_file_location("webvq", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ph-webvq.py"))
vq = importlib.util.module_from_spec(spec); spec.loader.exec_module(vq)
HOST = vq.HOST
html = urllib.request.urlopen(f"http://{HOST}/", timeout=5).read().decode()
cid, tid, typ = re.findall(r"/socket/(\d+)/(\d+)/(\w+)", html)[-1]
s = vq.ws_connect(HOST, f"/socket/{cid}/{tid}/{typ}")
vq.ws_send(s, json.dumps({"id": 0, "method": "Target.setPauseOnStart", "params": {"pauseOnStart": False}}))
page = None
while page is None:
    r = json.loads(vq.ws_recv(s))
    if r.get("method") == "Target.targetCreated": page = r["params"]["targetInfo"]["targetId"]
msgid = [0]
def call(method, params=None):
    msgid[0] += 1
    inner = json.dumps({"id": msgid[0], "method": method, "params": params or {}})
    vq.ws_send(s, json.dumps({"id": 1000 + msgid[0], "method": "Target.sendMessageToTarget", "params": {"targetId": page, "message": inner}}))
    while True:
        r = json.loads(vq.ws_recv(s))
        if r.get("method") == "Target.dispatchMessageFromTarget":
            m = json.loads(r["params"]["message"])
            if m.get("id") == msgid[0]:
                if "error" in m: raise SystemExit("%s: %s" % (method, m["error"]))
                return m.get("result", {})
call("LayerTree.enable")
doc = call("DOM.getDocument", {"depth": 0})
root = doc["root"]["nodeId"]
layers = call("LayerTree.layersForNode", {"nodeId": root}).get("layers", [])
area = 0; backing = 0
rows = []
for l in layers:
    b = l.get("bounds", {}); w, h = b.get("width", 0), b.get("height", 0)
    comp = l.get("compositedBounds", {}); cw, ch = comp.get("width", w), comp.get("height", h)
    area += cw * ch
    reasons = "-"
    try:
        rs = call("LayerTree.reasonsForCompositingLayer", {"layerId": l["layerId"]}).get("compositingReasons", {})
        reasons = ",".join(k for k, v in rs.items() if v)
    except SystemExit:
        pass
    rows.append((cw * ch, cw, ch, l.get("paintingEnabled", None), l.get("isInShadowTree", False), l.get("nodeId"), reasons))
rows.sort(reverse=True)
print("composited layers: %d   total composited area: %.1f Mpx   (screen 1440x2880 = 4.1 Mpx)" % (len(layers), area / 1e6))
reason_count = collections.Counter()
for r in rows:
    for k in r[6].split(","): reason_count[k] += 1
print("reasons:", reason_count.most_common(12))
print("largest layers (area, w x h, reasons):")
for r in rows[:15]: print("  %6.2f Mpx  %5dx%-5d  %s" % (r[0] / 1e6, r[1], r[2], r[6][:100]))
