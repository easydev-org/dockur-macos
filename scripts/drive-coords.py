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
CODE_NOT_NOW = (1132, 805)
# Back, on panes that have one.
BACK = (1218, 810)
# The 'Unable to Pair Computers' alert: single blue OK spanning x=843..1076, y=610..643.
ALERT_OK = (959, 626)

# Country list. Typing jumps by first letter; the list then sits near T, and five Page Downs
# land it on the U section with United States still on screen.
COUNTRY_LIST = (900, 500)

# 'Set up as new' - the fourth card on the transfer pane. Measured (869,671).
SET_UP_AS_NEW = (869, 671)

# 'Are you sure you want to skip sign in?' sheet - Skip is the right-hand of its pair.
SHEET_OK = (1026, 634)
# The account form, top to bottom.
FULL_NAME = (942, 455)
ACCOUNT_NAME = (942, 486)
PASSWORD = (896, 528)
VERIFY = (1057, 528)
HINT = (978, 562)


def log(msg):
    print("[xy %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


class Driver:
    def __init__(self, sock, screen_w=1920, screen_h=1080):
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
        """A frame for the record - never read, only kept, so a failure can be looked at later."""
        self.serial += 1
        path = "/frames/%s-%03d-%s.ppm" % (prefix, self.serial, name)
        try:
            self.q.execute("screendump", filename=path)
        except Exception as exc:
            log("screendump failed at %s: %s" % (name, exc))
        time.sleep(1.6)

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
    d.shot(prefix, "country")
    d.click(*COUNTRY_LIST, settle=0.8, why="focus the list")
    d.keys("united states", per_char=0.3, clear=False)
    d.key("pgdn", settle=0.8, times=5)
    d.key("pgup", settle=0.8)
    d.key("down", settle=0.8)
    # Return does not commit on this release; Tab + Space does.
    d.key("ret", settle=0.8, times=2)
    d.key("tab", settle=0.6, times=6)
    d.key("spc", settle=2.0)
    d.shot(prefix, "country-after")


def transfer_pane(d, prefix):
    """'Set up as new', then Continue."""
    d.click(*SET_UP_AS_NEW, settle=1.5, why="Set up as new card")
    d.click(*PRIMARY, settle=2.0, why="transfer Continue")
    d.shot(prefix, "transfer-after")


def simple_continue(d, prefix, name, use_return_first=False):
    """A pane whose only control is Continue at the usual place."""
    if use_return_first:
        d.key("ret", settle=1.5)
    d.click(*PRIMARY, settle=2.0, why=name)
    d.shot(prefix, name)


def privacy_pane(d, prefix):
    """Continue, then Not Now on the pairing pane it opens - within two seconds.

    The pairing pane lives for about two seconds before the failure alert replaces it, which is
    why it was never caught by OCR: by the time a frame had been taken and read, the alert was
    up. Here the click after Continue is sent immediately, with no read in between.
    """
    d.click(*PRIMARY, settle=1.2, why="privacy Continue")
    d.click(*CODE_NOT_NOW, settle=1.5, why="Not Now on the pairing pane, immediately")
    # If the alert still appeared, dismiss it and press Not Now again on what is underneath.
    d.click(*ALERT_OK, settle=1.5, why="alert OK if it appeared")
    d.click(*CODE_NOT_NOW, settle=2.0, why="Not Now again")
    d.shot(prefix, "privacy-after")


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

    d = Driver(sock)
    log("connected; driving by coordinates only")

    # The guest is still booting into the wizard for the first half-minute or so. Nothing is
    # read here, so this is a plain wait rather than a poll.
    log("waiting 45s for the wizard to appear")
    time.sleep(45)

    country_pane(d, prefix)
    transfer_pane(d, prefix)
    simple_continue(d, prefix, "languages")
    simple_continue(d, prefix, "accessibility", use_return_first=True)
    privacy_pane(d, prefix)
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
