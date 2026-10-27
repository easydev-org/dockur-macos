#!/usr/bin/env python3
"""Drive Setup Assistant by reading the screen with OCR.

    drive-ocr.py <qmp-socket> <frame-prefix>

WHY OCR, AFTER EVERY COORDINATE GUESS FAILED. Each attempt so far picked a target
from a heuristic and each one was wrong in a different way: a guessed (760,430) that
was not on the list, an accent band that was Continue's focus ring (14% coverage of
its own bounding box), another that was a 72x76 icon. Reading what is written on
the screen is the step that was missing - OSX-KVM's own requirements list
tesseract-ocr for exactly this reason, and its README calls automating the installer
a "not so crazy idea", which is about as close to prior art as this gets.

So this finds text and clicks the text. "Continue", "Agree", "Not Now", "Continue"
are all words on buttons, and OCR gives their bounding boxes directly. When the pane
is the country list, the target is the first country name; when it is the account
form, the target is the field labels.

THE SPLIT WITH THE CONTAINER: qemu runs in the launcher image, because that image is
a complete runtime for it and the host is not (libcapstone.so.5, then
libjpeg.so.62, before this was understood). The driver runs on the host, because the
host has apt and OCR tooling and the image is Alpine. They meet at the QMP socket in
the bind-mounted frames directory, which is why the socket is created there.
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qmp as qmpmod

W, H = 1920, 1080


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                          text=True, **kw)


class Screen:
    def __init__(self, qmp_socket):
        self.q = qmpmod.QMP(qmp_socket)

    def grab(self, stem):
        """Screen dump + PNG, returning the PNG path."""
        ppm = stem + ".ppm"
        reply = self.q.execute("screendump", filename=ppm)
        if "error" in reply:
            return None
        time.sleep(1.2)
        if not os.path.exists(ppm):
            return None
        # Convert without ImageMagick: ppm2png.py is in this directory and the
        # container writes plain P6.
        png = stem + ".png"
        sh([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "ppm2png.py"), ppm, png])
        return png if os.path.exists(png) else None

    def text(self, png):
        """OCR the frame into [(text, x_centre, y_centre, width, height)].

        tesseract's tsv output gives one line per word with its bounding box, which
        is what turns "the word Continue" into "click here".
        """
        if not png:
            return []
        out = sh(["tesseract", png, "stdout", "--psm", "11", "tsv"]).stdout
        words = []
        for line in out.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 12:
                continue
            try:
                conf = float(parts[10])
                x, y, w, h = int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9])
            except ValueError:
                continue
            text = parts[11].strip()
            if not text or conf < 40:
                continue
            words.append((text, x + w // 2, y + h // 2, w, h))
        return words

    def click(self, x, y):
        qmpmod.click(self.q, x, y)

    def key(self, name):
        qmpmod.key(self.q, name)

    def type(self, text):
        for ch in text:
            qmpmod.key(self.q, ch)
            time.sleep(0.1)

    def close(self):
        self.q.close()


# Words that mean "advance". Matched case-insensitively against OCR output, since
# OCR is not reliable about case.
ADVANCE = ("continue", "agree", "accept", "next", "ok", "allow", "enable",
           "not now", "later", "skip", "done", "start using", "get started",
           "set up later", "don't sign in", "do not sign in")


def find_advance(words):
    """The button to press, preferring lower-right (that is where Continue lives)."""
    best = None
    for text, x, y, w, h in words:
        low = text.lower().strip(" .,:;")
        if any(low == a or low.startswith(a) for a in ADVANCE):
            # Prefer right-most, then lowest - Continue is bottom-right.
            score = (x > W * 0.5, y)
            if best is None or score > best[0]:
                best = (score, (x, y), text)
    return (best[1], best[2]) if best else (None, None)


def find_country(words):
    """First country-looking word in the list, for the opening pane."""
    for text, x, y, w, h in words:
        t = text.strip()
        if 3 <= len(t) <= 24 and t[:1].isupper() and t.isascii() and t.isalpha():
            # In the list area of the window, below the heading.
            if 300 < y < 800:
                return (x, y), t
    return None, None


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    qmp_socket, prefix = sys.argv[1], sys.argv[2]
    log_path = prefix + "-drive.log"

    def log(msg):
        line = "[ocr %s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    screen = Screen(qmp_socket + ".qmp")
    log("qmp connected")

    # Let the guest finish drawing before the first read; OCR on a half-drawn pane
    # returns half the words.
    time.sleep(35)

    for step in range(1, 25):
        png = screen.grab("%s-s%02d" % (prefix, step))
        words = screen.text(png)
        log("step %d: %d words: %s" % (step, len(words),
                                      " | ".join(w[0] for w in words[:12])))

        if not words:
            log("step %d: nothing readable; pressing Return" % step)
            screen.key("ret")
            time.sleep(8)
            continue

        # A desktop has a menu bar and a Dock; the wizard always has a button.
        joined = " ".join(w[0].lower() for w in words)
        if "finder" in joined and "continue" not in joined:
            log("DESKTOP REACHED at step %d" % step)
            break

        target, label = find_advance(words)
        if target is None:
            target, label = find_country(words)
        if target is None:
            log("step %d: no target word; pressing Return" % step)
            screen.key("ret")
            time.sleep(8)
            continue

        log("step %d: clicking %r at %s" % (step, label, target))
        screen.click(target[0], target[1])
        time.sleep(6)

    log("done")
    screen.close()


if __name__ == "__main__":
    main()
