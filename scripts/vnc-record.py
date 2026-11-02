#!/usr/bin/env python3
"""Record a qemu VNC display to an mp4, without ffmpeg and without a VNC library.

WHY. Two things do not exist in this environment:

  ffmpeg from Ubuntu is built without --enable-libvncclient, so the obvious
  "-f vnc -i host:port" fails with "Unknown input format: vnc"

  there is no X server, so x11grab is not available either

VNC over RFB is a small protocol, so this speaks it directly: connect, complete the
handshake, ask for RAW frames at a fixed interval, and write each as a PPM. ffmpeg is then
used only to mux the PPM sequence into video, which needs no network input support.

The recorder runs in its own thread while the driver works, so the guest is never paused.

Usage: vnc-record.py <host> <port> <outdir> [--fps N] [--seconds N]
"""

import argparse
import os
import socket
import struct
import sys
import threading
import time


def log(msg):
    print("[vnc %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


class RFB:
    """Just enough of RFB 3.8 to receive RAW framebuffer updates."""

    def __init__(self, host, port, timeout=120):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.width = 0
        self.height = 0

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("the server closed the connection")
            buf += chunk
        return buf

    def handshake(self):
        version = self._recv(12)
        if not version.startswith(b"RFB "):
            raise ConnectionError("not an RFB server: %r" % version[:12])
        self.sock.sendall(b"RFB 003.008")
        count = self._recv(1)[0]
        if count == 0:
            raise ConnectionError("the server offered no security types")
        kinds = self._recv(count)
        if 1 not in kinds:
            raise ConnectionError("no unauthenticated access; offered %r" % list(kinds))
        self.sock.sendall(bytes([1]))
        self._recv(4)
        self.sock.sendall(bytes([1]))
        header = self._recv(24)
        self.width, self.height = struct.unpack("!HH", header[:4])
        log("desktop %dx%d" % (self.width, self.height))
        self.set_pixel_format()
        self.set_encodings()


    def set_pixel_format(self):
        """Choose the byte layout. RFB has no default, and without this the server

        sends nothing. 32bpp, depth 24, little-endian, true colour, with the usual
        red/green/blue shifts - the same layout qemu uses for its own console."""
        body = struct.pack("!BBBBHHHBBBBBB",
                           32, 24, 0, 1,
                           255, 255, 255,
                           16, 8, 0,
                           0, 0, 0)
        self.sock.sendall(bytes([0]) + b"\x00\x00\x00" + body)

    def set_encodings(self):
        """Allow only RAW (0). Left empty, the server has nothing it may send;

        asking for RAW also means read_frame gets uncompressed pixels it can copy
        straight into a PPM without a decoder."""
        self.sock.sendall(struct.pack("!BBH", 2, 0, 1) + struct.pack("!i", 0))
    def request_update(self, incremental=0):
        self.sock.sendall(
            struct.pack("!BBHHHH", 3, incremental, 0, 0, self.width, self.height))

    def read_frame(self):
        """Read messages until a FramebufferUpdate carrying RAW pixels arrives."""
        while True:
            kind = self._recv(1)[0]
            if kind == 0:
                self._recv(1)
                count = struct.unpack("!H", self._recv(2))[0]
                rects = []
                for _ in range(count):
                    x, y, w, h, enc = struct.unpack("!HHHHi", self._recv(12))
                    if enc != 0:
                        return None
                    rects.append((x, y, w, h, self._recv(w * h * 4)))
                return rects
            if kind == 1:
                self._recv(1)
                count = struct.unpack("!H", self._recv(2))[0]
                for _ in range(count):
                    self._recv(8)
                    self._recv(struct.unpack("!I", self._recv(4))[0])
                continue
            if kind == 2:
                continue
            if kind == 3:
                self._recv(3)
                self._recv(struct.unpack("!I", self._recv(4))[0])
                continue
            raise ConnectionError("unexpected server message %d" % kind)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
#!/usr/bin/env python3
"""Record a qemu VNC display to an mp4, without ffmpeg and without a VNC library.

WHY. Two things do not exist in this environment:

  ffmpeg from Ubuntu is built without --enable-libvncclient, so the obvious
  "-f vnc -i host:port" fails with "Unknown input format: vnc"

  there is no X server, so x11grab is not available either

VNC over RFB is a small protocol, so this speaks it directly: connect, complete the
handshake, ask for RAW frames at a fixed interval, and write each as a PPM. ffmpeg is then
used only to mux the PPM sequence into video, which needs no network input support.

The recorder runs in its own thread while the driver works, so the guest is never paused.

Usage: vnc-record.py <host> <port> <outdir> [--fps N] [--seconds N]
"""

import argparse
import os
import socket
import struct
import sys
import threading
import time




def write_ppm(path, width, height, rects):
    """Write the rectangles as a PPM, painted onto black."""
    buf = bytearray(width * height * 3)
    for x, y, w, h, data in rects:
        for row in range(h):
            sy = y + row
            if sy >= height:
                break
            src = row * w * 4
            dst = (sy * width + x) * 3
            line = bytearray(w * 3)
            for col in range(w):
                s = src + col * 4
                d = col * 3
                line[d] = data[s + 2]
                line[d + 1] = data[s + 1]
                line[d + 2] = data[s]
            end = min(len(buf), dst + len(line))
            buf[dst:end] = line[:end - dst]
    header = header_for(width, height)
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(bytes(buf))


def header_for(width, height):
    """The P6 magic, dimensions and maximum value."""
    return (chr(80) + chr(54) + chr(10) + str(width) + chr(32) + str(height) + chr(10) + str(255) + chr(10)).encode()


class Recorder:
    """Captures frames on a background thread until stopped."""

    def __init__(self, host, port, outdir, fps=2, seconds=2400):
        self.host = host
        self.port = port
        self.outdir = outdir
        self.interval = 1.0 / float(max(1, fps))
        self.deadline = time.time() + seconds
        self.stop_flag = threading.Event()
        self.thread = None
        self.count = 0
        self.error = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        client = None
        try:
            client = RFB(self.host, self.port)
            client.handshake()
            while not self.stop_flag.is_set() and time.time() < self.deadline:
                client.request_update(0)
                try:
                    rects = client.read_frame()
                except socket.timeout:
                    # THE SERVER SENDS NOTHING WHILE THE GUEST IS STILL BOOTING.
                    #
                    # The first attempt died here after exactly twenty seconds - the socket
                    # timeout - and reported 'captured 0 frames, error: timed out'. qemu has no
                    # framebuffer to describe until the guest draws something, so a read that
                    # returns nothing is normal early on and must not end the recording. The
                    # request is simply repeated - after a short pause, so a guest that draws
                    # nothing for a minute does not turn this into a busy loop.
                    time.sleep(self.interval)
                    continue
                if rects:
                    self.count += 1
                    name = "vnc-%05d.ppm" % self.count
                    write_ppm(os.path.join(self.outdir, name),
                              client.width, client.height, rects)
                time.sleep(self.interval)
        except Exception as exc:
            self.error = str(exc)
            log("recorder stopped: %s" % exc)
        finally:
            if client is not None:
                client.close()

    def stop(self):
        self.stop_flag.set()
        if self.thread is not None:
            self.thread.join(timeout=15)
        return self.count, self.error


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5900)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--fps", type=int, default=2)
    ap.add_argument("--seconds", type=int, default=2400)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    rec = Recorder(args.host, args.port, args.outdir, fps=args.fps, seconds=args.seconds)
    rec.start()
    log("capturing at %d fps" % args.fps)

    # Stay in the foreground so the caller controls the lifetime; SIGINT or SIGTERM stops it.
    try:
        while rec.thread is not None and rec.thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        count, error = rec.stop()
        log("captured %d frames%s" % (count, (", error: " + error) if error else ("")))
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())
