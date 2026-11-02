#!/usr/bin/env python3
"""Minimal QMP client for driving a QEMU guest with absolute pointer events.

    qmp.py <qmp.sock> <command> [args...]

Commands:
    status                     print the guest's run state
    move <x> <y>               move the pointer to absolute display coordinates
    click <x> <y>              move and left-click
    key <qcode>                press and release one key
    shot <path.ppm>            framebuffer dump (same file format as HMP)
    size                       print the display geometry QEMU reports

WHY QMP AND NOT HMP. The HMP driver in this repo failed completely: 64 clicks
left the pointer in the top-left corner and all 64 frames were byte-identical.
HMP's `mouse_move` carries relative deltas, while the guest has a usb-tablet,
which is an absolute device - so the deltas went nowhere useful. QMP's
input-send-event takes `abs` axis events plus explicit button state, which is
what an absolute pointer actually needs:

    {"execute":"input-send-event","arguments":{"events":[
        {"type":"abs","data":{"axis":"x","value":X}},
        {"type":"abs","data":{"axis":"y","value":Y}},
        {"type":"btn","data":{"down":true,"button":"left"}},
        {"type":"btn","data":{"down":false,"button":"left"}}]}}

The axis value is in the range QEMU reports for the console; query it with
query-input? No - it is reported per-event-range by `query-display-options` only
for some backends, so this uses the documented 0..32767 range that QEMU accepts
for absolute events and scales the requested pixel coordinates into it.
"""
import json
import threading
import os
import socket
import sys
import time

# QEMU accepts absolute pointer events on a 0..32767 scale regardless of the
# framebuffer size, so pixel coordinates are converted here.
ABS_MAX = 32767

SCREEN_W = int(os.environ.get("SCREEN_W", "1920"))
SCREEN_H = int(os.environ.get("SCREEN_H", "1080"))


class QMP:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(30)
        self.sock.connect(path)
        self.buf = b""
        # ONE LOCK PER CONNECTION.
        #
        # The driver's capture thread and its action thread share this socket - the recorder
        # has to, because qemu greets only the first connection and never a second. Matching
        # replies by id is not enough on its own: _read_message() consumes from a shared
        # self.buf, so two threads reading at once interleave and each can end up with half a
        # line. That produced:
        #
        #     qmp.py line 85, in execute
        #     TimeoutError: timed out
        #
        # while the capture thread was mid-screendump. The lock makes a whole request-reply
        # pair atomic, so the two threads take turns instead of splitting each other's bytes.
        self._lock = threading.RLock()
        self.greeting = self._read_message()
        self.execute("qmp_capabilities")

    def _read_message(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("QMP closed")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def execute(self, command, **arguments):
        """Send one command and return its reply, matched by request id.

        THE WHOLE PAIR IS ATOMIC, AND THAT IS THE POINT.

        The driver records through this connection - it has to, because qemu greets only the
        first client and never a second, so a separate recorder process starved the driver at
        its own connect(). That puts two threads on one socket: the capture thread
        screendumping five times a second, the action thread clicking and pressing keys.

        Matching replies by id is not enough by itself. _read_message() consumes from a shared
        self.buf, so two threads reading at once interleave and each can end up holding half a
        line - which surfaced as:

            qmp.py line 85, in execute
            TimeoutError: timed out

        While the capture thread was mid-screendump. The lock makes one request-reply pair
        atomic, so the threads take turns rather than splitting each other's bytes.
        """
        with self._lock:
            self._seq = getattr(self, "_seq", 0) + 1
            request_id = self._seq
            payload = {"execute": command, "id": request_id}
            if arguments:
                payload["arguments"] = arguments
            self.sock.sendall((json.dumps(payload) + "\n").encode())
            while True:
                msg = self._read_message()
                if msg.get("id") == request_id and ("return" in msg or "error" in msg):
                    return msg
                # An event, or a reply meant for another request: keep reading.

    def _send(self, events):
        """Send one input-send-event WITHOUT taking the lock.

        Only for callers that already hold it and need several sends to be inseparable -
        key() and key_combo() use this so a real gap can sit between press and release while
        the capture thread still cannot slip in between.
        """
        payload = {"execute": "input-send-event", "arguments": {"events": events},
                   "id": -1}
        self.sock.sendall((json.dumps(payload) + "\n").encode())
        while True:
            msg = self._read_message()
            if msg.get("id") == -1:
                return msg

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def to_abs(pixel, extent):
    value = int(round(pixel * ABS_MAX / float(extent)))
    return max(0, min(ABS_MAX, value))


def move(q, x, y):
    q.execute("input-send-event", events=[
        {"type": "abs", "data": {"axis": "x", "value": to_abs(x, SCREEN_W)}},
        {"type": "abs", "data": {"axis": "y", "value": to_abs(y, SCREEN_H)}},
    ])


def click(q, x, y, hold=0.09):
    """Move, press, release - with a real gap, and the pair insulated from other threads.

    Same reasoning as key(). Sending down and up in one batch removed the gap and produced
    clicks the guest did not register; sending them as two unlocked calls let the capture
    thread sit between them and produce a drag or nothing.
    """
    move(q, x, y)
    time.sleep(0.25)
    with q._lock:
        q._send([{"type": "btn", "data": {"down": True, "button": "left"}}])
        time.sleep(hold)
        q._send([{"type": "btn", "data": {"down": False, "button": "left"}}])


def key(q, qcode, hold=0.06):
    """Press and release one key, with a real gap between them that no other thread can fill.

    TWO FAILURES, TWO OPPOSITE MISTAKES.

    First this was key-down, sleep, key-up as separate execute() calls - so the capture thread
    could take the lock between them, and the guest saw a key held for an arbitrary time. A
    held key with a late release does not type a character, it modifies the next one; that is
    how the account form received "hi" where "docker" was typed.

    Then I sent both events in ONE call, which removes the gap entirely - and macOS ignores a
    key whose release arrives in the same batch as its press.

    So: hold the lock, send the press, sleep, send the release. The pair is atomic with
    respect to other threads, and the gap is real.
    """
    with q._lock:
        q._send([{"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": qcode}}}])
        time.sleep(hold)
        q._send([{"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": qcode}}}])


