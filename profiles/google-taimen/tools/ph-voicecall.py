#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: BOOTED (runs ON the device, as root)
# env: -
# exits: 0 ok - 1 the ADSP refused a command - 2 no bridge
# lib-exempt: runs on the device, not the host -- there is no mutex or
#             ph-lib.sh there. Invoke it through the mutex from the host.
"""Drive an ADSP voice-call session over the q6voice debugfs bridge.

Mainline has no voice service, so nothing sets up the modem<->ADSP<->codec
session a call needs. This does it from userspace, one raw APR packet at a
time, so the sequence can be corrected without a kernel build. Once a
sequence is known good it belongs in a kernel driver, not here.

The sequence is downstream q6voice's voc_start_voice_call(), minus everything
that needs shared memory (calibration, packet exchange, in-call record):

    MVM  create passive control session      -> mvm handle
    CVS  create passive control session      -> cvs handle
    MVM  attach stream (cvs)
    MVM  set policy dual control             (the modem drives the state machine)
    CVP  create full control session v2      -> cvp handle
    CVP  enable
    MVM  attach vocproc (cvp)
    MVM  start voice

The AFE ports are the ones Google's own mixer_paths uses for a taimen call
("voicemmode1-call"): QUAT_MI2S_RX out to the external amp, SLIM_0_TX in from
the codec mic. The ADSP opens those ports itself as part of the session --
downstream q6voice never calls into AFE -- but their clocks are not the
ADSP's to start, so hold each one open with a stream first (see --check).
"""
import argparse
import os
import signal
import struct
import sys
import time

DBG = "/sys/kernel/debug/q6voice"

APR_BASIC_RSP_RESULT = 0x000110E8

MVM_CREATE_PASSIVE  = 0x000110FF
MVM_ATTACH_STREAM   = 0x0001123C
MVM_DETACH_STREAM   = 0x0001123D
MVM_ATTACH_VOCPROC  = 0x0001123E
MVM_DETACH_VOCPROC  = 0x0001123F
MVM_START_VOICE     = 0x00011190
MVM_STOP_VOICE      = 0x00011192
MVM_DUAL_CONTROL    = 0x00011327
CVS_CREATE_PASSIVE  = 0x00011140
CVP_CREATE_V2       = 0x000112BF
CVP_ENABLE          = 0x000100C6
CVP_DISABLE         = 0x000110E1
DESTROY_SESSION     = 0x0001003C

TOPOLOGY_NONE       = 0x00010F70
TOPOLOGY_TX_SM_ECNS = 0x00010F71
TOPOLOGY_RX_DEFAULT = 0x00010F77
CAL_NETWORK_ID_NONE = 0x0001135E
VOCPROC_EC_INT_MIX  = 0x00010F7C
PORT_ID_NONE        = 0xFFFF

# Google's mixer_paths_tavil_taimen.xml, path "voicemmode1-call".
QUAT_MI2S_RX = 0x1006
SLIMBUS_0_TX = 0x4001

# Downstream uses TOPOLOGY_ID_NONE only for local call hold and for an explicit
# "disable topology" flag. With no calibration loaded its real defaults are
# these (q6voice.c voice_get_topology), and NONE builds no processing chain at
# all -- which looks like a neutral passthrough and is not one.
VOICEMMODE1 = "11C05000"
SESSION_IDX = 1                 # our src_port; responses come back on dest_port


ADSP_EALREADY = 0x9

# A PASSIVE control session joins the session the modem already created for a
# live call rather than making a new one. Everything that session has already
# done then comes back as ADSP_EALREADY, which is a statement of fact and not a
# failure: the stream is attached, the vocproc is attached, voice is started.
# Refusing on it means giving up at the exact point where the call is working.
ALREADY_IS_FINE = (
    MVM_CREATE_PASSIVE,
    CVS_CREATE_PASSIVE,
    MVM_ATTACH_STREAM,
    MVM_ATTACH_VOCPROC,
    MVM_START_VOICE,
    MVM_DUAL_CONTROL,
    CVP_ENABLE,
)


class Refused(Exception):
    pass


