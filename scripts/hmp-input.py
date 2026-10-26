#!/usr/bin/env python3
"""Drive a QEMU guest through the HMP monitor: send keys and mouse events.

    hmp-input.py <monitor.sock> <command> [args...]

Commands:
    key <qcode>          press and release one key, e.g. "ret", "spc", "tab"
    keys <qcode>...      press several in order
    type <text>          type ASCII text (letters, digits, space, hyphen)
    click <x> <y>        move the pointer then left click
    move <x> <y>         move the pointer only

WHY. The baked disk reaches Setup Assistant and stops there, and the three
package-based attempts to suppress it all failed for timing or target reasons
(see the notes in launch.sh and firstboot.sh). Driving the running guest needs no
prediction about when macOS reads what: it acts on the screen that is actually up.

The monitor is the same channel that already produces reliable screenshots, so
the plumbing is known to work.
"""
import socket
import sys
import time

# QEMU key names for what Setup Assistant needs. HMP wants QKeyCode values
# (the same names qemu uses internally), not ASCII.
NAMED = {
    " ": "spc", "-": "minus", ".": "dot", ",": "comma", "/": "slash",
    "=": "equal", "\n": "ret", "\t": "tab",
}


def connect(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(20)
    s.connect(path)
    time.sleep(0.4)
    try:
        s.recv(8192)          # drain the banner / prompt
    except socket.timeout:
        pass
    return s


def hmp(sock, line):
    sock.sendall((line + "\n").encode())
    time.sleep(0.25)
    try:
        sock.recv(8192)
    except socket.timeout:
        pass


def key(sock, qcode, hold=0.08):
    hmp(sock, "sendkey " + qcode)
    time.sleep(hold)


def type_text(sock, text):
    for ch in text:
        if ch in NAMED:
            key(sock, NAMED[ch])
        elif ch.isupper():
            hmp(sock, "sendkey shift-" + ch.lower())
            time.sleep(0.08)
        elif ch.isalnum():
            key(sock, ch.lower())
        # anything else is skipped rather than guessed at


def click(sock, x, y):
    # mouse_move takes absolute coordinates when the display has a fixed size,
    # which it does here (1920x1080 set in the OpenCore config).
    hmp(sock, "mouse_move %d %d" % (x, y))
    time.sleep(0.2)
    hmp(sock, "mouse_button 1")
    time.sleep(0.1)
    hmp(sock, "mouse_button 0")
    time.sleep(0.3)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    sock_path, cmd = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]

    s = connect(sock_path)
    try:
        if cmd == "key":
            key(s, args[0])
        elif cmd == "keys":
            for k in args:
                key(s, k)
        elif cmd == "type":
            type_text(s, args[0] if args else "")
        elif cmd == "click":
            click(s, int(args[0]), int(args[1]))
        elif cmd == "move":
            hmp(s, "mouse_move %s %s" % (args[0], args[1]))
        else:
            sys.exit("unknown command: " + cmd)
    finally:
        s.close()
    print("sent " + cmd + " " + " ".join(args))


main()
