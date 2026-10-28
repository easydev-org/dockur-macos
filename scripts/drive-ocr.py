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

# The migration pane's declining control, read off a screenshot of that pane:
# "Not now" sits alone on the left of its button row, well away from Back and
# Continue. OCR does not return it, so its position has to be known.
NOT_NOW_BUTTON = (604, 810)

# Keyboard Setup Assistant's Quit button, read off a screenshot of it. That dialog
# spans the whole window (x=560..1360, y=216..849) rather than floating as a small
# sheet, so its buttons sit on the ordinary button row - Quit at 1223 and Continue at
# 1304. Clicking (604,810), where the wizard's declining options live, hit nothing,
# which is why twenty-one steps went by with the dialog still up.
KEYBOARD_QUIT = (1223, 814)

# Same shape of control on the panes that follow: "Set Up Later" on the Apple ID
# pane, and equivalents elsewhere, sit alone on the LEFT of the button row while
# Back and Continue sit right. Not now proved this is where the declining option
# lives, so the same position is tried for the panes that offer one.
SKIP_BUTTON = (604, 810)

# The Apple ID confirmation sheet's own buttons, read off a screenshot of it:
#
#   Don't Skip (899,634)   Skip (1026,634)
#
# The sheet spans roughly x=833..1090, y=409..663 - far smaller than the window
# behind it and centred well above the main button row. Six earlier candidates were
# swept around (1080..1230, 680..720), which is outside the sheet entirely, so every
# click landed on the page behind it and the dialog stayed up.
SHEET_SKIP = (1026, 634)

# The account form's fields, read off a screenshot of it. Two details matter and
# both were wrong in the guessed version:
#
#   Full Name    (942,455)
#   Account Name (942,486)
#   Password     (896,528)   \
#   Verify      (1057,528)   / on ONE row, side by side
#   Hint         (978,562)
#
# The guessed version stacked all five vertically at y=470..710, so Password and
# Verify were never both filled, and Continue kept answering "You haven't provided
# all of the requested information".
# The account this wizard creates. It has to match src/install.sh's SETUP_USERNAME /
# SETUP_PASSWORD, which the first-boot daemon uses for the same account: two different
# names for one machine is a trap, and it is what the previous run shipped - the
# framework said admin/admin while the form said sandbox/sandbox.
#
# Read from the environment so the workflow sets it in one place.
ACCOUNT_USER = os.environ.get("SETUP_USERNAME", "docker")
ACCOUNT_PASS = os.environ.get("SETUP_PASSWORD", "admin")

ACCOUNT_FIELDS = (
    ("full name", 942, 455, ACCOUNT_USER),
    ("account name", 942, 486, ACCOUNT_USER),
    ("password", 896, 528, ACCOUNT_PASS),
    ("verify", 1057, 528, ACCOUNT_PASS),
    ("hint", 978, 562, "hint"),
)


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

    # Characters that need a specific QKeyCode rather than the letter itself.
    SHIFTED = {
        "|": "backslash", "!": "1", "@": "2", "#": "3", "$": "4",
        "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
        "_": "minus", "+": "equal", "{": "bracket_left",
        "}": "bracket_right", ":": "semicolon", '"': "apostrophe",
        "<": "comma", ">": "dot", "?": "slash", "~": "grave_accent",
    }
    PLAIN = {
        " ": "spc", "-": "minus", "=": "equal", "/": "slash",
        ".": "dot", ",": "comma", ";": "semicolon", "'": "apostrophe",
        "\\": "backslash", "[": "bracket_left", "]": "bracket_right",
        "`": "grave_accent",
    }

    def type(self, text, delay=0.12):
        """Type text as real key events, including capitals and punctuation.

        WHY THIS EXISTS. Earlier versions passed the character straight to the
        keycode channel and, for capitals, sent "shift-s" - which is not a QKeyCode
        at all. The frames show the result: `ls /System/Library/LaunchDaemons` arrived
        as a lowercase path and answered 'No such file or directory', and every command
        beginning with a capital - systemsetup, launchctl - was quietly mangled.

        A capital is shift held with the letter, so it is sent as a chord.
        """
        for ch in text:
            if ch.isupper():
                qmpmod.key_combo(self.q, "shift_l", ch.lower())
            elif ch in self.SHIFTED:
                qmpmod.key_combo(self.q, "shift_l", self.SHIFTED[ch])
            elif ch in self.PLAIN:
                qmpmod.key(self.q, self.PLAIN[ch])
            elif ch.isalnum():
                qmpmod.key(self.q, ch)
            else:
                # Anything unmapped is skipped rather than guessed at, so a missing
                # character shows up as a wrong command instead of a silently altered one.
                continue
            time.sleep(delay)

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


