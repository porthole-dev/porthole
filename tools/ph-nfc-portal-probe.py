#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (piped over ssh); a running portal with the NFC interface
#        (Task 6 installs it); NFC already switched on by the user in
#        Settings, or busctl -- OpenNFCRemote cannot do that itself, which is
#        exactly the claim this probe checks
# env: -
# exits: 0 filter holds · 1 filter is wrong · 2 could not reach a verdict
#
# Touched check_filter() (or open_remote(), or anything it calls)? Run
# `--selftest` before trusting the next real run. Nothing else covers this
# file -- it has no test in `make ci`, and a real run against hardware only
# tells you the CURRENT filter is right, never that the ASSERTION LOGIC
# itself would still catch a wrong one. `--selftest` is that check: it runs
# check_filter() against a CLOSED, an OPEN and a NOTHING-permitted proxy on
# a private bus and asserts it tells them apart.
"""Assert what an app on the NFC portal's fd can and cannot do.

The portal's whole security claim is one xdg-dbus-proxy filter: a permitted
app may read the adapter's properties, poll for tags, and enumerate them --
and may NOT write Adapter.Powered. That is a claim about a running proxy on
real hardware, not about a source file, so it gets a runtime probe rather
than an inspection (see docs/superpowers/specs/2026-09-10-nfc-toggle-and-
portal-design.md, "Filter rules").

Both halves are asserted, deliberately. A proxy that permitted nothing at all
would sail through a test that only checked the refusal, and would be just as
broken as one that permits everything -- the whole design point of dropping
a `disable-nfc` lockdown key is that the ALLOWED calls still work.

Exit 1 means one thing only: the filter is provably wrong (an allowed call
was denied with AccessDenied, or the forbidden Set succeeded). NOTHING ELSE
is allowed to produce it -- not a missing session bus, not a dead proxy
mid-handshake or mid-run, not the radio being off, not an unexpected
exception anywhere in open_remote() or check_filter(). All of those collapse
into exit 2 instead ("could not reach a verdict"), because "something kept
this run from reaching a verdict" is a different problem from "a verdict was
reached and it says the filter is broken" -- conflating them is exactly what
review caught here three times across two rounds, most recently
`bus_get_sync()` itself throwing when this tool's own documented "piped over
ssh" invocation runs without a usable `DBUS_SESSION_BUS_ADDRESS`.

  ph-nfc-portal-probe.py             the real probe: OpenNFCRemote, assert,
                                      try to catch a real tag while polling
  ph-nfc-portal-probe.py --selftest  proves this script's own assertion
                                      logic would fail an OPEN filter and not
                                      just pass a CLOSED one -- see the big
                                      comment on selftest() for why this
                                      exists and how it stays safe to run
                                      anywhere, including against real
                                      hardware, without touching the radio.
"""
import contextlib
import os
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GObject", "2.0")
from gi.repository import Gio, GLib, GObject

NEARD = "org.neard"
ADAPTER_PATH = "/org/neard/nfc0"
ADAPTER_IFACE = "org.neard.Adapter"
OBJMGR_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPS_IFACE = "org.freedesktop.DBus.Properties"


def _err(e):
    """The GDBus error name, if there is one, else the raw message. What
    distinguishes 'the filter refused this' (org.freedesktop.DBus.Error.
    AccessDenied, from xdg-dbus-proxy) from 'neard refused this for its own
    reasons' (e.g. the radio is off -- observed on this device as "No such
    device" from a real StartPollLoop against Powered=false) is the error
    name, and a future reader of a failing run needs to see it."""
    name = Gio.dbus_error_get_remote_error(e)
    return name if name else e.message


