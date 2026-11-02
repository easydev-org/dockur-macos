#!/usr/bin/env python3
"""Drive macOS 26's Setup Assistant with coordinates only.

WHY THIS EXISTS. drive-ocr.py decides what to do by reading the pane. That works on 14 and 15,
where every pane has a label it can find, and it has cost days on 26, where:

  the privacy pane's Continue leads into a pairing handshake that a container can never
  complete, and the only way past it is a Not Now that lives for about two seconds before the
  failure alert replaces it - far too short for a screenshot, a tesseract pass and a decision

  the country list ignores clicks and needed keyboard navigation plus a Tab-and-Space commit,
  each of which had to be discovered by reading frames

None of that variation is in the pane's GEOMETRY. The wizard is a fixed 1920x1080 window that
draws its controls in the same places every boot; only the labels and the stack of panes differ
between releases. So the pane sequence is written out as steps and each step is a click, a key
or a scroll at a measured position - no OCR, no branching, no sight.

Every coordinate below was measured from a captured frame, and the comment beside it names the
frame or the log line it came from.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qmp as qmpmod


# ---------------------------------------------------------------- measured positions

# The wizard's forward button. Same place on every pane of every release.
PRIMARY = (1306, 810)
# Not Now on three-button panes (migration): leftmost of the row.
NOT_NOW = (604, 810)
# Not Now on the two-button pairing pane: measured x=1070..1194 at y=795..815, so (1132,805).
# (1218,810) is the 20px gap between those two buttons and does nothing.
CODE_NOT_NOW = (1132, 801)
# Back, on panes that have one.
BACK = (1218, 810)
# The 'Unable to Pair Computers' alert: single blue OK spanning x=843..1076, y=610..643.
ALERT_OK = (959, 626)

# Country list. Typing jumps by first letter; the list then sits near T, and five Page Downs
# land it on the U section with United States still on screen.
COUNTRY_LIST = (900, 500)

# The transfer pane's four cards, measured from frame-062 of the coordinate run. OCR gives
# their label baselines, and the cards' radio marks sit 12px left of the labels:
#
#   How do you want to transfer your information?        y=599
#   (1) From a Mac, Time Machine, or startup disk        y=636..648   label x=758..
#   (2) From a Windows PC                                y=659..671   label x=758..
#   (3) Set up with iPhone or iPad                       y=683..695   label x=758..
#   (4) Set up as new                                    y=707..722   label x=758..
#   radio mark for (1)                                   x=732..754, y=629..641
#
# The card row is left-aligned at x=743, so the whole row is clickable there. The value used
# before, (869,671), lands on card TWO - which is why the pane kept coming back with
# 'From a Mac, Time Machine, or startup disk' still selected.
SET_UP_AS_NEW = (743, 714)

# 'Are you sure you want to skip sign in?' sheet - Skip is the right-hand of its pair.
SHEET_OK = (1026, 634)
# The account form, top to bottom.
FULL_NAME = (942, 455)
ACCOUNT_NAME = (942, 486)
PASSWORD = (896, 528)
VERIFY = (1057, 528)
HINT = (978, 562)


def find_button_row(ppm, y_lo=780, y_hi=825):
    """Locate the wizard's button pills on one frame by their fill colour.

    OCR cannot see these. On the transfer pane both buttons are grey-on-grey - (233,233,233)
    fill with dark lettering - and tesseract returned nothing at psm 3, 6, 7, 11 or 13, on the
    whole frame and on a 3x crop. A colour scan does not care about contrast:

      y=792:  x=1071..1193  (233,233,233)   -> one pill
              x=1215..1337  (233,233,233)   -> the next
              x=1193..1215  background      -> the gap between them

    Returns [(centre_x, centre_y), ...] left to right, empty if the row is not there.
    """
    got = _read_ppm(ppm)
    if not got:
        return []
    w, h, data = got
    rows = []
    for y in range(y_lo, min(y_hi, h)):
        span = []
        for x in range(700, 1450):
            i = (y * w + x) * 3
            if i + 2 >= len(data):
                break
            r, g, b = data[i], data[i + 1], data[i + 2]
            if abs(r - 233) <= 10 and abs(g - 233) <= 10 and abs(b - 233) <= 10:
                span.append(x)
        if len(span) > 60:
            rows.append((y, span))
    if not rows:
        return []
    # One representative row: the longest run, which is the middle of the pills.
    y, span = max(rows, key=lambda t: len(t[1]))
    pills = []
    start = span[0]
    prev = span[0]
    for x in span[1:]:
        if x - prev > 8:
            if prev - start > 60:
                pills.append(((start + prev) // 2, y))
            start = x
        prev = x
    if prev - start > 60:
        pills.append(((start + prev) // 2, y))
    return pills

def log(msg):
    print("[xy %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- pacing by pixels
#
# THE FIRST RUN PROVED THIS IS NEEDED. All fifty steps executed against a black screen and the
# last frame - taken after the sequence finished - showed the wizard only just arriving at the
# migration pane. A fixed 45-second wait assumed the guest boots into Setup Assistant in the
# time a warm VM takes; this one takes several minutes, and every click before that landed on
# nothing.
#
# These helpers look at the picture, but only to decide WHEN to act - never WHAT to do. They are
# colour and difference tests, not OCR: no text is read, no label is matched, and the pane
# sequence stays exactly as written below.

def _read_ppm(path):
    try:
        with open(path, "rb") as fh:
            if fh.readline().strip() != b"P6":
                return None
            line = fh.readline()
            while line.startswith(b"#"):
                line = fh.readline()
            w, h = map(int, line.split())
            fh.readline()
            return w, h, fh.read()
    except (OSError, ValueError):
        return None


def _dark_fraction(path):
    """How much of the frame is black. 1.0 means nothing is being drawn."""
    got = _read_ppm(path)
    if not got:
        return 1.0
    w, h, data = got
    dark = total = 0
    for y in range(0, h, 8):
        row = y * w * 3
        for x in range(0, w, 8):
            i = row + x * 3
            if i + 2 >= len(data):
                break
            if data[i] < 40 and data[i + 1] < 40 and data[i + 2] < 40:
                dark += 1
            total += 1
    return dark / float(total or 1)


def _signature(path):
    """A coarse brightness grid, for asking 'did the picture change?'."""
    got = _read_ppm(path)
    if not got:
        return None
    w, h, data = got
    sig = []
    for gy in range(12):
        for gx in range(16):
            x = (gx * w) // 16
            y = (gy * h) // 12
            i = (y * w + x) * 3
            sig.append(sum(data[i:i + 3]) // 3 if i + 2 < len(data) else 0)
    return sig


def _changed(a, b, threshold=6):
    if a is None or b is None:
        return True
    return sum(1 for p, q in zip(a, b) if abs(p - q) > threshold) > 8


class Driver:
    def __init__(self, sock, frames_dir, screen_w=1920, screen_h=1080):
        self.frames_dir = frames_dir.rstrip("/")
        # THE .qmp SUFFIX IS NOT OPTIONAL. drive-in-container.sh creates monitor.sock (HMP,
        # plain text) and monitor.sock.qmp (QMP, JSON). Connecting to the first and parsing its
        # greeting as JSON raises 'Expecting value: line 1 column 1' - which is exactly how the
        # first coordinate-driven run died, before sending a single click.
        if not sock.endswith(".qmp"):
            sock = sock + ".qmp"
        self.q = qmpmod.QMP(sock)
        self.w = screen_w
        self.h = screen_h
        self.serial = 0

    # ------------------------------------------------------------ primitives

    def click(self, x, y, settle=1.2, why=""):
        self.serial += 1
        log("click (%d,%d)%s" % (x, y, (" - " + why) if why else ""))
        qmpmod.click(self.q, x, y)
        time.sleep(settle)

    def key(self, name, settle=0.4, times=1):
        log("key %s x%d" % (name, times))
        for _ in range(times):
            qmpmod.key(self.q, name)
            time.sleep(settle)

    def keys(self, text, per_char=0.3, clear=True):
        """Type into the focused field, clearing it first.

        0.3s per character: at 0.15 the guest dropped characters, and on the country list that
        showed up as the jump landing on T instead of U.
        """
        if clear:
            for _ in range(2):
                qmpmod.key_combo(self.q, "meta_l", "a")
                time.sleep(0.5)
                qmpmod.key(self.q, "backspace")
                time.sleep(0.4)
            for _ in range(24):
                qmpmod.key(self.q, "backspace")
                time.sleep(0.04)
            time.sleep(0.6)
        for ch in text:
            qmpmod.key(self.q, "spc" if ch == " " else ch)
            time.sleep(per_char)

    def scroll(self, notches, x=960, y=550):
        """Positive scrolls down, per qmp.scroll's docstring."""
        log("scroll %+d at (%d,%d)" % (notches, x, y))
        qmpmod.scroll(self.q, x, y, notches)
        time.sleep(1.0)

    def shot(self, prefix, name):
        """A frame for the record - never read, only kept, so a failure can be looked at later.

        TWO PATHS, AND THEY MUST NOT BE CONFUSED. qemu runs inside the container and resolves
        'filename' against ITS filesystem, where the host's frames directory is mounted at
        /frames. The earlier version passed the host-style path into the QMP call, qemu wrote
        it somewhere the host never sees, and the artifact came back with no frames at all -
        while the log showed no error, because qemu had succeeded.
        """
        self.serial += 1
        leaf = "%s-%03d-%s.ppm" % (prefix, self.serial, name)
        in_container = "/frames/" + os.path.basename(leaf)
        try:
            self.q.execute("screendump", filename=in_container)
        except Exception as exc:
            log("screendump failed at %s: %s" % (name, exc))
            return
        # Wait for the host to see it, rather than guessing with a sleep.
        host_path = os.path.join(self.frames_dir, os.path.basename(leaf))
        for _ in range(25):
            if os.path.exists(host_path):
                return host_path
            time.sleep(0.2)
        log("frame never appeared at %s" % host_path)
        return None

    def wait_for_wizard(self, prefix, timeout=900):
        """Block until the screen stops being black.

        The guest takes minutes to reach Setup Assistant - not the 45 seconds the first run
        allowed, which is why all fifty of its steps landed on nothing. Nothing is read here
        beyond 'is this frame black'; the pane that finally appears is not inspected.
        """
        log("waiting for the wizard to draw something")
        start = time.time()
        while time.time() - start < timeout:
            path = self.shot(prefix, "boot")
            if path is not None and _dark_fraction(path) < 0.85:
                log("screen has content after %ds" % int(time.time() - start))
                return True
            time.sleep(5)
        log("nothing drawn after %ds - is the guest alive?" % timeout)
        return False

    def settle(self, prefix, name, quiet=2.0, timeout=90):
        """Wait until two consecutive frames match, so a click lands on a finished pane.

        Transition animations mean a click sent the instant a pane begins drawing can hit a
        control that is still moving. This is a picture comparison, not a read.
        """
        previous = None
        start = time.time()
        while time.time() - start < timeout:
            path = self.shot(prefix, name)
            sig = _signature(path) if path else None
            if previous is not None and not _changed(previous, sig):
                return True
            previous = sig
            time.sleep(quiet)
        log("pane never settled at %s" % name)
        return False

    def close(self):
        try:
            self.q.close()
        except Exception:
            pass


