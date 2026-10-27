#!/usr/bin/env python3
"""Walk macOS Setup Assistant to the desktop by clicking what the screen shows.

    setup-assistant-drive.py <qmp-socket> <frame-prefix>

WHY IT LOOKS AT THE FRAME. Every earlier version clicked coordinates I had guessed,
and the guesses were wrong in a way that took a screenshot to see: on the country
pane the highlighted row sits at (598,175) while the driver was clicking (760,430),
so nothing was ever selected, Continue stayed grey, and twenty minutes of clicking
changed nothing. The signature barely moved either - 380 to 410 distinct colours -
which the old code read as "the pane advanced" and used to justify filling the
account form over and over.

So this version asks the framebuffer where things are. Setup Assistant paints the
selected row and a focused default button in the accent colour; finding that colour
locates the thing to click without guessing. Progress is measured by a change of
more than a dozen colours, since the clock and pointer move the count by a few.

WHY QMP. HMP's mouse_move carries relative deltas and the guest has a usb-tablet -
an absolute device - so 64 HMP clicks left the pointer in the top-left corner and
all 64 frames byte-identical. QMP's input-send-event takes absolute axes.

WHY DRIVING AT ALL. There is no marker shortcut: dockur's README says only
dockur/windows installs automatically, OSX-KVM lists "automate the macOS
installation via OpenCV" as a not-so-crazy idea, and the unattended code in this
fork calls itself "the unattended-install proof". A person at the console does it in
two minutes; this does that once, and the disk it leaves behind is the deliverable.
"""
import os
import subprocess
import sys
import time

import qmp as qmpmod

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1920, 1080


class Monitor:
    """All input goes through QMP; screenshots and clicks share one connection."""

    def __init__(self, qmp_socket):
        self.q = qmpmod.QMP(qmp_socket)

    def shot(self, path):
        reply = self.q.execute("screendump", filename=path)
        time.sleep(1.5)
        if "error" in reply:
            raise RuntimeError("screendump failed: %s" % reply["error"])
        return path if os.path.exists(path) else None

    def click(self, x, y):
        qmpmod.click(self.q, x, y)

    def key(self, ch):
        qmpmod.key(self.q, ch)

    def close(self):
        self.q.close()


def signature(mon, path):
    """Return (distinct, dominant) for a fresh frame, via ppm2png's own report."""
    ppm = mon.shot(path)
    if not ppm:
        return None
    try:
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "ppm2png.py"), ppm, path + ".png"],
            capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return None
    distinct = dominant = None
    for token in out.split():
        if token.startswith("distinct="):
            try:
                distinct = int(token.split("=", 1)[1])
            except ValueError:
                pass
        elif token.startswith("dominant="):
            try:
                dominant = round(float(token.split("=", 1)[1].rstrip("%")), 1)
            except ValueError:
                pass
    return (distinct, dominant)


def find_accent_bands(ppm_path, x_lo=0, x_hi=W):
    """Locate bands of accent-coloured pixels, widest first.

    Returns [(y0, y1, x0, x1, pixels)]. Setup Assistant paints the selected row and
    the default button in macOS accent blue; the window background is neutral grey
    and never matches, so this finds clickable things from the frame itself.
    """
    try:
        with open(ppm_path, "rb") as fh:
            if fh.readline().strip() != b"P6":
                return []
            line = fh.readline()
            while line.startswith(b"#"):
                line = fh.readline()
            w, h = map(int, line.split())
            if int(fh.readline()) != 255:
                return []
            data = fh.read()
    except Exception:
        return []

    def is_accent(r, g, b):
        return b > 140 and b > r + 60 and 60 < g < b and r < 140

    bands = []
    current = None
    for y in range(0, h, 2):
        hits = []
        base = y * w * 3
        for x in range(max(0, x_lo), min(x_hi, w), 4):
            i = base + x * 3
            if is_accent(data[i], data[i + 1], data[i + 2]):
                hits.append(x)
        if hits:
            if current is None:
                current = [y, y, min(hits), max(hits), len(hits)]
            else:
                current[1] = y
                current[2] = min(current[2], min(hits))
                current[3] = max(current[3], max(hits))
                current[4] += len(hits)
        elif current is not None:
            bands.append(tuple(current))
            current = None
    if current is not None:
        bands.append(tuple(current))
    bands.sort(key=lambda b: -b[4])
    return bands