def check_filter(conn, bus_name=NEARD, path=ADAPTER_PATH, iface=ADAPTER_IFACE):
    """Assert both halves of the filter against an already-open connection.

    Returns one of three strings, not a bool -- round 1 review found that
    collapsing "the filter is provably wrong" and "something failed for a
    reason that has nothing to do with the filter" (the radio being off,
    say) into one False conflated two different problems with two different
    fixes, the same mistake the exit-code contract exists to avoid at the
    process level:

      'ok'           both halves hold: the three allowed calls succeeded and
                     Set was refused specifically with AccessDenied.
      'wrong'        proven: an allowed call was denied with AccessDenied
                     (the filter is too closed), or Set succeeded (the
                     filter is too open). Either is real proof.
      'inconclusive' an allowed call failed for a reason that is NOT
                     AccessDenied, or Set was refused by something that is
                     NOT AccessDenied. This says nothing about the filter
                     either way -- e.g. the radio being off makes
                     StartPollLoop fail with neard's own "No such device",
                     nothing to do with xdg-dbus-proxy.

    'wrong' always wins over 'inconclusive' once seen: one proven failure is
    definitive regardless of what else was ambiguous.
    """
    verdict = "ok"

    def downgrade(label, e, allowed):
        nonlocal verdict
        name = _err(e)
        if allowed and "AccessDenied" in name:
            print("FAIL     %s was refused by the filter: %s" % (label, name))
            verdict = "wrong"
        else:
            print("FAIL     %s (%s) -- inconclusive, not a filter verdict"
                  % (label, name))
            if verdict != "wrong":
                verdict = "inconclusive"

    try:
        conn.call_sync(
            bus_name, path, PROPS_IFACE, "Get",
            GLib.Variant("(ss)", (iface, "Protocols")),
            None, Gio.DBusCallFlags.NONE, 5000, None)
        print("allowed  Properties.Get Protocols    -> ok")
    except GLib.Error as e:
        downgrade("Properties.Get Protocols", e, allowed=True)

    # `started` tracks whether the radio was actually left polling by this
    # function. Round 1 review, minor: if Start succeeds and Stop is then
    # refused, the old code reported the (correct) failure and walked away
    # with the poll loop still running on real hardware. Attempt the
    # cleanup regardless of the verdict.
    started = False
    try:
        conn.call_sync(
            bus_name, path, iface, "StartPollLoop",
            GLib.Variant("(s)", ("Initiator",)),
            None, Gio.DBusCallFlags.NONE, 5000, None)
        started = True
        conn.call_sync(
            bus_name, path, iface, "StopPollLoop", None,
            None, Gio.DBusCallFlags.NONE, 5000, None)
        print("allowed  StartPollLoop / StopPollLoop -> ok")
    except GLib.Error as e:
        downgrade("poll loop", e, allowed=True)
        if started:
            with contextlib.suppress(GLib.Error):
                conn.call_sync(
                    bus_name, path, iface, "StopPollLoop", None,
                    None, Gio.DBusCallFlags.NONE, 5000, None)

    # Round 1 review, Important 1 (resolved from this session's own
    # evidence, not re-tested here): could an AccessDenied on this Set be
    # coming from neard's OWN policy rather than the proxy, which would let
    # a wide-open filter still show a refusal here and pass this probe by
    # accident? No. neard's shipped org.neard.conf grants at_console="true"
    # -- the session user -- talk access with no polkit helper (see
    # brain/playbooks/55-nfc.md, "The session user can talk to neard
    # without polkit"). That is the documented mechanism the retired
    # taimen-nfc-toggle script and the Settings switch both rely on to
    # write Powered directly, with no proxy in the picture at all: neard's
    # own policy PERMITS this Set for this user. So an AccessDenied
    # reaching this call can only have come from xdg-dbus-proxy's filter.
    # If a future device ships a neard whose org.neard.conf is stricter for
    # the session user, this assumption needs re-checking directly (as the
    # session user, on the system bus, no proxy: does Set Powered succeed
    # at all?) before trusting an AccessDenied here as filter evidence.
    try:
        conn.call_sync(
            bus_name, path, PROPS_IFACE, "Set",
            GLib.Variant("(ssv)", (iface, "Powered", GLib.Variant("b", True))),
            None, Gio.DBusCallFlags.NONE, 5000, None)
        print("FAIL     Properties.Set Powered SUCCEEDED -- the filter is open")
        verdict = "wrong"
    except GLib.Error as e:
        name = _err(e)
        if "AccessDenied" in name:
            print("refused  Properties.Set Powered       -> ok (%s)" % name)
        else:
            downgrade("Properties.Set Powered was refused, but not by the "
                      "filter", e, allowed=False)

    return verdict


