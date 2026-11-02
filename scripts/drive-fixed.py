#!/usr/bin/env python3
"""Drive macOS 26's Setup Assistant as a fixed sequence of clicks, keys and scrolls.

NO LOOKING. This script never takes a screenshot, never reads a pixel, never asks the screen a
question. Every action is at a recorded position after a recorded delay. The flow is the same on
every boot because the wizard's geometry is: a fixed 1920x1080 window drawing its controls in the
same places, with releases differing only in which panes appear.

WHY FIXED AND NOT READING. The reading versions worked on 14 and 15 and failed on 26 for reasons
of timing and contrast rather than logic:

  the pane right after Data & Privacy offers Not Now and a greyed Continue, and Continue starts a
  pairing handshake that waits thirteen seconds and fails. The Not Now that avoids it exists for
  about two seconds, so no screenshot-and-decide loop can act inside that window.

  both pills are grey-on-grey or white-on-white, and tesseract returns nothing for either at any
  page-segmentation mode - the labels are not there to be read.

  every "look first" added a way to be wrong: a Tab walk that reached the power controls and shut
  the machine down, a Down that moved the highlight off the target, a colour scan that accepted
  grey and missed the white enabled button.

Every position below is measured from a captured frame.

Usage: drive-fixed.py <monitor.sock> [user] [password]
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qmp as qmpmod


# ---------------------------------------------------------------- measured positions

# The wizard's forward button, in the same place on every pane of every release.
CONTINUE = (1306, 810)

# Country pane: empty part of the list, for keyboard focus; then the United States row
# (label centre x=764, y the middle of its 20px line).
COUNTRY_LIST = (900, 500)
UNITED_STATES = (764, 615)

# Transfer pane's four cards, measured from frame-062:
#   (1) From a Mac, Time Machine, or startup disk   y=642
#   (2) From a Windows PC                          y=665
#   (3) Set up with iPhone or iPad                 y=689
#   (4) Set up as new                              y=714   <- the one wanted
# Left-aligned at x=743, where the radio marks sit.
SET_UP_AS_NEW = (743, 714)

# The two-button pane. Both pills measured at y=792:
#   x=1071..1193  Not Now   centre (1132, 796)
#   x=1215..1337  Continue  centre (1276, 796)
# Continue is disabled there, so Not Now is the only control that moves the wizard.
NOT_NOW_LEFT = (1132, 796)

# Three-button panes put Not Now leftmost at x=604.
NOT_NOW = (604, 810)

# The 'Are you sure you want to skip sign in?' sheet - Skip is the right-hand of its pair.
SHEET_SKIP = (1026, 634)

# The account form, top to bottom.
FULL_NAME = (942, 455)
ACCOUNT_NAME = (942, 486)
PASSWORD = (896, 528)
VERIFY = (1057, 528)
HINT = (978, 562)


def log(msg):
    print("[fx %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


class Fixed:
    def __init__(self, sock):
        # The .qmp suffix is required: drive-in-container.sh creates monitor.sock (HMP, plain
        # text) and monitor.sock.qmp (QMP, JSON), and parsing the first as JSON fails at once.
        if not sock.endswith(".qmp"):
            sock = sock + ".qmp"
        self.q = qmpmod.QMP(sock)
        self.n = 0

    def wait(self, seconds, why=""):
        log("wait %ds%s" % (seconds, (" - " + why) if why else ""))
        time.sleep(seconds)

    def click(self, xy, why="", settle=1.5):
        self.n += 1
        log("#%d click (%d,%d) %s" % (self.n, xy[0], xy[1], why))
        qmpmod.click(self.q, xy[0], xy[1])
        time.sleep(settle)

    def key(self, name, times=1, settle=0.5):
        self.n += 1
        log("#%d key %s x%d" % (self.n, name, times))
        for _ in range(times):
            qmpmod.key(self.q, name)
            time.sleep(settle)

    def keys(self, text, per_char=0.3):
        """Type one character at a time.

        0.3s per character: at 0.15 the guest dropped them, which on the country list showed up
        as the jump landing on T instead of U.
        """
        self.n += 1
        log("#%d type %r" % (self.n, text))
        for ch in text:
            qmpmod.key(self.q, "spc" if ch == " " else ch)
            time.sleep(per_char)

    def clear_field(self):
        """Empty the focused field before typing into it.

        Appending produced 'dockerdocker' in an earlier run: Cmd-A and one backspace does not
        always take on these fields, so the sequence is Cmd-A, backspace, twice, then a run.
        """
        self.n += 1
        log("#%d clear field" % self.n)
        for _ in range(2):
            qmpmod.key_combo(self.q, "meta_l", "a")
            time.sleep(0.5)
            qmpmod.key(self.q, "backspace")
            time.sleep(0.4)
        for _ in range(24):
            qmpmod.key(self.q, "backspace")
            time.sleep(0.04)
        time.sleep(0.6)

    def scroll(self, notches, x=960, y=550):
        """Positive notches scroll down, per qmp.scroll's docstring."""
        self.n += 1
        log("#%d scroll %+d at (%d,%d)" % (self.n, notches, x, y))
        qmpmod.scroll(self.q, x, y, notches)
        time.sleep(1.0)

    def close(self):
        try:
            self.q.close()
        except Exception:
            pass

