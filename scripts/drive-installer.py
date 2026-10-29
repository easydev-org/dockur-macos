#!/usr/bin/env python3
"""Drive the macOS recovery installer to the Setup Assistant.

WHY THIS EXISTS. The unattended path (MANUAL=N) patches rc.cdrom.sh inside the recovery
image, and that patch matches macOS 14's text only, so on 15 and 26 it refuses with
"Expected exactly one rc.cdrom.sh bootstrap, found 0". Upstream ships the same needle,
so this is not a version input anyone can flip - the automated branch has never been
exercised on those releases.

So the screens are driven by looking at them. Everything below was measured from
captured frames rather than guessed, and the three versions turn out to be nearly
identical:

  option rows        y = 244 / 327 / 420 / 512   (14: 241/334/430/526, 26: 238/334/430/525)
  Continue           (1142, 588)                 (14: 1137,591  26: 1129,600)
  install entry text  "Reinstall macOS Sonoma" / "Sequoia" / "Tahoe"
  browser entry text  "Safari" on 14 and 15, "Web Browser" on 26

THE ONE THING THAT MATTERED MOST. Disk Utility's sidebar lists VOLUMES, and a freshly
created 64 GiB disk has none - it simply is not in the list. Every attempt to find it
there failed because it was never on screen. The View menu's "Show All Devices" is what
puts physical disks into the sidebar, and that one click is the whole prerequisite for
erasing anything.

The sequence is therefore:

  recovery menu        select Disk Utility, press Continue
  Disk Utility         View -> Show All Devices
                       select the largest physical disk  (the 64 GiB one; the recovery
                       and boot images are read-only and much smaller)
                       Erase -> APFS -> confirm
                       quit
  recovery menu        select Reinstall macOS, press Continue
  installer panes      agree/continue until Setup Assistant appears

Screen state is detected from the presence of structural elements, never from a label
that also appears in prose. That distinction cost several rounds: the recovery menu
contains the sentence "Disk Utility - Repair or erase a disk using Disk Utility", so any
test for the words "disk utility" or "erase" was true before the tool had even opened.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Reuse Screen from drive-ocr.py: it already handles screendump path mapping (qemu
# resolves paths inside its container, where frames is mounted at /frames), tesseract
# TSV parsing with a confidence floor, and shift chords for capitals.
from importlib.machinery import SourceFileLoader

import qmp as qmpmod

_drive_ocr = SourceFileLoader("drive_ocr", os.path.join(HERE, "drive-ocr.py")).load_module()
Screen = _drive_ocr.Screen

W, H = 1920, 1080

# The recovery menu's own Continue. Every entry there is opened by selecting it and then
# pressing this; clicking the entry alone only highlights it.
CONTINUE_MENU = (1142, 588)

# Row centres of the four recovery entries, averaged across 14, 15 and 26 frames.
ROW_Y = (244, 330, 425, 520)
ROW_X = 958


def words_of(screen, prefix, tag):
    return screen.text(screen.grab("%s-%s" % (prefix, tag)))


def joined(words):
    return " ".join(w[0] for w in words).lower()


def row(words, index, *needles):
    """Position of entry `index`, if its label is where that row should be.

    Two conditions, and both are needed. The label has to contain one of the needles -
    the install entry reads "Reinstall macOS Sonoma/Sequoia/Tahoe" and the browser reads
    "Safari" or "Web Browser" - and it has to sit near the row's own y and near x=ROW_X.
    Without the positional half, the description text beside each title matches first.
    """
    y_expected = ROW_Y[index]
    best = None
    for word, x, y, _w, _h in words:
        low = word.lower()
        if not any(n in low for n in needles):
            continue
        if abs(y - y_expected) > 40 or abs(x - ROW_X) > 80:
            continue
        if best is None or abs(y - y_expected) < abs(best[1] - y_expected):
            best = (x, y)
    return best


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    qmp_socket, prefix = sys.argv[1], sys.argv[2]
    log_path = prefix + "-installer.log"

    def log(msg):
        line = "[inst %s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    # QMP, with the .qmp suffix. drive-in-container.sh creates two sockets and only the
    # .qmp one speaks JSON; connecting to the plain monitor gives a text prompt whose
    # first character is not '{', which surfaces as a JSONDecodeError that looks like an
    # empty response and sent several rounds chasing qemu instead of the path.
    qmp_path = qmp_socket if qmp_socket.endswith(".qmp") else qmp_socket + ".qmp"
    screen = None
    for attempt in range(1, 31):
        try:
            screen = Screen(qmp_path)
            log("qmp connected on attempt %d" % attempt)
            break
        except Exception as exc:
            log("qmp not ready (%d): %s" % (attempt, type(exc).__name__))
            time.sleep(10)
    if screen is None:
        log("qmp never became ready")
        return 1

    # The recovery boot shows nothing for minutes. Wait for words rather than for a
    # fixed delay.
    deadline = time.time() + 1500
    words = []
    while time.time() < deadline:
        words = words_of(screen, prefix, "boot")
        if len(words) > 3:
            log("readable: %s" % joined(words)[:140])
            break
        time.sleep(20)
    if not words:
        log("nothing readable after 25 minutes")
        screen.close()
        return 1

    erased = False
    installing = False
    in_view_menu = False
    erase_clicked = False

    for step in range(1, 200):
        words = words_of(screen, prefix, "s%02d" % step)
        text = joined(words)
        log("step %d: %s" % (step, text[:150] or "(nothing)"))

        if not text:
            time.sleep(5)
            continue

        if "country or region" in text or "select your country" in text:
            log("SETUP ASSISTANT REACHED at step %d" % step)
            screen.close()
            return 0

        # --- Disk Utility -------------------------------------------------------
        # Identified by its toolbar, which the recovery menu does not have. Three of
        # Volume/Partition/Restore/Unmount/First Aid together is unambiguous; the menu's
        # prose never puts those words on one line.
        toolbar = sum(1 for w in text.split()
                      if w in ("volume", "partition", "restore", "unmount", "first", "ald"))
        in_du = toolbar >= 3 or "physical volume" in text

        if in_du:
            if not erased:
                if not erase_clicked:
                    # Show All Devices first. This is the step that makes the target
                    # exist: the default view lists volumes, and the empty 64 GiB disk
                    # has none, so it is absent until this is switched.
                    if not in_view_menu:
                        view = None
                        for word, x, y, _w, _h in words:
                            if word.strip().lower() == "view" and y < 340:
                                view = (x, y)
                                break
                        if view is not None:
                            log("opening the View menu at %s" % (view,))
                            screen.click(view[0], view[1])
                            in_view_menu = True
                            time.sleep(3)
                            continue
                        log("View control not found in this frame")

                    # "Show All Devices" sits at the bottom of that menu, below
                    # "Show Only Volumes".
                    log("choosing Show All Devices")
                    screen.click(210, 190)
                    time.sleep(4)
                    in_view_menu = False
                    erase_clicked = True
                    continue

                # Now the sidebar contains physical disks. Select the largest one: the
                # recovery image and the OpenCore disk are read-only and far smaller,
                # and the target is the only one measured in tens of gigabytes.
                log("selecting the largest device in the sidebar")
                screen.click(540, 340)
                time.sleep(3)
                probe = words_of(screen, prefix, "probe")
                right = " ".join(w[0] for w in probe if w[1] > 900)
                log("right panel: %s" % right[:160])

                erase = None
                for word, x, y, _w, _h in words:
                    if word.strip().lower().startswith("era") and y < 400:
                        erase = (x, y)
                        break
                if erase is not None:
                    log("clicking Erase at %s" % (erase,))
                    screen.click(erase[0], erase[1])
                    time.sleep(10)
                continue

            # The erase sheet. Its own Erase commits; it is the lowest such control.
            cands = [(w[1], w[2]) for w in words
                     if w[0].strip().lower().startswith("era") and w[2] > 380]
            if cands:
                target = cands[-1]
                log("confirming Erase at %s" % (target,))
                screen.click(target[0], target[1])
                time.sleep(30)
                erased = True
                continue

            log("leaving Disk Utility")
            qmpmod.key_combo(screen.q, "meta_l", "q")
            time.sleep(12)
            continue

        # --- recovery menu ------------------------------------------------------
        if not erased:
            du = row(words, 3, "disk utility")
            if du is not None:
                log("opening Disk Utility")
                screen.click(du[0], du[1])
                time.sleep(2)
                screen.click(CONTINUE_MENU[0], CONTINUE_MENU[1])
                time.sleep(15)
                continue
            log("waiting for the recovery menu")
            time.sleep(5)
            continue

        if not installing:
            inst = row(words, 1, "reinstall")
            if inst is not None:
                log("starting the reinstall")
                screen.click(inst[0], inst[1])
                time.sleep(2)
                screen.click(CONTINUE_MENU[0], CONTINUE_MENU[1])
                installing = True
                time.sleep(25)
                continue

        # Installer panes: agree, continue, and the occasional plain Continue.
        for needle in ("agree", "continue", "continue with", "install"):
            hit = None
            for word, x, y, _w, _h in words:
                if needle in word.lower() and y > 300:
                    hit = (x, y)
                    break
            if hit is not None:
                log("advancing at %s (%s)" % (hit, needle))
                screen.click(hit[0], hit[1])
                time.sleep(15)
                break
        else:
            screen.key("ret")
            time.sleep(12)

    log("did not reach Setup Assistant")
    screen.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
