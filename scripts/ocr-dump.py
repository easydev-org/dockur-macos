#!/usr/bin/env python3
"""Print every word tesseract finds in a frame, with its position.

    ocr-dump.py <frame.ppm>

Sorted top to bottom, so the pane's layout reads as text: heading first, body next,
the button row last. Used to see what OCR actually returns for a pane that the
driver cannot get past, rather than inferring it from the driver's own decisions.
"""
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    proc = subprocess.run(["tesseract", sys.argv[1], "stdout", "--psm", "3", "tsv"],
                          capture_output=True, text=True)
    rows = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            conf = float(parts[10])
            x, y, w, h = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
        except ValueError:
            continue
        text = parts[11].strip()
        if text and conf > 40:
            rows.append((y, x, text))
    rows.sort()
    for y, x, text in rows:
        print("%4d %4d  %s" % (y, x, text))


main()