def probe_ssh(log, host="127.0.0.1", port=2222, user=None):
    """Say whether remote login is reachable, and log the evidence either way.

    QEMU forwards the guest's port 22 to 2222 on the host, and firstboot.sh enables
    sshd inside macOS. Both halves are new, so this reports what it finds rather than
    assuming the pair worked.

    A TCP connect answers "is sshd listening". Waiting for the banner does too, and the
    banner names the server. Authentication is not attempted here: this process has no
    way to supply a password, and failing that check would say nothing about the setup.
    """
    import socket as _socket
    user = user or ACCOUNT_USER

    def tcp_open():
        s = _socket.socket()
        s.settimeout(10)
        try:
            s.connect((host, port))
            try:
                banner = s.recv(128).decode("utf-8", "replace").strip()
            except Exception:
                banner = ""
            return True, banner
        except Exception as exc:
            return False, str(exc)
        finally:
            s.close()

    opened, detail = tcp_open()
    log("ssh probe: tcp %s:%d open=%s detail=%r" % (host, port, opened, detail))
    if not opened:
        log("ssh probe: nothing on %d - sshd did not start in the guest" % port)
        return False
    if "ssh" in detail.lower():
        log("ssh probe: sshd is serving on port %d (banner above)" % port)
    else:
        log("ssh probe: port %d is open but the banner is not an ssh one" % port)
    return True