# ---------------------------------------------------------------- the sequence
#
# Written as the pane stack 26 actually shows, in order, from the logs of the runs that got
# furthest. No conditionals: a step either happens or the run is wrong, and the frames kept
# alongside it say where.
#
#   1  country pane          type the name, page to U, keyboard-walk, Tab+Space commits
#   2  transfer pane         'Set up as new' card, then Continue
#   3  languages             Continue
#   4  accessibility         Return, then Continue
#   5  data & privacy        Continue - and this is what opens the pairing pane
#   6  pairing code pane     Not Now at (1132,805), pressed within two seconds
#   7  account creation      five fields, Continue
#   8  apple account         Set Up Later
#   9  location services     Continue once, then the confirmation sheet
#   10 ...                   Continue to the desktop


def country_pane(d, prefix):
    """Pick United States out of the country list.

    Measured behaviour on 26: a click anywhere in the list moves nothing; typing scrolls it by
    first letter and it comes to rest on T; five Page Downs put the U section on screen with
    United States near the bottom; and Return does not commit, but Tab-walking the focus ring
    and pressing Space does.
    """
    d.settle(prefix, "country-settle")
    d.shot(prefix, "country")
    d.click(*COUNTRY_LIST, settle=0.8, why="focus the list")
    d.keys("united states", per_char=0.3, clear=False)
    d.key("pgdn", settle=0.8, times=5)
    d.key("pgup", settle=0.8)
    # No Down here. frame-031 shows Down moves the highlight past United States to Uruguay.

    # THE LIST IS NOW SHOWING THE RIGHT ROWS, so click the row itself.
    #
    # Measured on frame-031, taken right after this same sequence in an earlier run:
    #
    #   Turkmenistan           y=470
    #   Turks & Caicos         y=490
    #   Tuvalu                 y=510
    #   Uganda                 y=530
    #   Ukraine                y=550
    #   United Arab Emirates   y=570
    #   United Kingdom         y=590
    #   United States          y=610     <- the one wanted
    #   Uruguay                y=631
    #
    # So the scrolling was never the problem - the row has been on screen all along, and what
    # failed was the commit: Down then Return then a Tab walk lands the highlight wherever the
    # focus ring happens to be, not necessarily on this row.
    #
    # The row is clicked directly, at x=800 (inside the list, right of the radio column) and
    # y=615 (the middle of United States' 20px line). Return and a Tab-Space follow, because a
    # click on this list moves the selection in earlier runs without committing it.
    # THE DOWN KEY WAS MOVING THE HIGHLIGHT OFF THE TARGET.
    #
    # frame-031, taken after exactly this sequence, reads:
    #
    #   highlighted: Uruguay            <- one row BELOW United States
    #   Continue:    grey
    #   mouse:       in the list, right of Turks & Caicos
    #
    # which says three things. The typing-and-paging already left the highlight ON United
    # States (Down then moved it to the next row, Uruguay). The highlight is focus, not
    # selection, so Continue stays grey either way. And the mouse is where the focus click
    # put it.
    #
    # So the sequence ends without Down, and commits with Return, which is what a focused
    # list row answers to. A direct click on the row follows as a second attempt, because a
    # click alone moves the selection here without committing it - the two together cover
    # both behaviours.
    # THE COMMIT IS Tab x6 THEN Space, AND ONLY AFTER THE HIGHLIGHT IS ON THE ROW.
    #
    # This is the sequence that actually worked, from the run of 3e5c80e:
    #
    #   05:42:06  clicking country 'United States' at (764, 615)
    #   05:42:37  highlight reached 'United States' after 1 steps
    #   05:42:45  after choosing -> Select Your Country or Region Turkiye ...
    #   05:43:34  after tab+space -> None          <- the pane changed
    #
    # Return on its own never commits this list, and a click moves the selection without
    # committing it either. What commits is walking the focus ring away from the list and
    # pressing Space on whatever it lands on - six Tabs from the list reaches Continue.
    #
    # The click goes to (764,615) exactly as that run did: x=764 is the label's own centre,
    # and it is what put the highlight in range for the Down that follows.
    # CLICK THE ROW, PRESS RETURN, AND STOP THERE.
    #
    # Re-reading the run that got past this pane (3e5c80e), the pane had ALREADY changed
    # before its Tab walk began:
    #
    #   05:42:06  clicking country 'United States' at (764, 615)
    #   05:42:37  highlight reached 'United States' after 1 steps
    #   05:42:45  after choosing -> Select Your Country or Region Turkiye ...   <- changed here
    #   05:43:22  no button after commit; tabbing to it        <- already on a different pane
    #   05:43:34  after tab+space -> None
    #
    # So the click and the Return were the commit; the Tab walk that followed was operating
    # on the NEXT pane, not this one. Adding Down and six Tabs here sent the focus ring
    # somewhere else entirely - the last run went black right after this step, which is what
    # happens when the ring reaches the wizard's power controls.
    #
    # Click, Return, done. If the pane does not move, the frames will say so and the next
    # step will click where it actually is.
    log("country list at the U section; clicking the row and pressing Return")
    d.click(764, 615, settle=1.5, why="United States row")
    d.key("ret", settle=2.0)
    d.shot(prefix, "country-after")