def watch_for_tag(conn, bus_name=NEARD, path=ADAPTER_PATH, iface=ADAPTER_IFACE,
                   timeout_s=15):
    """The device row of the spec's test table: with the poll loop started
    over the *proxied* connection, does a real tag's arrival reach the app as
    an InterfacesAdded signal?

    This is not just "does neard see the tag" -- Task 3 found and fixed a
    broadcast-scoping bug that would have delivered PropertiesChanged but
    swallowed InterfacesAdded, because neard's ObjectManager lives at '/' and
    an early filter draft scoped the broadcast under '/org/neard/*'. Seeing
    InterfacesAdded arrive here, through the proxy, is the only way to know
    that fix holds on real hardware. Returns the new object's path, or None.
    """
    loop = GLib.MainLoop()
    found = []

    def on_added(connection, sender, obj_path, iface_name, signal, params):
        added_path, ifaces = params.unpack()
        if "org.neard.Tag" in ifaces:
            found.append(added_path)
            loop.quit()

    sub_id = conn.signal_subscribe(
        None, OBJMGR_IFACE, "InterfacesAdded", "/", None,
        Gio.DBusSignalFlags.NONE, on_added)

    try:
        conn.call_sync(
            bus_name, path, iface, "StartPollLoop",
            GLib.Variant("(s)", ("Initiator",)),
            None, Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as e:
        conn.signal_unsubscribe(sub_id)
        print("tag       could not start the poll loop: %s" % _err(e))
        return None

    GLib.timeout_add_seconds(timeout_s, loop.quit)
    loop.run()
    conn.signal_unsubscribe(sub_id)

    with contextlib.suppress(GLib.Error):
        conn.call_sync(
            bus_name, path, iface, "StopPollLoop", None,
            None, Gio.DBusCallFlags.NONE, 5000, None)

    return found[0] if found else None


def open_remote():
    """The real path: ask the portal for a filtered connection to neard.
    Returns (Gio.DBusConnection, None) on success, or (None, error message).

    Round 2 review, Important 1: round 1 guarded only the
    fd-list-to-connection handshake (the two lines a Critical had just named)
    and left `bus_get_sync()` / `DBusProxy.new_sync()` above it bare --
    same bug class, two lines higher, in the very function the previous fix
    touched. `bus_get_sync()` throwing is not hypothetical for a tool whose
    own header says "piped over ssh": a session with no
    `DBUS_SESSION_BUS_ADDRESS` (or a stale one) hits exactly this.

    Rather than patch that one call and risk leaving a THIRD sibling for a
    future round, the whole function body is now one boundary: every GLib/
    GObject call that can throw between "we have nothing yet" and "we have a
    connection" is inside a single try/except. There is no longer a call in
    this function that is allowed to propagate.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        portal = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.NFC", None)

        reply, fd_list = portal.call_with_unix_fd_list_sync(
            "OpenNFCRemote", GLib.Variant("(a{sv})", ({},)),
            Gio.DBusCallFlags.NONE, -1, None, None)

        fd = fd_list.steal_fds()[reply.unpack()[0]]
        # NOT "unix:fd=%d" % fd via new_for_address_sync(): that address form
        # is for a GDBusServer to LISTEN on an already-open fd (socket
        # activation), not for a client to treat one as an already-connected
        # peer -- confirmed on device, GLib 2.88.3: "the unix transport
        # requires exactly one of the keys 'path' or 'abstract' to be set".
        # A client needs an actual GSocketConnection wrapping the fd, and
        # a bare `GObject.new(Gio.SocketConnection, socket=sock)` is not
        # enough either: that always builds the plain base class, which
        # cannot answer the SASL EXTERNAL auth's credentials-passing
        # (observed: "CLIENT: didn't send any credentials" over
        # G_DBUS_DEBUG=authentication, then "Exhausted all available
        # authentication mechanisms"). factory_lookup_type() is what
        # resolves AF_UNIX/SOCK_STREAM to GUnixConnection, which can.
        sock = Gio.Socket.new_from_fd(fd)
        conn_type = Gio.SocketConnection.factory_lookup_type(
            sock.get_family(), sock.get_socket_type(), sock.get_protocol())
        iostream = GObject.new(conn_type, socket=sock)
        # MESSAGE_BUS_CONNECTION, not just AUTHENTICATION_CLIENT: the far
        # end of this fd is xdg-dbus-proxy relaying to a real dbus-daemon,
        # and a real dbus-daemon refuses every message from a peer that has
        # not called org.freedesktop.DBus.Hello -- confirmed against a live
        # proxy+bus in this task, "Hello() was not yet called", before this
        # flag was added. Without MESSAGE_BUS_CONNECTION, GDBusConnection
        # never sends it.
        flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                 | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
        conn = Gio.DBusConnection.new_sync(iostream, None, flags, None, None)
        # The proxy's lifetime is tied to THIS connection's bus name, not to
        # the returned fd (nfc.c's on_peer_disconnect: "Removing the proxy
        # closes its sync fd, which makes xdg-dbus-proxy exit" -- Task 3).
        # `bus` and `portal` are otherwise local and go out of scope the
        # moment this function returns; measured on device, the underlying
        # session connection is then actually torn down (not just
        # unreffed-but-cached) within ~150-200ms, xdp sees the sender leave
        # the bus, and tears the proxy down under us -- `conn` starts
        # returning "The connection is closed" for every call, looking
        # exactly like a filter regression when it is a probe lifetime bug.
        # Confirmed by reproduction: an identical open_remote() that instead
        # holds bus/portal alive never sees the connection close. Keep them
        # alive for as long as `conn` might be used.
        conn._ph_nfc_probe_keepalive = (bus, portal)
    except Exception as e:  # noqa: BLE001 -- deliberate: see docstring above
        return None, str(e)
    return conn, None


def main(argv):
    if len(argv) > 1 and argv[1] == "--selftest-mock":
        return _run_mock_neard()  # internal: see selftest()
    if len(argv) > 1 and argv[1] == "--selftest":
        return selftest()

    conn, err = open_remote()
    if conn is None:
        print("no connection: %s" % err)
        return 2

    # Round 1 review, Critical 2: this was a bare call with no boundary. An
    # exception here (the proxy dying mid-run, a transport error) used to
    # propagate out of main() and exit 1 by Python's default -- reporting a
    # crash as "the filter is wrong" when nothing about the filter was ever
    # determined. The invariant this contract needs: 1 means the filter is
    # wrong, and NOTHING ELSE ever produces 1. An exception here is closer
    # to "could not get a connection" (2) than to a proven-wrong filter.
    try:
        verdict = check_filter(conn)
    except Exception as e:  # noqa: BLE001 -- deliberate: see comment above
        print("no connection: check_filter failed unexpectedly: %r" % e)
        return 2

    if verdict == "inconclusive":
        print("inconclusive: an allowed call failed for a reason that is "
              "not the filter (often: the radio is off -- OpenNFCRemote "
              "cannot turn it on itself, that is the design). This run "
              "cannot judge the filter one way or the other; fix the "
              "precondition (see the header's `needs:`) and re-run.")
        return 2

    if verdict == "ok":
        # Only chase the tag row once the filter demonstrably holds and the
        # allowed calls actually worked -- otherwise the poll loop this
        # would start never started either. And a failure here must never
        # downgrade an already-true verdict (round 1, Critical 2): it is
        # purely informational, so it gets its own try/except rather than
        # sharing the one above.
        try:
            tag_path = watch_for_tag(conn)
        except Exception as e:  # noqa: BLE001 -- deliberate, see above
            print("tag       watch failed after the verdict was already "
                  "determined (%r) -- not counted against the filter" % e)
            tag_path = None
        if tag_path:
            print("tag       InterfacesAdded %s -> ok, broadcast rule "
                  "verified" % tag_path)
        else:
            print("tag       NOT TESTED -- no InterfacesAdded within the "
                  "timeout (card placement, not a portal defect: move the "
                  "card under the phone, upper-middle of the back)")
        return 0

    assert verdict == "wrong", "unreachable verdict %r" % verdict
    return 1


# --------------------------------------------------------------- selftest --

# "A probe that asserts only the refusal is worthless. A proxy that permitted
# nothing at all would pass it." The reverse blind spot is just as real: does
# check_filter() actually fail when the filter is wide OPEN, or would a bug
# in the probe's own try/except plumbing report success no matter what came
# back? The device does not have a portal to answer that yet (Task 6), and
# even once it does, deliberately misconfiguring the *real* proxy to prove a
# negative would mean briefly handing a real app write access to the radio.
#
# So this runs the exact same check_filter() against three xdg-dbus-proxy
# processes this script spawns itself, on a private bus, against a mock
# 'org.neard' this script also owns -- never the real system bus, never the
# real radio:
#   - CLOSED:   the subset of the production rules check_filter() exercises
#     (Get, StartPollLoop, StopPollLoop allowed; Set named nowhere)
#   - OPEN:     --talk=org.neard with no --filter at all, i.e. no rules --
#     the shape an empty or reverted filter config would actually take
#   - NOTHING:  --see only, no --call rules at all -- the brief's own named
#     worry, that a proxy permitting nothing would pass a refusal-only test
# check_filter() must return 'ok' for CLOSED and something other than 'ok'
# for OPEN and NOTHING. If it ever returns 'ok' for either of the other two,
# this script would rubber-stamp a broken portal.
NEARD_XML = """
<node>
  <interface name='org.neard.Adapter'>
    <method name='StartPollLoop'><arg type='s' direction='in'/></method>
    <method name='StopPollLoop'/>
  </interface>
  <interface name='org.freedesktop.DBus.Properties'>
    <method name='Get'>
      <arg type='s' direction='in'/><arg type='s' direction='in'/>
      <arg type='v' direction='out'/>
    </method>
    <method name='Set'>
      <arg type='s' direction='in'/><arg type='s' direction='in'/>
      <arg type='v' direction='in'/>
    </method>
  </interface>
</node>
"""


def _run_mock_neard():
    """Own 'org.neard' on the process's own session bus and answer just
    enough of Adapter + Properties for check_filter() to have something real
    to call. Not a faithful neard -- it exists to be a *target*, not to be
    tested itself (that is what the real device below it is for).

    Runs as its OWN process (see selftest(), which spawns
    `--selftest-mock`), not inlined into the driver. Tried inlined first: a
    single process that both owns the name and then makes a blocking
    call_sync() through the proxy back to itself hung every time -- the name
    ownership handshake with the real bus daemon never got to run before the
    nested call_sync() loop started waiting on a reply that could only ever
    arrive after it. A second process with its own GLib.MainLoop sidesteps
    the ordering question entirely, and is exactly how a real client and a
    real neard are two different processes anyway.

    Prints exactly one line, 'READY', once the name is actually owned (via
    the name_acquired callback, not a sleep) -- that is the driver's cue that
    calls routed at 'org.neard' will actually reach this process.
    """
    props = {"Protocols": GLib.Variant("as", ["Initiator"]),
             "Powered": GLib.Variant("b", False)}

    def handle(connection, sender, path, iface, method, params, invocation):
        if method in ("StartPollLoop", "StopPollLoop"):
            invocation.return_value(None)
        elif method == "Get":
            name = params.get_child_value(1).get_string()
            invocation.return_value(GLib.Variant("(v)", (props[name],)))
        elif method == "Set":
            name = params.get_child_value(1).get_string()
            props[name] = params.get_child_value(2).get_variant()
            invocation.return_value(None)
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), 0, "no such method on the mock")

    def on_acquired(connection, name):
        print("READY", flush=True)

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    node = Gio.DBusNodeInfo.new_for_xml(NEARD_XML)
    for interface in node.interfaces:
        bus.register_object(ADAPTER_PATH, interface, handle, None, None)
    Gio.bus_own_name_on_connection(
        bus, NEARD, Gio.BusNameOwnerFlags.NONE, on_acquired, None)
    GLib.MainLoop().run()


def _terminate(proc):
    """SIGTERM, then SIGKILL if it does not take. Round 1 review, minor:
    `terminate()` + a bare `wait(timeout=5)` swallowed in
    `contextlib.suppress(Exception)` had no fallback if the process ignored
    SIGTERM -- a probe that claims to leave no residue should not be able to
    leak one this way."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@contextlib.contextmanager
def _proxy_connection(bus_address, xdp_extra_args):
    """Spawn one xdg-dbus-proxy against `bus_address` with the given policy
    args, wait for its socket to accept a connection, yield a connection made
    through it, then tear the process down. No --fd readiness channel (the
    production code's readiness contract is Task 3's to keep correct, and is
    exercised there): this is a one-shot self-check, so a short connect-retry
    loop is simpler and just as certain."""
    with tempfile.TemporaryDirectory(prefix="nfc-probe-selftest-") as d:
        os.chmod(d, 0o700)
        sock = os.path.join(d, "bus")
        proxy = subprocess.Popen(
            ["xdg-dbus-proxy", bus_address, sock] + xdp_extra_args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            conn = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if proxy.poll() is not None:
                    raise RuntimeError(
                        "xdg-dbus-proxy exited early (rc=%s)" % proxy.returncode)
                if os.path.exists(sock):
                    try:
                        flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                                 | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
                        conn = Gio.DBusConnection.new_for_address_sync(
                            "unix:path=%s" % sock, flags, None, None)
                        break
                    except GLib.Error:
                        pass
                time.sleep(0.05)
            if conn is None:
                raise RuntimeError("proxy socket never came up: %s" % sock)
            yield conn
        finally:
            _terminate(proxy)


def selftest():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if NEARD in Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None, "org.freedesktop.DBus",
            "/org/freedesktop/DBus", "org.freedesktop.DBus", None
    ).call_sync("ListNames", None, Gio.DBusCallFlags.NONE, 5000,
                None).unpack()[0]:
        print("selftest: 'org.neard' is already owned on this session bus "
              "-- refusing to collide with it. Run this under "
              "`dbus-run-session -- ...` for a private bus.")
        return 2

    mock = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--selftest-mock"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        ready = mock.stdout.readline()
        if ready.strip() != "READY":
            print("selftest: mock neard never became ready (%r)" % ready)
            return 2

        bus_address = Gio.dbus_address_get_for_bus_sync(
            Gio.BusType.SESSION, None)
        return _selftest_against(bus_address)
    finally:
        _terminate(mock)


def _selftest_against(bus_address):
    # Not the full 9-rule production set (docs/superpowers/specs/2026-09-10-
    # nfc-toggle-and-portal-design.md) -- just the subset that check_filter()
    # actually exercises (Get, StartPollLoop, StopPollLoop; no Set rule,
    # which is the load-bearing omission). GetManagedObjects and the
    # broadcast rules are irrelevant to what this meta-test is checking:
    # whether check_filter() itself can tell allowed from refused.
    closed_rules = [
        "--filter",
        "--see=%s" % NEARD,
        "--call=%s=org.neard.Adapter.StartPollLoop@/org/neard/*" % NEARD,
        "--call=%s=org.neard.Adapter.StopPollLoop@/org/neard/*" % NEARD,
        "--call=%s=org.freedesktop.DBus.Properties.Get@/org/neard/*" % NEARD,
    ]
    open_rules = ["--talk=%s" % NEARD]  # no --filter at all: everything allowed
    # Round 1 review, Important 2: the brief's own named worry -- "a proxy
    # that permitted nothing at all would pass a test that only checks the
    # refusal" -- had zero permanent coverage; it was checked once by hand
    # during development and that does not survive anyone else running this.
    nothing_rules = ["--filter", "--see=%s" % NEARD]  # no --call rules: permits nothing

    print("--- CLOSED (production-shaped) filter: expect the real check to hold ---")
    with _proxy_connection(bus_address, closed_rules) as conn:
        closed = check_filter(conn)

    print()
    print("--- OPEN filter (--talk, no rules): expect the check to CATCH it ---")
    with _proxy_connection(bus_address, open_rules) as conn:
        open_ = check_filter(conn)

    print()
    print("--- NOTHING permitted (--see only, no --call rules): expect the check to CATCH it too ---")
    with _proxy_connection(bus_address, nothing_rules) as conn:
        nothing = check_filter(conn)

    print()
    if closed == "ok" and open_ != "ok" and nothing != "ok":
        print("selftest: PASS -- check_filter() holds a closed filter to "
              "'ok' and catches both an open one and a too-closed one")
        return 0
    print("selftest: FAIL -- closed=%r open=%r nothing=%r (closed must be "
          "'ok', the other two must not, for the real probe's result to "
          "mean anything)" % (closed, open_, nothing))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
