#!/usr/bin/env python3
"""Boot the guest and drive it, in Python, with no shell in between.

WHY THIS REPLACES THE SHELL. The step it replaces was ninety lines of shell inside a
workflow that cost four consecutive runs, and not one failure was about the wizard:

  FFMPEG_PID=                    - a pid capture that lost its dollar sign
  echo "recording started"        - and the reference to it
  docker: requires at least 1 argument
                                - a comment between continuation lines ended the command,
                                  so docker never saw an IMAGE

Each was a quoting or continuation accident in a language where a stray character becomes a
runtime failure. Here every external command is an argv list, and every container script is a
file rather than an inline string.

Usage: drive-session.py --image IMG --workspace PATH [--driver fixed|ocr]
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time


def log(msg):
    print("[session %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(argv, check=False, echo=True, timeout=None):
    """Run argv (a list) with no shell.

    A string is rejected on purpose: accepting one would reintroduce exactly the class of
    fault this file exists to remove.
    """
    if isinstance(argv, (str, bytes)):
        raise TypeError("run() takes a list of arguments, not a string")
    argv = [str(a) for a in argv]
    if echo:
        print(" " + " ".join(argv), flush=True)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        code, out, err = p.returncode, p.stdout, p.stderr
    except FileNotFoundError as exc:
        code, out, err = 127, "", str(exc)
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", "timed out after %ss" % timeout
    if echo and (out or err):
        for line in (out or "").rstrip().splitlines()[-8:]:
            print("  " + line, flush=True)
        for line in (err or "").rstrip().splitlines()[-4:]:
            print("! " + line, flush=True)
    if check and code != 0:
        raise SystemExit("command failed: %s (exit %d)" % (" ".join(argv), code))
    return code, out, err


def claim_kvm():
    """Make /dev/kvm usable, or stop.

    Without this qemu falls back to emulation and the guest takes hours instead of minutes,
    which reads as a hang rather than as a failure.
    """
    if os.access("/dev/kvm", os.R_OK | os.W_OK):
        log("/dev/kvm is usable")
        return
    log("/dev/kvm is not accessible; granting it")
    rule = 'KERNEL=="kvm", GROUP="kvm", MODE="0666"\n'
    path = "/etc/udev/rules.d/99-kvm4all.rules"
    tee = subprocess.run(["sudo", "tee", path], input=rule, capture_output=True, text=True)
    if tee.returncode != 0:
        raise SystemExit("could not write the udev rule")
    run(["sudo", "udevadm", "control", "--reload-rules"])
    run(["sudo", "udevadm", "trigger", "--name-match=kvm"])
    run(["sudo", "chmod", "666", "/dev/kvm"])
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise SystemExit("NO /dev/kvm - qemu would fall back to emulation")


def prepare(workspace):
    """Create frames/ and vm/, and make frames writable by the container."""
    frames = os.path.join(workspace, "frames")
    vm = os.path.join(workspace, "vm")
    for path in (frames, vm):
        os.makedirs(path, exist_ok=True)
    run(["chmod", "777", frames])
    log("workspace ready: %s" % workspace)
    return frames, vm


def start_driver(image, workspace):
    """Start the container that runs qemu, returning its id.

    The arguments are a list, so nothing can be broken here by a comment, a continuation or a
    quote. The VNC port is published to the host for the recorder.
    """
    run(["docker", "rm", "-f", "driver"], echo=False)
    argv = [
        "docker", "run", "-d", "--name", "driver",
        "--device=/dev/kvm",
        "-p", "127.0.0.1:5900:5900",
        "--entrypoint", "sh",
        "-v", "%s/vm:/vm" % workspace,
        "-v", "%s/frames:/frames" % workspace,
        "-v", "%s/scripts:/scripts:ro" % workspace,
        image, "/scripts/drive-in-container.sh",
    ]
    code, out, err = run(argv, check=True)
    cid = (out or "").strip().splitlines()[-1] if out else ""
    log("driver container %s" % cid[:12])
    return cid


def wait_for_qmp(frames, attempts=40, pause=5):
    """Wait for qemu to create its QMP socket."""
    sock = os.path.join(frames, "monitor.sock.qmp")
    for i in range(1, attempts + 1):
        if os.path.exists(sock):
            log("QMP socket appeared after %ds" % ((i - 1) * pause))
            return sock
        time.sleep(pause)
    run(["docker", "logs", "--tail", "40", "driver"])
    raise SystemExit("the QMP socket never appeared")


def measure_screendump(frames):
    """Report how long one screendump takes, before committing to a capture rate.

    The OCR driver sleeps 1.5s after each dump, and the "0.2-1.6s per frame" figure in the
    comments includes that sleep. Only a measurement decides whether a 1 fps capture through
    QMP is possible here: VNC was tried first and delivered nothing, with the recorder waiting
    120 seconds for a first frame while the guest was drawing happily.
    """
    sock = os.path.join(frames, "monitor.sock")
    run([sys.executable, "scripts/probe-screendump.py", sock, frames])


def mux_video(frames, fps=5):
    """Mux the captured frames into session.mp4, then remove them.

    The frames come from drive-fixed.py, which captures on its own QMP connection - there is
    no recorder process to stop first. ffmpeg is used only as an image-sequence muxer, which
    needs no network input support.
    """
    shots = sorted(n for n in os.listdir(frames)
                   if n.startswith("cap-") and n.endswith(".ppm"))
    log("muxing %d captured frames" % len(shots))
    out_path = os.path.join(frames, "session.mp4")
    if len(shots) < 2:
        log("not enough frames to mux a video")
        return
    argv = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-framerate", str(fps),
        "-pattern_type", "glob", "-i", os.path.join(frames, "cap-*.ppm"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        out_path,
    ]
    code, out, err = run(argv)
    if os.path.exists(out_path):
        log("video: %.1f MB" % (os.path.getsize(out_path) / (1024.0 * 1024.0)))
        code2, info, _ = run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration", "-of", "default=nw=1", out_path], echo=False)
        if info:
            log(info.strip())
    else:
        log("muxing failed (exit %d)" % code)
    for name in shots:
        try:
            os.remove(os.path.join(frames, name))
        except OSError:
            pass
    log("removed %d intermediate frames" % len(shots))


def drive(frames, driver):
    """Run the chosen driver against the QMP socket."""
    sock = os.path.join(frames, "monitor.sock")
    if driver == "fixed":
        argv = ["sudo", "python3", "scripts/drive-fixed.py", sock, frames]
    else:
        argv = ["sudo", "python3", "scripts/drive-ocr.py", sock,
                os.path.join(frames, "frame")]
    log("driving with %s" % driver)
    code, out, err = run(argv, timeout=3600)
    # THE TRACEBACK IS THE ONLY THING THAT EXPLAINS A NON-ZERO EXIT.
    #
    # run() prints the last few lines of each stream, and a Python traceback is wider than
    # that - a crash left nothing but 'the driver exited 1' in the log, with the actual error
    # cut off above the window. The tail is echoed here in full.
    if code != 0:
        log("--- driver stderr ---")
        for line in (err or "").rstrip().splitlines()[-30:]:
            log("  " + line)
        log("--- driver stdout ---")
        for line in (out or "").rstrip().splitlines()[-12:]:
            log("  " + line)
    if code != 0:
        log("the driver exited %d" % code)
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True,
                    help="launcher image holding qemu and the OpenCore assets")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--driver", default="fixed", choices=("fixed", "ocr"))
    args = ap.parse_args()

    log("session start")
    claim_kvm()
    frames, _vm = prepare(args.workspace)

    start_driver(args.image, args.workspace)
    wait_for_qmp(frames)
    measure_screendump(frames)

    # NO SEPARATE RECORDER PROCESS.
    #
    # A second connection to the QMP socket is served for the greeting and then not again, so
    # the driver's own connect() blocked until it timed out:
    #
    #   File "qmp.py", line 52, in __init__
    #     self.greeting = self._read_message()
    #   TimeoutError: timed out
    #
    # The driver therefore records through the connection it already holds - see
    # drive-fixed.py's start_capture() - and this session only muxes the frames afterwards.
    code = drive(frames, args.driver)

    mux_video(frames, fps=5)
    run(["sudo", "chown", "-R", "%d:%d" % (os.getuid(), os.getgid()), frames])

    log("frames in %s:" % frames)
    for name in sorted(os.listdir(frames))[-25:]:
        path = os.path.join(frames, name)
        log("  %s  %d bytes" % (name, os.path.getsize(path)))

    run(["docker", "stop", "-t", "30", "driver"], echo=False)
    log("session end (driver exit %d)" % code)


if __name__ == "__main__":
    main()