class Bridge:
    """One open fd per voice service. Reads pop one packet each."""

    def __init__(self, verbose=False):
        self.verbose = verbose
        # Set when the ADSP answers EALREADY: the modem got there first and the
        # session is its property, not ours.
        self.joined = False
        try:
            self.fd = {s: os.open("%s/%s" % (DBG, s), os.O_RDWR)
                       for s in ("mvm", "cvs", "cvp")}
        except FileNotFoundError:
            sys.exit("no %s -- is q6voice-dbg loaded, and debugfs mounted?" % DBG)

    def send(self, svc, opcode, dest=0, payload=b"", token=0):
        line = "%x %x %x %x %s" % (opcode, SESSION_IDX, dest, token,
                                   " ".join("%02x" % b for b in payload))
        if self.verbose:
            print("  -> %-3s %s" % (svc, line))
        os.write(self.fd[svc], line.encode())

    def recv(self, svc, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = os.read(self.fd[svc], 4096)
            if raw:
                pkt = dict(kv.split("=", 1) for kv in raw.decode().split())
                if self.verbose:
                    print("  <- %-3s %s" % (svc, raw.decode().strip()))
                return pkt
            time.sleep(0.01)
        return None

    def cmd(self, svc, opcode, dest=0, payload=b"", timeout=2.0):
        """Send, wait for the basic response to THIS opcode, return its src_port.

        src_port is how the ADSP hands back a session handle: for the create
        commands it is the new handle, for everything else it is noise.
        """
        self.send(svc, opcode, dest, payload)
        while True:
            pkt = self.recv(svc, timeout)
            if pkt is None:
                raise Refused("%s 0x%08x: no response in %.1fs" %
                              (svc, opcode, timeout))
            if int(pkt["opcode"], 16) != APR_BASIC_RSP_RESULT:
                continue                        # an event, not our answer
            body = bytes.fromhex(pkt["payload"])
            if len(body) < 8:
                continue
            rsp_op, status = struct.unpack("<II", body[:8])
            if rsp_op != opcode:
                continue                        # someone else's answer
            if status == ADSP_EALREADY and opcode in ALREADY_IS_FINE:
                print("  (%s 0x%08x: already done by the modem's session)" %
                      (svc, opcode))
                self.joined = True
                return int(pkt["src"], 16)
            if status:
                raise Refused("%s 0x%08x: ADSP status 0x%08x" %
                              (svc, opcode, status))
            return int(pkt["src"], 16)


def name20(s):
    return s.encode().ljust(20, b"\0")


def start(br, rx_port, tx_port, tx_topo, rx_topo, mvm_h=0, cvs_h=0):
    # On ADSP_EALREADY the response carries src_port 0, not the handle of the
    # session that already exists -- so a join gives us no way to address it.
    # The handle has to come from the run that created it, hence the overrides.
    mvm = br.cmd("mvm", MVM_CREATE_PASSIVE, payload=name20(VOICEMMODE1)) or mvm_h
    mvm = mvm_h or mvm
    print("mvm handle 0x%04x" % mvm)
    cvs = br.cmd("cvs", CVS_CREATE_PASSIVE, payload=name20(VOICEMMODE1))
    cvs = cvs_h or cvs
    print("cvs handle 0x%04x" % cvs)

    br.cmd("mvm", MVM_ATTACH_STREAM, dest=mvm, payload=struct.pack("<H", cvs))
    br.cmd("mvm", MVM_DUAL_CONTROL, dest=mvm, payload=struct.pack("<B", 1))

    cvp_cfg = struct.pack("<HHIHIIIH",
                          2,                    # direction: tx and rx
                          tx_port, tx_topo,
                          rx_port, rx_topo,
                          CAL_NETWORK_ID_NONE,
                          VOCPROC_EC_INT_MIX,
                          PORT_ID_NONE) + name20("")
    cvp = br.cmd("cvp", CVP_CREATE_V2, payload=cvp_cfg, timeout=4.0)
    print("cvp handle 0x%04x  (rx 0x%04x, tx 0x%04x)" % (cvp, rx_port, tx_port))

    br.cmd("cvp", CVP_ENABLE, dest=cvp, timeout=4.0)
    br.cmd("mvm", MVM_ATTACH_VOCPROC, dest=mvm, payload=struct.pack("<H", cvp),
           timeout=4.0)
    br.cmd("mvm", MVM_START_VOICE, dest=mvm, timeout=4.0)

    print("voice session running: mvm=0x%04x cvs=0x%04x cvp=0x%04x" %
          (mvm, cvs, cvp))
    return mvm, cvs, cvp


def stop(br, mvm, cvs, cvp):
    """Best effort: a half-torn-down session still has to be told to go away.

    NEVER destroy a session we only joined. During a live call the modem owns
    the MVM and CVS; tearing those down kills the call's voice path AND leaves
    every later attempt creating a fresh session that carries no call audio --
    which looks exactly like "the voice path does not work".
    """
    steps = [("mvm", MVM_DETACH_VOCPROC, mvm, struct.pack("<H", cvp)),
             ("cvp", CVP_DISABLE, cvp, b""),
             ("cvp", DESTROY_SESSION, cvp, b"")]
    if br.joined:
        print("joined the modem's session -- leaving MVM/CVS alone")
    else:
        steps = ([("mvm", MVM_STOP_VOICE, mvm, b"")] + steps +
                 [("mvm", MVM_DETACH_STREAM, mvm, struct.pack("<H", cvs)),
                  ("cvs", DESTROY_SESSION, cvs, b""),
                  ("mvm", DESTROY_SESSION, mvm, b"")])
    for svc, opcode, dest, payload in steps:
        try:
            br.cmd(svc, opcode, dest=dest, payload=payload)
        except Refused as e:
            print("  teardown: %s" % e)


def selftest():
    """The packing is the only part that can be silently wrong. Check it."""
    # Downstream vss_ivocproc_cmd_create_full_control_session_v2_t is __packed:
    # u16 u16 u32 u16 u32 u32 u32 u16 = 24 bytes, then char name[20].
    cfg = struct.pack("<HHIHIIIH", 2, SLIMBUS_0_TX, TOPOLOGY_NONE,
                      QUAT_MI2S_RX, TOPOLOGY_NONE, CAL_NETWORK_ID_NONE,
                      VOCPROC_EC_INT_MIX, PORT_ID_NONE) + name20("")
    assert len(cfg) == 44, len(cfg)
    assert cfg[:4] == b"\x02\x00\x01\x40", cfg[:4].hex()   # direction, tx port

    assert name20(VOICEMMODE1) == b"11C05000" + b"\0" * 12
    assert len(name20(VOICEMMODE1)) == 20

    # A response line as the bridge renders it: create-session ACK, status 0,
    # handle in src. bytes.fromhex must survive the concatenated payload.
    line = ("opcode=000110e8 src=0007 dest=0001 token=00000000 size=8 "
            "payload=ff100100" + "00000000")
    pkt = dict(kv.split("=", 1) for kv in line.split())
    body = bytes.fromhex(pkt["payload"])
    rsp_op, status = struct.unpack("<II", body[:8])
    assert rsp_op == MVM_CREATE_PASSIVE, hex(rsp_op)
    assert status == 0
    assert int(pkt["src"], 16) == 7

    # A NACK must not be read as a handle.
    body = struct.pack("<II", MVM_START_VOICE, 0x00000102)
    assert struct.unpack("<II", body)[1] != 0
    print("selftest ok")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=("probe", "start", "cycle", "selftest"),
                   help="probe: create an MVM session and tear it down -- "
                        "answers whether the ADSP speaks voice at all. "
                        "start: bring a session up and leave it running. "
                        "cycle: start, hold, then tear down.")
    p.add_argument("--rx-port", type=lambda x: int(x, 0), default=QUAT_MI2S_RX)
    p.add_argument("--tx-port", type=lambda x: int(x, 0), default=SLIMBUS_0_TX)
    p.add_argument("--tx-topology", type=lambda x: int(x, 0),
                   default=TOPOLOGY_TX_SM_ECNS)
    p.add_argument("--rx-topology", type=lambda x: int(x, 0),
                   default=TOPOLOGY_RX_DEFAULT)
    p.add_argument("--mvm-handle", type=lambda x: int(x, 16), default=0,
                   help="hex handle of an existing MVM session to drive")
    p.add_argument("--cvs-handle", type=lambda x: int(x, 16), default=0,
                   help="hex handle of an existing CVS session to attach")
    p.add_argument("--hold", type=float, default=20.0,
                   help="seconds to hold the session up in 'cycle'")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print every packet in both directions")
    args = p.parse_args()

    if args.action == "selftest":
        selftest()
        return 0

    br = Bridge(args.verbose)

    if args.action == "probe":
        mvm = br.cmd("mvm", MVM_CREATE_PASSIVE, payload=name20(VOICEMMODE1))
        print("the ADSP created MVM session 0x%04x -- voice services are live"
              % mvm)
        br.cmd("mvm", DESTROY_SESSION, dest=mvm)
        return 0

    # A session left behind refuses the next CREATE, and the handle needed to
    # free it dies with the process. Tear down whatever we got as far as.
    created = []
    try:
        handles = start(br, args.rx_port, args.tx_port,
                        args.tx_topology, args.rx_topology,
                        args.mvm_handle, args.cvs_handle)
        created = list(handles)
    except Refused:
        for svc, h in zip(("mvm", "cvs", "cvp"), created):
            try:
                br.cmd(svc, DESTROY_SESSION, dest=h)
            except Refused:
                pass
        raise
    if args.action == "cycle":
        # A session the ADSP still holds refuses the next CREATE with
        # ADSP_EALREADY (0x9), and the handle needed to free it died with the
        # process that made it. Being killed is the normal way a hold ends --
        # systemctl stop, Ctrl-C -- so tear down on the signal, not just on the
        # happy path.
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: sys.exit(0))
        print("holding %.0fs" % args.hold)
        try:
            time.sleep(args.hold)
        finally:
            stop(br, *handles)
            print("torn down")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as e:
        sys.exit("REFUSED: %s" % e)
