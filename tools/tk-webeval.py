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
sockets = re.findall(r"/socket/(\d+)/(\d+)/(\w+)", html)
if not sockets:
    print("no inspector targets", file=sys.stderr); sys.exit(1)

# WebKit exposes more than one target: alongside the real page there is often an
# about:blank one, and taking the last socket -- which this did -- lands on it
# roughly half the time. The symptom is brutal to debug because everything looks
# healthy: the inspector answers, document.readyState is "complete", and only
# movie_player is missing, so an arm reads as "the video would not play" when
# the page is in fact playing perfectly in the OTHER target. Several arms were
# voided this way on 2026-09-05 before the URL was printed.
#
# So evaluate in the target that is actually showing something. TK_TARGET_URL
# overrides the default "anything that is not about:blank".
WANT = os.environ.get("TK_TARGET_URL", "")

def open_target(sock):
    cid, tid, typ = sock
    s = vq.ws_connect(vq.HOST, f"/socket/{cid}/{tid}/{typ}")
    vq.ws_send(s, json.dumps({"id": 0, "method": "Target.setPauseOnStart", "params": {"pauseOnStart": False}}))
    page = None
    while page is None:
        r = json.loads(vq.ws_recv(s))
        if r.get("method") == "Target.targetCreated": page = r["params"]["targetInfo"]["targetId"]
    return s, page

# Anything gated on a user gesture -- requestFullscreen(), unmuted play() --
# is refused from a plain inspector eval, and the refusal looks exactly like the
# call not working. TK_GESTURE=1 marks the evaluation as user-initiated, which
# is how the fullscreen video path can be measured without synthesising a tap on
# a button whose position depends on the page.
GESTURE = os.environ.get("TK_GESTURE", "") not in ("", "0")

def run(s, page, expression, msg_id):
    inner = json.dumps({"id": msg_id, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True, "emulateUserGesture": GESTURE}})
    vq.ws_send(s, json.dumps({"id": 1000 + msg_id, "method": "Target.sendMessageToTarget", "params": {"targetId": page, "message": inner}}))
    while True:
        r = json.loads(vq.ws_recv(s))
        if r.get("method") == "Target.dispatchMessageFromTarget":
            mm = json.loads(r["params"]["message"])
            if mm.get("id") == msg_id: return mm

chosen = None
for sock in reversed(sockets):
    try:
        s, page = open_target(sock)
        probe = run(s, page, "location.href", 9)
        href = probe.get("result", {}).get("result", {}).get("value", "") or ""
    except Exception:
        continue
    if (WANT and WANT in href) or (not WANT and not href.startswith("about:")):
        chosen = (s, page, href); break
    try: s.close()
    except Exception: pass
if chosen is None:
    # Nothing matched; fall back to the last target rather than failing outright,
    # but say so, because a caller measuring about:blank gets a perfect score.
    print("warning: no target matched (%s); falling back" % (WANT or "non-about:"), file=sys.stderr)
    s, page = open_target(sockets[-1])
else:
    s, page, href = chosen
    if os.environ.get("TK_TARGET_VERBOSE"): print("target: %s" % href[:70], file=sys.stderr)

m = run(s, page, expr, 1)
if "error" in m: print("error:", m["error"], file=sys.stderr); sys.exit(1)
res = m["result"]
if res.get("wasThrown"): print("thrown:", res["result"].get("description"), file=sys.stderr); sys.exit(1)
v = res["result"].get("value", res["result"].get("description"))
print(json.dumps(v) if as_json else v)
