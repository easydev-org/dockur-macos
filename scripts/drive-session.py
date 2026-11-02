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
    """Start the VNC recorder as a child process, returning it.

    NOT ffmpeg -f vnc: the ffmpeg from Ubuntu is built without --enable-libvncclient and
    answers 'Unknown input format: vnc'. scripts/vnc-record.py speaks RFB directly instead and
    writes PPM frames, which are muxed into video afterwards.
    """
    argv = [
        "python3", "scripts/vnc-record.py",
        "--host", "127.0.0.1", "--port", "5900",
        "--outdir", frames,
        "--fps", str(fps), "--seconds", str(seconds),
    ]
    log("recording at %d fps via RFB" % fps)
    log_path = os.path.join(frames, "vnc.log")
    with open(log_path, "w") as logf:
        proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT)
    time.sleep(5)
    if proc.poll() is not None:
        run(["tail", "-20", log_path])
        log("recorder exited early")
        return None
    log("recorder running as pid %d" % proc.pid)
    return proc


def stop_recording(proc, frames):
    """Stop the recorder, then mux its frames into an mp4.

    SIGTERM first and a wait, because the recorder closes its socket and finishes the frame it
    is writing; a kill would leave a truncated PPM. The mux is ffmpeg on the image sequence,
    which needs no network input support.
    """
    if proc is not None:
        log("stopping the recorder")
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            pass
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(1)
        if proc.poll() is None:
            log("recorder ignored SIGTERM; killing")
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    log_path = os.path.join(frames, "vnc.log")
    if os.path.exists(log_path):
        log("--- vnc.log ---")
        with open(log_path) as fh:
            for line in fh.read().splitlines()[-25:]:
                log("  " + line)
    frames_list = sorted(n for n in os.listdir(frames)
                         if n.startswith("vnc-") and n.endswith(".ppm"))
    log("recorder produced %d frames" % len(frames_list))
    out_path = os.path.join(frames, "session.mp4")
    if len(frames_list) < 2:
        log("not enough frames to mux a video")
        return
    argv = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-framerate", "2",
        "-pattern_type", "glob", "-i", os.path.join(frames, "vnc-*.ppm"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "2",
        out_path,
    ]
    code, out, err = run(argv)
    if os.path.exists(out_path):
        log("video: %.1f MB" % (os.path.getsize(out_path) / (1024.0 * 1024.0)))
    else:
        log("muxing failed (exit %d)" % code)
    for name in frames_list:
        try:
            os.remove(os.path.join(frames, name))
        except OSError:
            pass
    log("removed %d intermediate frames" % len(frames_list))


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
