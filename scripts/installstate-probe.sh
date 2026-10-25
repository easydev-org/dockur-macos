#!/usr/bin/env bash
# Print the installer bootstrap state from inside the running container.
#
# WHY THIS EXISTS. install/recovery/launch.sh drives the whole install and only
# reboots when bootstrap.done appears:
#
#   while kill -0 "$STARTOSINSTALL_PID"; do
#     [ -e "$BOOTSTRAP_DONE" ]   && { kill -USR1 "$STARTOSINSTALL_PID"; break; }
#     [ -e "$BOOTSTRAP_FAILED" ] && fail "bootstrap installation failed"
#     sleep 1
#   done
#
# startosinstall was given --rebootdelay 300, but the USR1 signal is what releases
# that delay. No bootstrap.done means no signal, no reboot, and launch.sh sleeps
# forever - which is what every bake so far has looked like from outside: a disk
# that stops growing and never moves again.
#
# The state directory is a 9p share at /run/shm/installstate inside the container
# (QEMU_DIR=/run/shm in the qemux/qemu base image). Reading it is the only way to
# see WHY bootstrap stopped: the guest writes its own log beside the markers.
#
# usage: installstate-probe.sh <container-name>

set -uo pipefail
NAME="${1:?container name}"

probe() {
  docker exec "$NAME" sh -c \(quote\)
    for d in /run/shm/installstate /run/installstate /storage/installstate; do
      [ -d "$d" ] || continue
      echo "STATE_DIR=$d"
      ls -la "$d" 2>/dev/null
      for f in "$d"/*.log; do
        [ -s "$f" ] || continue
        echo "--- tail of $f ---"
        tail -30 "$f" 2>/dev/null
      done
      exit 0
    done
    echo "NO STATE DIR"
    echo "--- /run/shm ---"
    ls -la /run/shm 2>/dev/null | head -20
  \(quote\) 2>&1
}

echo "=== installstate probe at $(date -u +%H:%M:%S) ==="
probe || true

for marker in bootstrap.ready bootstrap.done bootstrap.failed; do
  if docker exec "$NAME" sh -c "test -e /run/shm/installstate/$marker" 2>/dev/null; then
    echo "MARKER $marker: present"
  else
    echo "MARKER $marker: absent"
  fi
done