def transfer_pane(d, prefix):
    """'Set up as new', then Continue."""
    d.click(*SET_UP_AS_NEW, settle=1.5, why="Set up as new card")
    d.click(*PRIMARY, settle=2.0, why="transfer Continue")
    d.shot(prefix, "transfer-after")


def simple_continue(d, prefix, name, use_return_first=False):
    """A pane whose only control is Continue at the usual place."""
    d.settle(prefix, name + "-before")
    if use_return_first:
        d.key("ret", settle=1.5)
    d.click(*PRIMARY, settle=2.0, why=name)
    d.shot(prefix, name)


def transfer_pane_two_buttons(d, prefix):
    """The transfer pane that offers Not Now and a GREY Continue.

    Measured from the frame, bottom row y=785..830:

      x=1070..1194   Not Now    centre (1132,801)
      x=1214..1338   Continue   centre (1276,801)
      x=1194..1214   background - the gap between them

    Continue is disabled on this pane until one of the option cards is chosen, so pressing it
    does nothing at all; Not Now is the only control that moves the wizard. The previous version
    pressed Continue first and then aimed at Not Now, which is exactly backwards.
    """
    # THE BUTTONS ARE FOUND BY COLOUR, NOT BY OCR.
    #
    # On this pane both pills are (233,233,233) with dark lettering, and tesseract returns
    # nothing for either at psm 3, 6, 7, 11 or 13 - whole frame or 3x crop. A colour scan of
    # the button row finds them immediately:
    #
    #   x=1071..1193  -> Not Now     centre (1132, 796)
    #   x=1215..1337  -> Continue    centre (1276, 796)
    #
    # The leftmost is Not Now. Continue is disabled until a card is chosen, so it is never
    # the one to press on this pane.
    shot = d.shot(prefix, "transfer-buttons")
    pills = find_button_row(shot) if shot else []
    log("button row found: %s" % (pills,))
    # Drop anything right of the window (a stray pill-shaped region in the desktop behind).
    pills = [p for p in pills if p[0] < 1360]
    if len(pills) >= 2:
        target = pills[0]
        log("pressing the left button, Not Now, at %s" % (target,))
        d.click(target[0], target[1], settle=2.5, why="Not Now (leftmost pill)")
    else:
        log("button row not read; falling back to the measured Not Now")
        d.click(*CODE_NOT_NOW, settle=2.5, why="Not Now (measured)")
    d.shot(prefix, "transfer-notnow")


