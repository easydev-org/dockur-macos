#!/usr/bin/env python3
"""Describe the structure of a Setup Assistant frame: rows, controls, text areas.

    analyse-frame.py <frame.ppm>

WHY. Guessing which accent band is a control has now failed twice: one band was a
focus ring drawn as an outline, the next was a 72x76 icon. Reading the frame's
horizontal structure answers the question directly - the country list is a stack of
equal-height rows inside one column, and the pane's buttons sit on one row near the
bottom. Printing the row boundaries and the horizontal extent of non-background
pixels in each band makes that visible instead of inferred.
"""
import sys


def read_ppm(path):
    with open(path, "rb") as fh:
        if fh.readline().strip() != b"P6":
            raise SystemExit("not a P6 ppm")
        line = fh.readline()
        while line.startswith(b"#"):
            line = fh.readline()
        w, h = map(int, line.split())
        if int(fh.readline()) != 255:
            raise SystemExit("only 8-bit ppm")
        return w, h, fh.read()


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    w, h, data = read_ppm(sys.argv[1])

    def px(x, y):
        i = (y * w + x) * 3
        return data[i], data[i + 1], data[i + 2]

    # Setup Assistant here runs in dark mode: the window is a dark panel on a
    # blurred backdrop, so "different from the centre pixel" is useless - the centre
    # is inside the panel and so is most of what matters. What identifies a list is
    # TEXT: rows of letters produce many short luminance transitions along a scan
    # line. Counting transitions per row finds the text lines without needing to
    # know the panel colour at all.
    def transitions(y):
        n = 0
        prev = None
        for x in range(0, w, 2):
            r, g, b = px(x, y)
            lum = (r * 299 + g * 587 + b * 114) // 1000
            if prev is not None and abs(lum - prev) > 25:
                n += 1
            prev = lum
        return n

    print()
    print("%-6s %-8s %-6s %s" % ("y", "transitions", "extent", "note"))
    bands = []
    current = None
    for y in range(0, h):
        first = last = None
        count = transitions(y)
        if count > 6:
            for x in range(0, w, 4):
                r, g, b = px(x, y)
                lum = (r * 299 + g * 587 + b * 114) // 1000
                if lum > 120:
                    if first is None:
                        first = x
                    last = x
        else:
            count = 0
        if count:
            if current is None:
                current = [y, y, first, last, count]
            else:
                current[1] = y
                if first is not None:
                    current[2] = min(current[2], first)
                if last is not None:
                    current[3] = max(current[3], last)
                current[4] += count
        elif current is not None:
            bands.append(current)
            current = None
    if current is not None:
        bands.append(current)

    for y0, y1, x0, x1, n in bands:
        note = ""
        if 8 <= y1 - y0 <= 40:
            note = "text row"
        elif y1 - y0 > 40:
            note = "run of text rows"
        print("%-6s %-8s %-6s %s" % ("%d-%d" % (y0, y1), n, "%d-%d" % (x0, x1), note))


main()
