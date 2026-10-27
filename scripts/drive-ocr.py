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
        """Screen dump, returning the PPM path.

        tesseract reads P6 PPM directly, so there is no conversion step to fail. An
        earlier version converted to PNG with ppm2png.py via sys.executable, which
        under sudo is a different interpreter than the one the script ran under; the
        conversion silently produced nothing and every OCR call then read an empty
        path and reported zero words.
        """
        ppm = stem + ".ppm"
        # qemu resolves this path inside its own container, where the frames
        # directory is mounted at /frames. The caller works in host paths, so map
        # only the leading directory and let both sides agree on where the file is:
        # qemu writes /frames/x.ppm inside the container, which is the same inode as
        # <frames>/x.ppm on the host.
        name = os.path.basename(ppm)
        in_container = "/frames/" + name
        reply = self.q.execute("screendump", filename=in_container)
        if "error" in reply:
            print("[ocr] screendump error: %s" % reply["error"], flush=True)
            return None
        time.sleep(1.5)
        if not os.path.exists(ppm):
            print("[ocr] no file at %s" % ppm, flush=True)
            return None
        return ppm

    def text(self, png):
        """OCR the frame into [(text, x_centre, y_centre, width, height)].

        tesseract's tsv output gives one line per word with its bounding box, which
        is what turns "the word Continue" into "click here".
        """
        if not png:
            return []
        # --psm 3 is the default automatic page segmentation. --psm 11 (sparse
        # text) was tried first and returned nothing at all on a frame that
        # demonstrably contains a list of country names - it treats isolated words
        # as noise when there is no obvious paragraph. If OCR still returns nothing,
        # say so with the raw stderr, because 'no words' has more than one cause.
        proc = sh(["tesseract", png, "stdout", "--psm", "3", "tsv"])
        out = proc.stdout
        if not out.strip():
            print("[ocr] tesseract produced no output; stderr: %s"
                  % (proc.stderr.strip()[:300] or "(empty)"), flush=True)
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
# Button words. Deliberately short: every entry has to be something that only
# appears on a control, never in body copy, or OCR will match the prose and the
# click lands on nothing.
#
# "later" was in this list and did exactly that. The accessibility pane reads
# "...you can set this up later", OCR returned the word "later", the driver clicked
# the sentence, and it looped on that pane for nineteen steps. The control there is
# "Not Now" - a phrase, not a word that appears in prose.
BUTTONS = ("continue", "agree", "accept", "not now", "don't sign in",
           "do not sign in", "set up later", "start using", "get started",
           "allow", "enable", "next", "done", "ok")


def find_button(words):
    """Pick a control, preferring the lower-right corner where Continue lives.

    y is required to be in the bottom fifth of the screen. On every pane of this
    wizard the buttons are there, and insisting on it means body text can never be
    mistaken for a control even when a phrase matches.
    """
    best = None
    for text, x, y, w, h in words:
        low = text.lower().strip(" .,:;")
        if not any(low == b or low.startswith(b) for b in BUTTONS):
            continue
        if y < H * 0.80:
            continue
        score = (x, y)
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

    # Track the pane, so being stuck is detected rather than repeated.
    #
    # The previous run clicked the word "later" in a sentence on the accessibility
    # pane for nineteen consecutive steps, and nothing in the log said so - each
    # line looked like progress. The fix is to remember what the pane said and act
    # differently when it says the same thing twice.
    previous = None
    stuck = 0

    for step in range(1, 30):
        png = screen.grab("%s-s%02d" % (prefix, step))
        words = screen.text(png)
        joined = " ".join(w[0] for w in words)
        shortened = joined[:70]
        log("step %d: %s" % (step, shortened or "(nothing readable)"))

        if shortened and shortened == previous:
            stuck += 1
            log("step %d: same pane as last step (%d in a row)" % (step, stuck))
        else:
            stuck = 0
        previous = shortened

        # A desktop has a menu bar; the wizard always shows a button near the bottom.
        low = joined.lower()
        if "finder" in low and "continue" not in low:
            log("DESKTOP REACHED at step %d" % step)
            break

        if not words:
            log("step %d: nothing readable; pressing Return" % step)
            screen.key("ret")
            time.sleep(8)
            continue

        target, label = find_button(words)

        # Stuck on a pane: the button text was probably misread, so press Return -
        # the default button - rather than clicking the same wrong spot again.
        if target is None or stuck >= 2:
            log("step %d: no usable button (stuck=%d); pressing Return" % (step, stuck))
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