def is_desktop(sig):
    """A full UI rather than a wizard pane, by colour spread.

    Measured on this guest: wizard panes 380-410 colours at ~63% dominant; real
    full-screen UI 34436 at 22.9% and 34684 at 19.2%. Two orders of magnitude
    apart, so the threshold is not delicate.
    """
    if not sig:
        return False
    distinct, dominant = sig
    return (distinct is not None and dominant is not None
            and distinct > 5000 and dominant < 50.0)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    mon_path, prefix = sys.argv[1], sys.argv[2]
    log_path = prefix + "-drive.log"

    def log(msg):
        line = "[drive %s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        try:
            with open(log_path, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    mon = Monitor(mon_path + ".qmp")
    log("qmp connected via " + mon_path + ".qmp")

    def locate_and_click(label, wait=10):
        """Screenshot, find the widest accent band, click its centre."""
        ppm = mon.shot(prefix + "-" + label)
        if not ppm:
            log("%s: no frame" % label)
            return None
        bands = find_accent_bands(ppm, 100, W - 100)
        if not bands:
            log("%s: nothing accent-coloured on screen" % label)
            return None
        y0, y1, x0, x1, n = bands[0]
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        log("%s: accent band y=%d..%d x=%d..%d (%d px) -> click (%d,%d)"
            % (label, y0, y1, x0, x1, n, cx, cy))
        mon.click(cx, cy)
        time.sleep(wait)
        return (cx, cy)

    def step(label, tries=3):
        """Make progress on the current pane; return the new signature or None."""
        before = signature(mon, prefix + "-" + label + "-before")
        log("%s: before=%s" % (label, before))
        for attempt in range(1, tries + 1):
            locate_and_click("%s-locate%d" % (label, attempt))
            after = signature(mon, prefix + "-" + label + "-try%d" % attempt)
            log("%s attempt %d: after=%s" % (label, attempt, after))
            # More than a dozen colours means the pane really changed. The clock
            # ticks and the pointer moves by a handful, and treating that as
            # progress is what made the previous run loop for twenty minutes.
            if before and after and abs(after[0] - before[0]) > 12:
                return after
            before = after
        return None

    def fill_account():
        """Type the account form, clicking each field before typing into it."""
        fields = [
            ("full name", 400, "Sandbox"),
            ("account name", 470, "sandbox"),
            ("password", 540, "sandbox"),
            ("verify", 610, "sandbox"),
            ("hint", 680, "hint"),
        ]
        for label, y, text in fields:
            log("account: " + label)
            mon.click(W // 2, y)
            time.sleep(0.7)
            for ch in text:
                mon.key(ch)
                time.sleep(0.12)
            time.sleep(0.4)
        log("account form filled")

    # Let the wizard finish drawing; a click into a half-drawn window is lost.
    time.sleep(30)
    log("initial frame: %s" % (signature(mon, prefix + "-000"),))

    for i in range(1, 20):
        sig = step("pane%d" % i)
        if sig is None:
            log("pane %d did not move; trying the account form" % i)
            fill_account()
            sig = step("pane%d-after" % i)
        if is_desktop(sig):
            log("DESKTOP REACHED at pane %d: %s" % (i, sig))
            mon.shot(prefix + "-desktop")
            break
        log("pane %d now at %s" % (i, sig))

    log("done; the caller inspects the frames")
    mon.close()


if __name__ == "__main__":
    main()
