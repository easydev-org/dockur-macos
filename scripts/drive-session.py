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


def start_recording(frames, seconds=2400, fps=2):
    """Start ffmpeg on the VNC stream as a background process, returning it.

    screendump costs 0.2-1.6s per frame and would perturb the action timing. VNC is passive:
    the guest draws the same whether or not anyone is watching, and ffmpeg captures it at a
    steady rate.
    """
    if shutil.which("ffmpeg") is None:
        run(["sudo", "apt-get", "install", "-y", "-qq", "ffmpeg"])
    out_path = os.path.join(frames, "session.mp4")
    log_path = os.path.join(frames, "ffmpeg.log")
    argv = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-f", "vnc", "-r", str(fps), "-i", "127.0.0.1:5900",
        "-t", str(seconds), "-r", str(fps), "-pix_fmt", "yuv420p",
        out_path,
    ]
    log("recording to %s at %d fps" % (out_path, fps))
    with open(log_path, "w") as logf:
        proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT)
    time.sleep(3)
    if proc.poll() is not None:
        run(["tail", "-20", log_path])
        log("recorder exited early")
        return None
    log("recorder running as pid %d" % proc.pid)
    return proc


def stop_recording(proc, frames):
    """Terminate ffmpeg and wait for it.

    A hard kill leaves an mp4 with no moov atom, which no player will open, so this sends
    SIGTERM and waits, killing only if it will not go.
    """
    if proc is None:
        return
    log("stopping the recorder")
    try:
        proc.send_signal(signal.SIGTERM)
    except OSError:
        pass
    for _ in range(30):
        if proc.poll() is not None:
            break
        time.sleep(1)
    if proc.poll() is None:
        log("recorder ignored SIGTERM; killing")
        proc.kill()
        proc.wait(timeout=10)
    out_path = os.path.join(frames, "session.mp4")
    if os.path.exists(out_path):
        size = os.path.getsize(out_path)
        log("recording: %.1f MB" % (size / (1024.0 * 1024.0)))
        code, out, err = run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration,size",
            "-of", "default=nw=1", out_path], echo=False)
        if out:
            print(out, flush=True)
    else:
        log("no recording was produced")


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
    if code != 0:
        log("the driver exited %d" % code)
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True,
                    help="launcher image holding qemu and the OpenCore assets")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--driver", default="fixed", choices=("fixed", "ocr"))
    ap.add_argument("--record-seconds", type=int, default=2400)
    args = ap.parse_args()

    log("session start")
    claim_kvm()
    frames, _vm = prepare(args.workspace)

    start_driver(args.image, args.workspace)
    wait_for_qmp(frames)
    recorder = start_recording(frames, seconds=args.record_seconds)

    code = drive(frames, args.driver)

    stop_recording(recorder, frames)
    run(["sudo", "chown", "-R", "%d:%d" % (os.getuid(), os.getgid()), frames])

    log("frames in %s:" % frames)
    for name in sorted(os.listdir(frames))[-25:]:
        path = os.path.join(frames, name)
        log("  %s  %d bytes" % (name, os.path.getsize(path)))

    run(["docker", "stop", "-t", "30", "driver"], echo=False)
    log("session end (driver exit %d)" % code)


if __name__ == "__main__":
    main()