# ---------------------------------------------------------------- the sequence
#
# Fixed order, fixed delays. The pane stack below is what 26 shows, assembled from the capture
# logs of the runs that got furthest.


def pick_country(d):
    """United States: focus the list, type, page to the U section, click the row, Return.

    Measured behaviour on this release: a click in the list gives it focus but selects nothing;
    typing jumps by first letter and comes to rest near T; five Page Downs put the U section on
    screen; and the row is committed by clicking it and pressing Return. Return alone does
    nothing, and a Tab walk from here reaches the wizard power controls - which shut the machine
    down twice before this was understood.
    """
    d.wait(30, "let the country pane finish drawing")
    d.click(COUNTRY_LIST, "focus the country list")
    d.keys("united states")
    d.key("pgdn", times=5, settle=0.8)
    d.key("pgup", settle=0.8)
    d.click(UNITED_STATES, "the United States row")
    d.key("ret", settle=2.5)


def set_up_as_new(d):
    """The transfer pane: choose the fourth card, then Continue."""
    d.wait(6, "transfer pane settles")
    d.click(SET_UP_AS_NEW, "card 4, Set up as new")
    d.click(CONTINUE, "transfer Continue")


def languages(d):
    d.wait(4)
    d.click(CONTINUE, "languages Continue")


def accessibility(d):
    d.wait(4)
    d.key("ret", settle=1.5)
    d.click(CONTINUE, "accessibility Continue")


def privacy_then_not_now(d):
    """Data & Privacy, then the pane it opens: Not Now, never Continue.

    Continue there leads to a pairing handshake with a Mac that does not exist - thirteen
    seconds and a failure alert that covers the way out. Not Now is at (1132,796) on the pane
    that appears, and is pressed straight after Continue so it lands while the pane is up.
    """
    d.wait(4)
    d.click(CONTINUE, "privacy Continue")
    d.click(NOT_NOW_LEFT, "Not Now on the pairing pane", settle=2.5)
    d.click(NOT_NOW_LEFT, "Not Now again, in case the first was early", settle=2.5)


def account(d, user, password):
    """The five-field form, then Continue."""
    d.wait(6, "account pane settles")
    for xy, value, label in (
            (FULL_NAME, user, "full name"),
            (ACCOUNT_NAME, user, "account name"),
            (PASSWORD, password, "password"),
            (VERIFY, password, "verify"),
            (HINT, "hint", "hint")):
        d.click(xy, label, settle=0.8)
        d.clear_field()
        d.keys(value, per_char=0.25)
    d.click(CONTINUE, "account Continue", settle=3.0)


def apple_account(d):
    """Set Up Later, then the confirmation sheet."""
    d.wait(6, "apple account pane settles")
    d.click(NOT_NOW, "Set Up Later")
    d.click(SHEET_SKIP, "sheet: Skip", settle=2.5)


def tail(d):
    """After the account: Continue at the usual place, with a Return before each.

    Location services, time zone, analytics, Siri, FileVault, appearance and the finishing panes
    all take the same action. The window is fixed, so this is a count, not a loop over anything
    observed.
    """
    for i in range(12):
        d.wait(5, "pane %d settles" % (i + 1))
        d.key("ret", settle=2.0)
        d.click(CONTINUE, "pane %d Continue" % (i + 1), settle=2.5)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: drive-fixed.py <monitor.sock> [user] [password]")
    sock = sys.argv[1]
    user = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SETUP_USERNAME", "docker")
    password = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("SETUP_PASSWORD", "admin")

    d = Fixed(sock)
    log("connected; fixed sequence, no reads")

    # The guest takes a couple of minutes to reach the wizard. A wait, not a poll: the measured
    # duration, and the sequence starts after it.
    d.wait(120, "guest boots into Setup Assistant")

    pick_country(d)
    set_up_as_new(d)
    languages(d)
    accessibility(d)
    privacy_then_not_now(d)
    account(d, user, password)
    apple_account(d)
    tail(d)

    log("sequence complete after %d actions" % d.n)
    d.close()


if __name__ == "__main__":
    main()

