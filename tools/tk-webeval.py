#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device; tk-webvq.py beside it; browser launched with WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222
# env: TK_INSPECTOR
# exits: 0 ok · 1 error
"""Evaluate JavaScript in the current page through WebKit's remote inspector.

A tap at a coordinate is not an instrument: it lands on a consent wall, a
paused player or the app grid and nothing in the numbers says so. This asks
the page. `tk-webeval.py "document.title"`; with -j the result is printed as
JSON so a script can branch on it.
  tk-webeval.py [-j] EXPRESSION
"""
import importlib.util, json, os, re, sys, urllib.request
spec = importlib.util.spec_from_file_location("webvq", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk-webvq.py"))
vq = importlib.util.module_from_spec(spec); spec.loader.exec_module(vq)
args = sys.argv[1:]; as_json = False
if args and args[0] == "-j": as_json = True; args = args[1:]
expr = " ".join(args)
html = urllib.request.urlopen(f"http://{vq.HOST}/", timeout=5).read().decode()
cid, tid, typ = re.findall(r"/socket/(\d+)/(\d+)/(\w+)", html)[-1]
s = vq.ws_connect(vq.HOST, f"/socket/{cid}/{tid}/{typ}")
vq.ws_send(s, json.dumps({"id": 0, "method": "Target.setPauseOnStart", "params": {"pauseOnStart": False}}))
page = None
while page is None:
    r = json.loads(vq.ws_recv(s))
    if r.get("method") == "Target.targetCreated": page = r["params"]["targetInfo"]["targetId"]
inner = json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}})
vq.ws_send(s, json.dumps({"id": 1001, "method": "Target.sendMessageToTarget", "params": {"targetId": page, "message": inner}}))
while True:
    r = json.loads(vq.ws_recv(s))
    if r.get("method") == "Target.dispatchMessageFromTarget":
        m = json.loads(r["params"]["message"])
        if m.get("id") == 1: break
if "error" in m: print("error:", m["error"], file=sys.stderr); sys.exit(1)
res = m["result"]
if res.get("wasThrown"): print("thrown:", res["result"].get("description"), file=sys.stderr); sys.exit(1)
v = res["result"].get("value", res["result"].get("description"))
print(json.dumps(v) if as_json else v)
