#!/usr/bin/env python3
"""Report where the selected row of a list sits in a framebuffer dump.

    find-list-row.py <file.ppm> [x-lo x-hi]

WHY. The driver kept clicking at a guessed coordinate and Continue stayed grey,
because the click landed between rows or outside the list. Setup Assistant's
country list highlights the chosen row in the system accent colour, so the honest
way to find a row is to look for that colour rather than to guess.

Prints, for every row of pixels that contains accent-coloured pixels, the row's
y coordinate and the horizontal extent of the highlight. The middle of the widest
such band is where a click belongs.
"""
import sys


def read_ppm(path):
    with open(path, "rb") as fh:
        magic = fh.readline().strip()
        if magic != b"P6":
            raise SystemExit("not a P6 ppm: " + repr(magic))
        line = fh.readline()
        while line.startswith(b"#"):
            line = fh.readline()
        w, h = map(int, line.split())
        maxv = int(fh.readline())
        if maxv != 255:
            raise SystemExit("only 8-bit ppm supported")
        data = fh.read()
    return w, h, data


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    w, h, data = read_ppm(sys.argv[1])
    x_lo = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    x_hi = int(sys.argv[3]) if len(sys.argv) > 3 else w

    # macOS highlight blue is around (0,122,255) in light mode and (10,132,255) in
    # dark; both are strongly blue-dominant with a real green component and almost
    # no red. That signature is distinct from the grey window background.
    def is_accent(r, g, b):
        return b > 140 and b > r + 60 and g > 60 and g < b and r < 140

    bands = []
    current = None
    for y in range(0, h, 2):
        hits = []
        base = y * w * 3
        for x in range(x_lo, x_hi, 4):
            i = base + x * 3
            r, g, b = data[i], data[i + 1], data[i + 2]
            if is_accent(r, g, b):
                hits.append(x)
        if hits:
            if current is None:
                current = [y, y, min(hits), max(hits), len(hits)]
            else:
                current[1] = y
                current[2] = min(current[2], min(hits))
                current[3] = max(current[3], max(hits))
                current[4] += len(hits)
        else:
            if current is not None:
                bands.append(current)
                current = None
    if current is not None:
        bands.append(current)

    if not bands:
        print("no accent-coloured band found between x=%d and %d" % (x_lo, x_hi))
        return

    bands.sort(key=lambda b: -b[4])
    print("accent bands, widest first:")
    for y0, y1, x0, x1, n in bands[:6]:
        print("  y=%4d..%-4d x=%4d..%-4d pixels=%d -> click at (%d,%d)"
              % (y0, y1, x0, x1, n, (x0 + x1) // 2, (y0 + y1) // 2))


main()
