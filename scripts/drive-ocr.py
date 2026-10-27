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

    Two constraints, both learned from frames rather than chosen:

    y must be below 60% of the screen. Continue on the country pane sits at y=810
    of 1080 (75%); the Accessibility pane puts "Not Now" higher, near y=700
    (65%). Anything above that is the body copy region - the same region where
    "System" and "See" were being clicked as if they were list rows.

    And the word has to be one of BUTTONS, which contains no word that appears in
    prose. Together those two rules cover the failure that cost the most time here:
    clicking text that merely looked clickable.
    """
    # Multi-word labels arrive as separate words from OCR, so the bottom area is
    # also joined back into phrases and searched for whole labels. "Not Now" is the
    # clearest case: without this it comes back as "Not" and "Now" and never matches
    # a label, which is why the accessibility pane had no discoverable button at
    # all.
    best = None
    for text, x, y, w, h in words:
        low = text.lower().strip(" .,:;")
        if not any(low == b or low.startswith(b) for b in BUTTONS):
            continue
        if y < H * 0.60:
            continue
        score = (x, y)
        if best is None or score > best[0]:
            best = (score, (x, y), text)

    if best is not None:
        return (best[1], best[2])

    # No single word matched. Multi-word labels arrive split, and buttons that sit
    # side by side arrive joined, so neither "starts with" nor "whole line" is the
    # right test.
    #
    # The accessibility pane's controls OCR as "Back Not Now" on one line: Back is a
    # different button, and the run ended up clicking Back or nothing. Matching every
    # consecutive run of one to three words against the labels finds "Not Now" inside
    # that line and clicks its centre, which is where the button actually is.
    band = [w for w in words if w[2] > H * 0.60]
    band.sort(key=lambda w: (w[2], w[1]))

    def same_line(a, b):
        return abs(a[2] - b[2]) <= 14

    for start in range(len(band)):
        for length in (3, 2, 1):
            chunk = band[start:start + length]
            if len(chunk) != length:
                continue
            if any(not same_line(chunk[0], w) for w in chunk[1:]):
                continue
            phrase = " ".join(w[0] for w in chunk).strip(" .,:;").lower()
            if not any(phrase == b or phrase.startswith(b) for b in BUTTONS):
                continue
            xs = [w[1] for w in chunk]
            ys = [w[2] for w in chunk]
            return ((sum(xs) // len(xs), sum(ys) // len(ys)),
                    " ".join(w[0] for w in chunk))
    return (None, None)


def find_list_rows(words):
    """Rows of a list in the middle of the pane, top first.

    Returning all of them rather than the first lets the caller judge whether this
    is a list at all. That distinction matters: the country pane is a list of two
    hundred names, while the accessibility pane is a paragraph that happens to
    contain the words "System" and "See". Clicking the first capitalised word found
    treated both the same way, and spent twenty steps clicking prose.

    The heading sits at y=325..382 and the list body at y=502..719, measured from a
    captured country pane, so only 480..760 is searched - otherwise the heading
    word "Select" comes back as a row.
    """
    rows = []
    for text, x, y, w, h in words:
        clean = text.strip(" ‘`'\"|")
        if not (3 <= len(clean) <= 24):
            continue
        if not clean[:1].isupper():
            continue
        if not clean.replace("&", "").replace("-", "").replace(" ", "").isalpha():
            continue
        if 480 < y < 760:
            rows.append(((x, y), clean))
    rows.sort(key=lambda r: r[0][1])
    return rows


# The country to select on the first pane. Left at its default the machine ends up
# set to whatever macOS highlights first, which is Afghanistan - a locale nobody
# asked for, and every later pane inherits its language and region from it.
WANTED_COUNTRY = ("United States", "United Kingdom")


def find_wanted_country(words):
    """The row for WANTED_COUNTRY, if the list has scrolled far enough to show it.

    The list is alphabetical and starts at A, so United States is not on screen at
    first. Scrolling to it with the keyboard is the reliable way: click into the
    list to give it focus, then type the first letters, which macOS list widgets use
    to jump. If the wanted name appears in the OCR output, it is clicked directly.
    """
    for wanted in WANTED_COUNTRY:
        first = wanted.split()[0].lower()
        for text, x, y, w, h in words:
            clean = text.strip(" ‘`'\"|")
            if clean.lower().startswith(first) and 480 < y < 760:
                return (x, y), clean
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

        low = joined.lower()

        # --- country pane: pick a country somebody would actually choose -------
        # The list opens with Afghanistan highlighted, and every later pane takes
        # its language and region from whatever is selected here. Clicking into the
        # list and typing the first letters jumps the selection, which is how these
        # list widgets work and is far more reliable than scrolling an unknown
        # distance.
        if "country or region" in low:
            picked, picked_label = find_wanted_country(words)
            if picked is not None and picked_label.lower() == WANTED_COUNTRY[0].lower():
                log("step %d: clicking country %r at %s" % (step, picked_label, picked))
                screen.click(picked[0], picked[1])
                time.sleep(2)
                screen.key("ret")
                time.sleep(8)
                continue

            # The country pane, in three phases, because RETURN DOES NOT ADVANCE IT.
            #
            # Every pane so far has been advanced by clicking Continue - the one
            # pane that did move, moved on a click at (1306,810). Return was assumed
            # to work as the "default button" and never actually did; the log of the
            # last run is return after return with the pane unchanged:
            #
            #   step 3: accepting the selected country (try 1)
            #   step 4: same pane as last step (1 in a row)
            #   step 5: the country pane will not accept Return; moving on
            #
            # So: type the full name to move the list, press Down once to commit the
            # selection to the matched row, then click Continue.
            #
            # Typing only "united" was the earlier mistake - it matches United Arab
            # Emirates, not United States, and Down-walking from there overshot to
            # U.S. Virgin Islands and Vanuatu.
            phase = getattr(main, "country_phase", "type")

            if phase == "type":
                log("step %d: focusing the list and typing %r" % (step, WANTED_COUNTRY[0]))
                rows = find_list_rows(words)
                if rows:
                    screen.click(rows[0][0][0], rows[0][0][1])
                    time.sleep(1)
                for ch in WANTED_COUNTRY[0].lower():
                    screen.key("spc" if ch == " " else ch)
                    time.sleep(0.15)
                time.sleep(2)
                # Down commits the selection to the row the typing matched.
                screen.key("down")
                time.sleep(2)
                main.country_phase = "click"
                continue

            target, label = find_button(words)
            if target is not None:
                log("step %d: country chosen, clicking %r at %s" % (step, label, target))
                screen.click(target[0], target[1])
                time.sleep(8)
                main.country_phase = None
                continue

            # Continue is not enabled, so nothing is selected. Click a row that is
            # definitely visible - the middle of the list - which enables it.
            rows = find_list_rows(words)
            if rows:
                pick = rows[len(rows) // 2]
                log("step %d: Continue not enabled; clicking row %r at %s"
                    % (step, pick[1], pick[0]))
                screen.click(pick[0][0], pick[0][1])
                time.sleep(2)
                continue
            log("step %d: country pane has no rows and no button; pressing Return" % step)
            screen.key("ret")
            time.sleep(8)
            continue

        # Any pane that is not the country list clears its state, so a later return
        # to it starts from the top instead of resuming a half-finished sequence.
        main.country_phase = None

        target, label = find_button(words)

        # No button found. There are two quite different panes this can mean, and
        # confusing them is what produced twenty steps of clicking "System" and
        # "See" in the middle of a paragraph:
        #
        #   the country list  - a real list, Continue grey until a row is chosen
        #   a plain pane      - the control's label was not recognised, and the
        #                       only safe action is the default one, Return
        #
        # So a list row is clicked only when the pane actually looks like a list:
        # several capitalised words in the list band, stacked vertically. One or
        # two such words is body copy, and clicking body copy is never right.
        if target is None:
            rows = find_list_rows(words)
            if len(rows) >= 3:
                row, row_label = rows[0]
                log("step %d: no button; list of %d rows, clicking %r at %s"
                    % (step, len(rows), row_label, row))
                screen.click(row[0], row[1])
                time.sleep(2)
                screen.key("ret")
                time.sleep(8)
                continue
            log("step %d: no button and no list (%d candidates); pressing Return"
                % (step, len(rows)))
            screen.key("ret")
            time.sleep(8)
            continue

        # Stuck on a pane: the text was probably misread, so press the default
        # button rather than clicking the same spot again.
        if stuck >= 2:
            log("step %d: still on the same pane; pressing Return" % step)
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
