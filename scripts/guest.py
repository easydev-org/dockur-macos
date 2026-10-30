#!/usr/bin/env python3
"""Everything that touches the guest, in one place: screendumps, clicks, keys, OCR.

WHY THIS EXISTS. Screen handling was spread across three scripts, each with its own copy of
the same three awkward details, and every copy had a different bug:

  qemu resolves screendump paths inside ITS container, so a host path has to be rewritten
  to /frames/... - drive-ocr knew this, drive-installer had it, probe-sidebar did not

  the monitor socket is monitor.sock as HMP and monitor.sock.qmp as QMP; connecting to the
  wrong one returns text, which a JSON parser reports as "Expecting value: line 1 column 1"
  and looks exactly like qemu being dead

  tesseract returns one WORD per row with its own box, so "Disk Utility" arrives as
  ('Disk', 837) and ('Utility', 866) - matching a phrase against a word never succeeds

None of these are decisions worth re-making per script, and all three cost runs when made
wrong. They live here once.

Coordinates from text() are CENTRES, because that is what a click needs and because adding
half a width at the call site is how six call sites ended up clicking half a word to the
right of their target.
"""

import json
import os
import socket
import time

import xsh


class QMP:
    """Minimal QMP client over a unix socket: screendump, pointer, keys."""

    ABS_MAX = 32767

    def __init__(self, path, screen_w=1920, screen_h=1080, timeout=30):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.buf = b""
        self.ident = json.loads(self._readline())
        self.execute("qmp_capabilities")

    def _readline(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("monitor closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode()

    def execute(self, name, **args):
        msg = {"execute": name}
        if args:
            msg["arguments"] = args
        self.sock.sendall((json.dumps(msg) + "\n").encode())
        while True:
            reply = json.loads(self._readline())
            if "event" in reply:
                continue
            return reply

    def _abs(self, pixel, extent):
        value = int(round(pixel * self.ABS_MAX / float(extent)))
        return max(0, min(self.ABS_MAX, value))

    def click(self, x, y):
        self.execute("input-send-event", events=[
            {"type": "abs", "data": {"axis": "x", "value": self._abs(x, self.screen_w)}},
            {"type": "abs", "data": {"axis": "y", "value": self._abs(y, self.screen_h)}},
        ])
        time.sleep(0.3)
        self.execute("input-send-event", events=[
            {"type": "btn", "data": {"down": True, "button": "left"}}])
        time.sleep(0.15)
        self.execute("input-send-event", events=[
            {"type": "btn", "data": {"down": False, "button": "left"}}])

    def key(self, qcode):
        self.execute("input-send-event", events=[
            {"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": qcode}}}])
        time.sleep(0.08)
        self.execute("input-send-event", events=[
            {"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": qcode}}}])

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Screen:
    """A guest's screen: capture, read text, and point at things.

    qemu runs in the container, so screendump writes inside it and the file is read back out
    with docker cp. Host and container paths are kept in step by mapping only the leading
    directory - the one thing every caller needs and none should re-derive.
    """

    def __init__(self, frames_dir, screen_w=1920, screen_h=1080):
        self.frames_dir = frames_dir.rstrip("/")
        self.w = screen_w
        self.h = screen_h
        self.q = None

    def connect(self, tries=30, delay=10):
        """Attach to QMP, retrying while qemu is still starting.

        The .qmp suffix is not optional: drive-in-container.sh creates monitor.sock (HMP,
        plain text) and monitor.sock.qmp (QMP, JSON). Connecting to the first and parsing
        its output as JSON produced thirty "Expecting value: line 1 column 1" errors over
        five minutes while qemu was perfectly healthy.
        """
        path = os.path.join(self.frames_dir, "monitor.sock")
        if not path.endswith(".qmp"):
            path += ".qmp"
        last = None
        for attempt in range(1, tries + 1):
            try:
                self.q = QMP(path, self.w, self.h)
                return attempt
            except Exception as exc:
                last = exc
                time.sleep(delay)
        raise xsh.Fail(xsh.Result(["connect", path], 1, "", str(last)))

    def grab(self, stem):
        """Capture the screen; returns the host path of the PPM.

        tesseract reads P6 PPM directly, so there is no conversion step that can fail.
        """
        host = stem + ".ppm"
        name = os.path.basename(host)
        reply = self.q.execute("screendump", filename="/frames/" + name)
        if "error" in reply:
            raise xsh.Fail(xsh.Result(["screendump", name], 1, "", str(reply["error"])))
        for _ in range(30):
            if os.path.exists(host):
                return host
            time.sleep(0.2)
        raise xsh.Fail(xsh.Result(["screendump", name], 1, "", "file never appeared"))

    def text(self, ppm, min_conf=40):
        """OCR one frame into [(word, x_centre, y_centre, w, h)].

        Centres, because that is what click() takes. tesseract's tsv gives a box per WORD,
        so a two-word label arrives as two entries - match single words and pin the row by
        y, never a phrase.
        """
        if not ppm:
            return []
        r = xsh.run(["tesseract", ppm, "stdout", "--psm", "3", "tsv"], echo=False)
        words = []
        for line in (r.out or "").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 12:
                continue
            try:
                conf = float(parts[10])
                x, y, w, h = (int(parts[6]), int(parts[7]),
                              int(parts[8]), int(parts[9]))
            except ValueError:
                continue
            word = parts[11].strip()
            if not word or conf < min_conf:
                continue
            words.append((word, x + w // 2, y + h // 2, w, h))
        return words

    def read(self, stem):
        """Capture and OCR in one step."""
        return self.text(self.grab(stem))

    def click(self, x, y):
        self.q.click(x, y)

    def key(self, qcode):
        self.q.key(qcode)

    def close(self):
        if self.q:
            self.q.close()


def find(words, *needles, y_range=None, x_range=None):
    """First word matching any needle, optionally constrained to a region.

    Region limits are how a label is told apart from the same word in prose - the recovery
    menu says "Repair or erase a disk using Disk Utility", so an unanchored search for
    "disk" matches the description as readily as the entry.
    """
    for word, x, y, _w, _h in words:
        low = word.strip().lower()
        if not any(low == n or low.startswith(n) for n in needles):
            continue
        if y_range and not (y_range[0] <= y <= y_range[1]):
            continue
        if x_range and not (x_range[0] <= x <= x_range[1]):
            continue
        return (x, y)
    return None


def joined(words):
    """All recognised text on one lower-cased line, for phrase tests."""
    return " ".join(w[0] for w in words).lower()