def key_combo(q, *qcodes, hold=0.1):
    """Press several keys together, e.g. key_combo(q, "meta_l", "spc") for Cmd-Space.

    Same shape as key(): one lock, real gaps inside it. Modifiers go down, the chord is held,
    then everything comes up in reverse - and no other thread can interleave with any of it.
    """
    with q._lock:
        for qcode in qcodes:
            q._send([{"type": "key",
                      "data": {"down": True, "key": {"type": "qcode", "data": qcode}}}])
            time.sleep(0.05)
        time.sleep(hold)
        for qcode in reversed(qcodes):
            q._send([{"type": "key",
                      "data": {"down": False, "key": {"type": "qcode", "data": qcode}}}])
            time.sleep(0.05)


def scroll(q, x, y, notches, dx_notches=0):
    """Turn the wheel at (x,y). Positive notches scroll down.

    UNITS MATTER AND ARE NOT PIXELS. With axis-size "int", QEMU reads units as
    thousandths of an inch - so the 120 that a real mouse reports per notch is 0.12
    inch here, far below the threshold at which macOS scrolls at all. Which is exactly
    what happened: six "scrolls" left the Sharing pane pixel-identical, scan-top and
    scan-6 matching byte for byte.

    One notch is therefore 12000 units here (12 inches), and one call = one notch.
    """
    move(q, x, y)
    time.sleep(0.3)

    def turn(axis, count):
        sign = 1 if count > 0 else -1
        for _ in range(abs(int(count))):
            q.execute("input-send-event", events=[
                {"type": "wheel",
                 "data": {"axis": axis,
                          "axis-size": "int",
                          "units": sign * 12000}}])
            time.sleep(0.15)

    turn("vertical", notches)
    if dx_notches:
        turn("horizontal", dx_notches)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    sock_path, cmd = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]

    q = QMP(sock_path)
    try:
        if cmd == "status":
            print(json.dumps(q.execute("query-status")))
        elif cmd == "size":
            print(json.dumps(q.execute("query-display-options")))
        elif cmd == "move":
            move(q, int(args[0]), int(args[1]))
            print("moved to %s,%s" % (args[0], args[1]))
        elif cmd == "click":
            click(q, int(args[0]), int(args[1]))
            print("clicked %s,%s" % (args[0], args[1]))
        elif cmd == "key":
            key(q, args[0])
            print("key " + args[0])
        elif cmd == "shot":
            print(json.dumps(q.execute("screendump", filename=args[0])))
        else:
            sys.exit("unknown command: " + cmd)
    finally:
        q.close()


if __name__ == "__main__":
    # Guarded so this file can also be imported as a module by
    # setup-assistant-drive.py. Without the guard, importing it ran main(),
    # which tried to open a socket from argv that the importer did not supply -
    # the driver died at the import line and the run produced no frames at all.
    main()