def enable_ssh_from_desktop(screen, log, prefix):
    """Turn remote login on by clicking System Settings, not by typing a command.

    WHY NOT THE KEYBOARD. Typing a command through QMP was tried nine ways and failed
    each time for a different reason, all of them measured from frames:

      sudo -S launchctl load ...   arrived as "sudo -s", path cut at /system/libra/
      launchctl list | grep ssh    arrived with a backslash where the pipe should be
      .../launchdaemons/ssh.plist  arrived as ".../launchdaemons", filename dropped

    Shift chords drop keys, the pipe does not exist, long lines get truncated. Each fix
    exposed the next. Clicking is a different channel: it moves a pointer and never has
    to reproduce a character exactly.

    WHY THE SEARCH FIELD IS SAFE. Spotlight takes "sharing" - six lowercase letters,
    no shift, no punctuation - and macOS indexes the Sharing pane of System Settings
    under it, so the pane opens directly without hunting through the sidebar.

    Then Remote Login is found by reading the screen, not by a fixed coordinate, and
    its switch is toggled. Both are ordinary clicks.
    """
    def open_spotlight():
        for attempt in (1, 2, 3):
            qmpmod.key_combo(screen.q, "meta_l", "spc")
            time.sleep(3)
            if screen.text(screen.grab(prefix + "-spotlight-try")):
                log("spotlight opened on attempt %d" % attempt)
                return True
            log("spotlight not visible after attempt %d" % attempt)
        return False

    def find_words(png):
        return screen.text(png)

    if not open_spotlight():
        log("ssh: spotlight never opened; cannot reach System Settings")
        return False

    # "sharing" is the whole search term: lowercase, short, and it lands on the pane
    # that holds Remote Login.
    screen.type("sharing", delay=0.18)
    time.sleep(2.5)
    screen.key("ret")
    time.sleep(12)
    png = screen.grab(prefix + "-sharing")

    words = find_words(png)
    log("sharing pane text: %s" % " | ".join("%s@%d,%d" % (w[0], w[1], w[2])
                                              for w in sorted(words, key=lambda w: (w[2], w[1])))[:600])

    # Remote Login is the row to click. Its label is two words, so the row is found by
    # looking for "remote" and taking that position - the switch sits to its right.
    target = None
    for w in words:
        if w[0].strip(":,").lower() == "remote":
            target = (w[1], w[2])
            log("found Remote Login label at %s" % (target,))
            break

    if target is None:
        log("ssh: Remote Login row is not on screen; the pane may need scrolling")
        return False

    # Click the switch, which is right of the label. A click on the label itself only
    # selects the row.
    switch = (target[0] + 300, target[1])
    log("clicking the Remote Login switch at %s" % (switch,))
    screen.click(switch[0], switch[1])
    time.sleep(6)
    screen.grab(prefix + "-ssh-toggled")

    # Turning it on raises an administrator prompt; type the password and confirm.
    after = screen.text(screen.grab(prefix + "-ssh-after-toggle"))
    joined = " ".join(w[0] for w in after).lower()
    log("after toggle: %s" % " ".join(w[0] for w in after)[:200])
    if "password" in joined or "administrator" in joined:
        log("administrator prompt; typing the password")
        screen.type(ACCOUNT_PASS, delay=0.2)
        time.sleep(1)
        screen.key("ret")
        time.sleep(6)
        screen.grab(prefix + "-ssh-confirmed")

    return True


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

    for step in range(1, 45):
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

        low = joined.lower()

        # The login window, which the published desktop disk boots into.
        #
        # The baked disk arrived at Setup Assistant, so the driver only ever knew how
        # to walk a wizard. A disk that has already been driven boots to a login
        # window instead: "docker / Your password is required to log in". Nine steps
        # were spent clicking the primary position at it, which is a window with no
        # buttons there.
        #
        # Password only: the account is selected and the field already has focus, so
        # typing goes into it. Return submits.
        if "password is required" in low or ("your password" in low and "log in" in low):
            log("step %d: login window; typing the account password" % step)
            screen.type(ACCOUNT_PASS, delay=0.2)
            time.sleep(1)
            screen.key("ret")
            time.sleep(14)
            log("step %d: after login -> %s"
                % (step, signature_from(screen, prefix, step)))
            continue

        # Keyboard Setup Assistant first, BEFORE the desktop check.
        #
        # It appears on top of the desktop itself - the guest has no Apple keyboard to
        # identify - and its window does not stop the Finder menu bar from being
        # readable behind it. So a check for "finder" fires and declares success while
        # this dialog still covers the screen. Ordering is the whole fix.
        #
        # "Quit" is the control that dismisses it, at the same lower-left position the
        # wizard's declining options use.
        if "keyboard setup assistant" in low:
            # This dialog is the size of the whole window (x=560..1360, y=216..849),
            # so its buttons are on the ordinary button row: Quit at 1223, Continue at
            # 1304. Twenty-one steps were spent clicking (604,810) - where the wizard's
            # left-hand declining options live - and that dialog has nothing there.
            log("step %d: keyboard setup assistant; clicking Quit at %s"
                % (step, KEYBOARD_QUIT))
            screen.click(KEYBOARD_QUIT[0], KEYBOARD_QUIT[1])
            time.sleep(7)
            after = signature_from(screen, prefix, step)
            log("step %d: after Quit -> %s" % (step, after))
            if after is not None and "keyboard setup" in after.lower():
                # Continue is the other button; either one closes the dialog, and
                # Continue is the one that does not raise "are you sure".
                log("step %d: still up; clicking Continue instead" % step)
                screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
                time.sleep(7)
                log("step %d: now at -> %s"
                    % (step, signature_from(screen, prefix, step)))
            continue

        # Only now: a desktop has a menu bar and no wizard button.
        if "finder" in low and "continue" not in low:
            log("DESKTOP REACHED at step %d" % step)
            # The desktop is reached. Now enable remote login by CLICKING, not typing.
            #
            # Every attempt to type a shell command into Terminal failed, each for a
            # different measured reason - shift chords dropped keys, the pipe arrived as
            # a backslash, long lines lost their tails. A click does not have to
            # reproduce a character, so none of that applies: Spotlight takes the word
            # "sharing", System Settings opens on the pane that holds Remote Login, and
            # the switch is found by reading the screen.
            enable_ssh_from_desktop(screen, log, prefix)
            probe_ssh(log)
            # The workflow publishes the disk when it sees DESKTOP REACHED in the log,
            # so this run ships a desktop disk whether or not the switch was found.
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

        # The migration pane has three controls on its button row:
        #
        #   Not now (604,810) | Back (1220,810) | Continue (1306,810)
        #
        # Continue opens the transfer sub-pane, which searches the network for a peer
        # Mac and never finds one - a dead end that consumed many runs. "Not now" is
        # the control that declines the whole migration, and it is a plain text button
        # on the left, so OCR does not return it as a label and the position rule does
        # not reach it either.
        #
        # Reading the pane as a screenshot is what found it: the three labels are
        # legible in the image and invisible in the text extraction.
        if "migration assistant" in joined.lower():
            log("step %d: migration pane; clicking Not now at %s" % (step, NOT_NOW_BUTTON))
            screen.click(NOT_NOW_BUTTON[0], NOT_NOW_BUTTON[1])
            time.sleep(9)
            after = signature_from(screen, prefix, step)
            log("step %d: after Not now -> %s" % (step, after))
            continue

        # The Apple ID pane: "Sign In with Your Apple ID" with a "Set Up Later" option
        # that lives on the left of the button row, exactly where Not now was. Signing
        # in is neither possible nor wanted in a container, and this is the control
        # that says so.
        if "apple id" in joined.lower():
            log("step %d: apple id pane; clicking Set Up Later at %s"
                % (step, SKIP_BUTTON))
            screen.click(SKIP_BUTTON[0], SKIP_BUTTON[1])
            time.sleep(9)
            after = signature_from(screen, prefix, step)
            log("step %d: after Set Up Later -> %s" % (step, after))
            if after is not None and "are you sure" in after.lower():
                # A confirmation sheet: "Are you sure you want to skip sign in with an
                # Apple ID?" with Skip and Cancel. It is a smaller sheet centred over
                # the main window, so its buttons are NOT at the main window's
                # (1306,810) - clicking there hits the sheet's own blank area and the
                # dialog stays up, which is what the log shows step after step.
                #
                # The sheet's buttons sit on its lower edge, and "Skip" is the
                # confirming one, on the right of that pair. Sweep along the sheet's
                # button row: the sheet is narrower than the window, so its right-hand
                # button is well left of 1306.
                log("step %d: confirmation sheet; clicking Skip at %s"
                    % (step, SHEET_SKIP))
                screen.click(SHEET_SKIP[0], SHEET_SKIP[1])
                time.sleep(9)
                now_at = signature_from(screen, prefix, step)
                log("step %d: now at -> %s" % (step, now_at))
                if now_at is not None and "are you sure" in now_at.lower():
                    # Only if the click missed does it try the other sheet button.
                    log("step %d: still up; clicking the left sheet button" % step)
                    screen.click(899, 634)
                    time.sleep(8)
                    log("step %d: now at -> %s"
                        % (step, signature_from(screen, prefix, step)))
                continue
            continue

        # The account creation pane. This is the one pane that cannot be clicked
        # through - it needs five fields typed, and until they are filled Continue
        # refuses with "You haven't provided all of the requested information".
        #
        # The fields are stacked under the pane's title, and each is clicked before
        # typing because tab order is not guaranteed. Names here are deliberately
        # simple: the account must exist for the machine to reach a desktop.
        if "computer account" in joined.lower() or "create a computer" in joined.lower():
            if not getattr(main, "account_filled", False):
                log("step %d: account pane; filling the form" % step)
                for label, x, y, value in ACCOUNT_FIELDS:
                    log("account: %s at (%d,%d)" % (label, x, y))
                    screen.click(x, y)
                    time.sleep(1.2)

                    # Only clear on a retry. Clearing unconditionally broke the last
                    # run: Cmd-A, backspace and typing raced, the delete landed after
                    # the first character, and a full name of "Docker" came out as
                    # "ocker". The password and its verification then disagreed - 5
                    # dots against 6 - which is why Continue refused the form.
                    # Clear the field on EVERY pass, thoroughly.
                    #
                    # The frame from the last run shows why: Full name docker was
                    # right, both password fields held five dots and matched, and only
                    # Account name was wrong - "dockerdocker". The retry appended to
                    # the value the first pass had left, because Cmd-A followed by
                    # backspace does not always take on these fields.
                    #
                    # Three methods in sequence, with a pause after each: Cmd-A and
                    # backspace, again, then a run of backspaces. The field starts empty,
                    # so this costs nothing on the first pass and it cannot leave stale
                    # text behind on a retry.
                    for _ in range(2):
                        qmpmod.key_combo(screen.q, "meta_l", "a")
                        time.sleep(0.7)
                        screen.key("backspace")
                        time.sleep(0.7)
                    for _ in range(24):
                        screen.key("backspace")
                        time.sleep(0.05)
                    time.sleep(1.0)

                    for ch in value:
                        screen.key(ch)
                        # 0.2s per character. At 0.12 the guest dropped and reordered
                        # keys, and the field contents are the only evidence that it did.
                        time.sleep(0.2)
                    time.sleep(0.8)
                main.account_passes = getattr(main, "account_passes", 0) + 1
                main.account_filled = True
                log("account form filled (pass %d)" % main.account_passes)
                screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
                time.sleep(9)
                log("step %d: after Continue -> %s"
                    % (step, signature_from(main, prefix, step)))
                continue
            # A second pass means a field was rejected; try once more from the top.
            log("step %d: account pane again; retrying the form" % step)
            main.account_filled = False
            continue

        # The location-services confirmation, which appears right after the account
        # is accepted. Its declining answer is the one to give: nothing in a container
        # has a location, and the sheet's buttons are again well inside the window.
        if "location services" in joined.lower():
            # This one is a full pane, not a sheet: "Enable Location Services" with an
            # explanation and the usual Back/Continue row. The sheet positions tried
            # first all landed inside its body text, which is why eight steps produced
            # no movement at all.
            #
            # Continuing here enables location services, which is harmless in a
            # container - there is no location to disclose - and it is the pane's own
            # forward control, unlike the Apple ID sheet which needed its inner button.
            # Two stages under this heading, and they need different targets:
            #
            # 1. "Enable Location Services" - a full pane, forward control at the usual
            #    primary position.
            # 2. "Are you sure you don't want to use Location Services?" - the sheet
            #    that appears after declining. Its buttons are inside the sheet, the
            #    same geometry as the Apple ID one.
            #
            # The log for this run shows stage 1 working (the pane advanced at 19:02:16)
            # and stage 2 then receiving primary clicks forever.
            if "are you sure" in joined.lower():
                # The sheet's button is not where it is on the Apple ID sheet - that
                # one was measured at (1026,634) and worked, this one has not moved on
                # four attempts. Rather than guess again, a wider set of candidates is
                # tried including the main button row, since this dialog may be
                # positioned differently.
                log("step %d: location confirmation; sweeping sheet candidates" % step)
                tries = getattr(main, "loc_sheet", 0)
                # (1000,660) is measured: it is the one that dismissed this sheet, at
                # step 28. Tried first now, with the others kept as fallbacks.
                spots = ((1000, 660), PRIMARY_BUTTON, SHEET_SKIP, (899, 634),
                         (1306, 700), (1222, 810), (960, 700), (1130, 660))
                if tries < len(spots):
                    lx, ly = spots[tries]
                    main.loc_sheet = tries + 1
                    log("step %d: candidate (%d,%d) [%d/%d]"
                        % (step, lx, ly, tries + 1, len(spots)))
                    screen.click(lx, ly)
                    time.sleep(8)
                    now_at = signature_from(screen, prefix, step)
                    if now_at is None or "are you sure" not in now_at.lower():
                        log("step %d: confirmed at (%d,%d) -> %s" % (step, lx, ly, now_at))
                        main.loc_sheet = 0
                else:
                    log("step %d: every candidate missed; resetting the sweep" % step)
                    main.loc_sheet = 0
                continue

            log("step %d: location services; clicking the primary button" % step)
            for lx, ly in (PRIMARY_BUTTON, (604, 810), (1222, 810)):
                screen.click(lx, ly)
                time.sleep(8)
                now_at = signature_from(screen, prefix, step)
                if now_at is None or "enable location" not in now_at.lower():
                    log("step %d: dismissed at (%d,%d) -> %s" % (step, lx, ly, now_at))
                    break
            continue

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
                # This pane is a dead end and always will be. A full screenshot shows
                # why: there are no option cards at all, only a spinner with
                # "Looking for other sources...", an "Other Server..." link, and two
                # paragraphs of explanation. Continue is grey and stays grey, because
                # the search it is waiting on wants a peer Mac on the network and there
                # is none - there never will be, inside a container.
                #
                # The real mistake was upstream: the Migration Assistant pane was
                # advanced with Continue, which opens this search. Its forward control
                # is "Don't Transfer" - the whole point of that pane is to offer a way
                # past the migration.
                #
                # So go back, and take the declining option there rather than trying to
                # satisfy this pane.
                # Before going Back, try the skip link. macOS puts "Don't transfer any
                # information now" as a text link in the lower-left of this pane rather
                # than as a button, so OCR reads it as prose and the primary button
                # stays grey until it is pressed. Sweeping the lower-left band is the
                # only way to find it without reading its exact wording.
                log("step %d: transfer pane; looking for the skip link" % step)
                # Positions are absolute now, not fractions. The frame gives the exact
                # geometry: the window spans x=560..1360, its paragraphs end at y=706,
                # and the button row is at y=800..823. "Don't transfer any information
                # now" is a link sitting under the last paragraph, left-aligned inside
                # the window - so around x=700..900, y=735..775. Fractions of the full
                # screen put the earlier sweep in the wrong place entirely.
                nested = getattr(main, "transfer_sweep", 0)
                spots = ((760, 745), (820, 745), (700, 755), (860, 750),
                         (760, 770), (900, 745), (680, 740), (800, 765))
                if nested < len(spots):
                    px_, py_ = spots[nested]
                    main.transfer_sweep = nested + 1
                    log("step %d: clicking skip-link candidate (%d,%d) [%d/%d]"
                        % (step, px_, py_, nested + 1, len(spots)))
                    screen.click(px_, py_)
                    time.sleep(3)
                    screen.click(PRIMARY_BUTTON[0], PRIMARY_BUTTON[1])
                    time.sleep(7)
                    now_at = signature_from(screen, prefix, step)
                    log("step %d: now at -> %s" % (step, now_at))
                    if now_at is not None and "transfer" not in now_at.lower():
                        log("step %d: the skip link worked" % step)
                        main.transfer_sweep = 0
                    continue

                log("step %d: transfer search pane is a dead end; going Back" % step)
                main.transfer_sweep = 0
                screen.click(BACK_BUTTON[0], BACK_BUTTON[1])
                time.sleep(8)
                after = signature_from(screen, prefix, step)
                log("step %d: after Back -> %s" % (step, after))
                # On Migration Assistant the declining control is the primary one, in
                # the usual lower-right position - that is how macOS offers "don't do
                # this" as the default path. Sweeping left of centre lands on Back,
                # which goes backwards to Data & Privacy; the primary position is what
                # moves forward. Tried primary first, and the left-hand positions only
                # if it does not move.
                for fx in (0.68, 0.70, 0.66):
                    screen.click(int(W * fx), 810)
                    time.sleep(8)
                    now_at = signature_from(screen, prefix, step)
                    if now_at is not None and "migration assistant" not in now_at.lower():
                        log("step %d: x=%.2f advanced -> %s" % (step, fx, now_at))
                        break
                    log("step %d: x=%.2f did not advance (still %r)" % (step, fx, now_at))
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