def account_pane(d, prefix, user, password):
    """Five fields, top to bottom, then Continue.

    Each field is clicked before typing because tab order is not guaranteed, and each is
    cleared first: a retry that appends produced 'dockerdocker' in an earlier run.
    """
    for point, value, label in (
            (FULL_NAME, user, "full name"),
            (ACCOUNT_NAME, user, "account name"),
            (PASSWORD, password, "password"),
            (VERIFY, password, "verify"),
            (HINT, "hint", "hint")):
        d.click(point[0], point[1], settle=0.8, why="field: " + label)
        d.keys(value, per_char=0.25)
    d.click(*PRIMARY, settle=2.5, why="account Continue")
    d.shot(prefix, "account-after")


def apple_account(d, prefix):
    """Set Up Later, and its confirmation sheet."""
    d.click(*NOT_NOW, settle=2.0, why="Set Up Later")
    d.click(*SHEET_OK, settle=2.0, why="confirmation sheet OK")
    d.shot(prefix, "apple-account-after")


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: drive-coords.py <monitor.sock.qmp> <prefix> [user] [password]")
    sock, prefix = sys.argv[1], sys.argv[2]
    user = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("SETUP_USERNAME", "docker")
    password = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("SETUP_PASSWORD", "admin")

    d = Driver(sock, os.path.dirname(os.path.abspath(prefix)) or ".")
    log("connected; driving by coordinates only")

    # WAIT FOR THE WIZARD TO EXIST, not for a guessed duration. The first run waited 45 seconds,
    # by which time this guest was still showing a black screen, and all fifty steps were sent
    # into it.
    if not d.wait_for_wizard(prefix):
        log("no wizard ever appeared; stopping rather than clicking at nothing")
        d.close()
        sys.exit(1)

    country_pane(d, prefix)
    transfer_pane(d, prefix)
    simple_continue(d, prefix, "languages")
    simple_continue(d, prefix, "accessibility", use_return_first=True)
    transfer_pane_two_buttons(d, prefix)
    simple_continue(d, prefix, "after-pairing")
    account_pane(d, prefix, user, password)
    apple_account(d, prefix)

    # Everything after the account is a Continue at the usual position, with two panes that
    # need a Return first. Written out rather than looped so the frames line up with the steps.
    for name in ("location-1", "location-2", "timezone", "analytics", "siri", "filevault",
                 "appearance", "finish-1", "finish-2", "finish-3", "finish-4"):
        simple_continue(d, prefix, name, use_return_first=True)

    d.shot(prefix, "desktop")
    log("sequence complete")
    d.close()


if __name__ == "__main__":
    main()
