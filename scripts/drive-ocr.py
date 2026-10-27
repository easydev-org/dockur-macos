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

# Where this wizard puts its forward control, measured from a frame: OCR read
# "Continue" at (1306, 810). Every pane uses the same lower-right placement, so when
# a pane's label cannot be read, this is where to click.
PRIMARY_BUTTON = (1306, 810)

# Back sits immediately left of it, and OCR reads this one reliably - "Back@1225,810"
# appears in every pane diagnosis. Measured, not guessed.
BACK_BUTTON = (1218, 810)


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
BUTTONS = ("continue", "continue with an apple account", "agree", "accept",
           "not now", "don't sign in", "do not sign in",
           "set up later", "start using", "get started",
           # The transfer pane's forward control, which is not a Continue: it
           # declines the migration. Without it, the run clicks the primary position
           # on that pane forever.
           "don't transfer", "do not transfer",
           # The data & privacy pane.
           "only continue", "continue")

# Single common words used to live in this list - "enable", "allow", "next", "ok",
# "done" - and every one of them appears in ordinary prose. The data & privacy pane
# reads "You can enable only features you trust", and the driver clicked the phrase
# "enable only features," at (855,557) as if it were a button. A label has to be
# something that only a control would say.


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

    # Position beats text on the button row.
    #
    # This wizard puts its forward control at (1306,810) on every pane, and OCR
    # reads the label with varying success - Continue, "Conique", "Contlnue". The
    # diagnosis output from a failing pane showed it plainly:
    #
    #   Back@1225,810 | |@1267,812 | Conique@1306,814
    #
    # Back was read correctly and the button beside it was not, although its
    # position is unambiguous. So a word in the button row at the primary position's
    # x is treated as the primary button whatever it looks like - the position is the
    # reliable part.
    for text, x, y, w, h in words:
        # 30px, not 60: at 60 the separator glyph between Back and Continue (x=1267)
        # also matched, and clicking it did nothing.
        if y > H * 0.72 and abs(x - PRIMARY_BUTTON[0]) <= 30:
            return ((x, y), text)

    # No single word matched. Multi-word labels arrive split, and buttons that sit
    # side by side arrive joined, so neither "starts with" nor "whole line" is the
    # right test.
    #
    # The accessibility pane's controls OCR as "Back Not Now" on one line: Back is a
    # different button, and the run ended up clicking Back or nothing. Matching every
    # consecutive run of one to three words against the labels finds "Not Now" inside
    # that line and clicks its centre, which is where the button actually is.
    # The band is broadened to 45% for the phrase search specifically. Not every
    # forward control lives in the same corner: "Don't Transfer" sits to the left of
    # Continue's position, and its label is long enough that OCR places it higher on
    # the pane. The label list is what keeps this safe - no phrase in it appears in
    # body copy - so the vertical window can be generous.
    band = [w for w in words if w[2] > H * 0.45]
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
        # 480..790: the list body of the country pane sits at 502..719, and the
        # option cards on the transfer pane extend to about 710. Stopping at 760 cut
        # the bottom card off, and 800 is the button row, which must stay excluded.
        if 480 < y < 790:
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

    def signature_from(_mon, _prefix, step):
        """Read the pane's text again, for comparing before and after a click."""
        again = screen.grab("%s-s%02d-after" % (prefix, step))
        words = screen.text(again)
        return (" ".join(w[0] for w in words)[:70]) or None

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

        # No button was recognised. Click where the primary button sits anyway.
        #
        # Every pane of this wizard puts its forward control at the lower right, and
        # a click there is harmless on a pane that has none: Continue was found at
        # (1306,810) by OCR on one pane, and the accessibility pane's Not Now is in
        # the same place but OCR never returns it - it reads the line as "Back Not
        # Now", and no substring test survives that reliably.
        #
        # The alternative was to look for a list and click a row, and that is exactly
        # what went wrong: the accessibility pane's four category tiles (Vision,
        # Motor, Hearing, Cognitive) pass any "looks like a list" test, so the driver
        # spent twenty steps clicking Vision and Motor as if they were countries.
        #
        # Clicking the known button position first, and only then trying a row, fixes
        # that ordering: the button is right where the wizard always puts it.
        if target is None:
            # A search sub-pane: "Looking for other sources..." with a spinner and an
            # "Other Server..." link. This is not the transfer pane itself, it is what
            # clicking one of its cards opens, and it searches for a peer that will
            # never appear in a container.
            #
            # Sixteen steps were spent clicking into that spinner before a cropped
            # frame of the region showed what it was. The way out is Back - the same
            # (1218,810) that OCR reads correctly on every pane that has it - and then
            # the card that declines the transfer.
            # "Other Server..." only exists on the search sub-pane. "Looking for
            # other sources" does NOT work as a marker: that sentence is printed as
            # body text on the transfer pane itself, so the check matched the pane the
            # loop was supposed to move past. Twenty-one steps went:
            #
            #   Transfer pane -> (misread as search) -> Back -> Migration Assistant
            #   -> Continue -> Transfer pane -> ...
            if "other server" in joined.lower():
                log("step %d: inside the search sub-pane; clicking Back" % step)
                screen.click(BACK_BUTTON[0], BACK_BUTTON[1])
                time.sleep(8)
                after = signature_from(screen, prefix, step)
                log("step %d: after Back -> %s" % (step, after))
                # Back lands on Migration Assistant, and the way forward from there is
                # its own Continue - not another card. Clicking a card there opens the
                # transfer pane again, whose first card opens this search sub-pane,
                # and the loop closes:
                #
                #   Back -> Migration Assistant -> card -> search -> Back -> ...
                #
                # The log showed exactly that cycle for five steps. So after Back the
                # primary button is pressed and nothing else.
                log("step %d: clicking the primary button on %r" % (step, after))
                screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
                time.sleep(9)
                after2 = signature_from(screen, prefix, step)
                log("step %d: now at -> %s" % (step, after2))
                continue

            # Print every word OCR found, once per pane. When a pane resists, the
            # only way to fix it is to see what the screen actually says; reasoning
            # about it from the driver's choices has produced three wrong guesses in
            # a row (Vision/Motor tiles, "System"/"See" prose, and now "Looking"
            # from a sentence about Time Machine).
            if stuck == 0:
                log("step %d: pane text for diagnosis: %s"
                    % (step, " | ".join("%s@%d,%d" % (w[0], w[1], w[2])
                                        for w in sorted(words, key=lambda w: (w[2], w[1])))))

            # The transfer pane: two cards side by side in the middle, "From a Mac,
            # Time Machine Backup, or Startup Disk" on the left and "To another Mac"
            # on the right. Neither has readable text where the card itself is, and
            # its Continue stays grey until one is chosen. Clicking the LEFT card is
            # the one that leads somewhere: the right card opens the network search
            # sub-pane, which can never succeed in a container.
            if "transfer information" in joined.lower():
                log("step %d: transfer pane; clicking the left card" % step)
                screen.click(int(W * 0.38), int(H * 0.47))
                time.sleep(3)
                screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
                time.sleep(9)
                after = signature_from(screen, prefix, step)
                log("step %d: after the left card -> %s" % (step, after))
                continue
            # A pane whose button row OCR cannot read. Two things are worth trying,
            # in this order, and the order matters:
            #
            # 1. Click the option list's first row. Panes like "Transfer information
            #    to this Mac" put a selectable list in the middle and leave Continue
            #    grey until a row is chosen - the screenshot of that pane shows
            #    Continue disabled. Clicking the primary position does nothing while
            #    the list is unselected, which is why twelve steps produced no
            #    movement.
            #
            # 2. Only then the primary position, which works on panes that need no
            #    choice at all - it is what carried the run through language,
            #    accessibility, privacy and migration.
            # Click the primary position FIRST, before touching any row.
            #
            # The previous ordering selected a row and then pressed the button, which
            # walked into the accessibility pane's Cognitive category and then spent
            # several steps inside its sub-pages - "Speak Selection", "Typing
            # Feedback", "Hover Text" - because clicking a category card opens that
            # category rather than advancing the wizard. The pane's own forward
            # control was there all along, in the usual place.
            log("step %d: no button; clicking the primary button position" % step)
            screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
            time.sleep(9)
            after = signature_from(screen, prefix, step)
            if after is not None and after != shortened:
                log("step %d: that advanced the pane -> %s" % (step, after))
                continue

            # Only now try a row: this is the case of a pane whose forward button is
            # disabled until a row is chosen, like the transfer pane. The lowest row
            # is picked because option cards stack downward and the "don't" entry is
            # last, while the topmost row on such a pane is the paragraph above them.
            rows_all = find_list_rows(words)
            if rows_all:
                first = rows_all[-1]
                log("step %d: unchanged; clicking list row %r at %s"
                    % (step, first[1], first[0]))
                screen.click(first[0][0], first[0][1])
                time.sleep(3)
                log("step %d: then the primary button position again" % step)
                screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
                time.sleep(9)
                after = signature_from(screen, prefix, step)
                if after is not None and after != shortened:
                    log("step %d: that advanced the pane -> %s" % (step, after))
                    continue

            # Still the same pane: it wants something selected. Click a row in the
            # middle of the window, well away from the category tiles at the top.
            rows = find_list_rows(words)
            if rows:
                pick = rows[len(rows) // 2]
                log("step %d: unchanged; clicking row %r at %s" % (step, pick[1], pick[0]))
                screen.click(pick[0][0], pick[0][1])
                time.sleep(3)
                screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
                time.sleep(8)
                continue

            # No rows either. On the transfer pane the selectable options are drawn
            # as icons with their labels underneath, and OCR returns only the
            # instruction paragraphs - the whole card area at y=400..560 comes back
            # empty. Clicking into that empty region is what a person does: the cards
            # are there, they just have no readable text.
            log("step %d: unchanged and no rows; clicking the option-card area" % step)
            screen.click(W // 2, int(H * 0.44))
            time.sleep(3)
            screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
            time.sleep(9)
            after = signature_from(screen, prefix, step)
            if after is not None and after != shortened:
                log("step %d: that advanced the pane -> %s" % (step, after))
                continue

            log("step %d: still unchanged; pressing Return" % step)
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
