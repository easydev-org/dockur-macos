#!/usr/bin/env bash
# Print the installer bootstrap state from inside the running container.
#
# WHY. install/recovery/launch.sh only proceeds when bootstrap.done appears:
#
#   while kill -0 "$STARTOSINSTALL_PID"; do
#     [ -e "$BOOTSTRAP_DONE" ]   && { kill -USR1 "$STARTOSINSTALL_PID"; break; }
#     [ -e "$BOOTSTRAP_FAILED" ] && fail "bootstrap installation failed"
#     sleep 1
#   done
#
# startosinstall has --rebootdelay 300, but USR1 is what releases it. Measured so
# far: bootstrap.ready appears and bootstrap.done never does - which is exactly
# why the install stops and nothing ever restarts.
#
# The state dir is a 9p share at /run/shm/installstate (QEMU_DIR=/run/shm in the
# qemux/qemu base image). The guest writes its own log beside the markers, and
# that log is the only account of what bootstrap actually failed on.
#
# usage: installstate-probe.sh <container-name>
set -uo pipefail
NAME="${1:?container name}"
STATE=/run/shm/installstate

echo "=== installstate probe at $(date -u +%H:%M:%S) ==="

# A static script piped to the container's sh: no nested quoting, and DIR comes
# from the environment so the heredoc body can stay untouched.
printf '%s\n' \
  'if [ -d "$DIR" ]; then' \
  '  echo "DIR_OK $DIR"' \
  '  ls -la "$DIR"' \
  '  for f in "$DIR"/*.log; do' \
  '    [ -s "$f" ] || continue' \
  '    echo "--- $f ---"' \
  '    tail -40 "$f"' \
  '  done' \
  'else' \
  '  echo "NO_STATE_DIR $DIR"' \
  '  echo "--- /run/shm ---"' \
  '  ls -la /run/shm 2>/dev/null | head -20' \
  'fi' \
  | docker exec -i -e DIR="$STATE" "$NAME" sh 2>&1 || true

echo '--- markers ---'
for marker in bootstrap.ready bootstrap.done bootstrap.failed; do
  if docker exec "$NAME" test -e "$STATE/$marker" 2>/dev/null; then
    echo "MARKER $marker: PRESENT"
  else
    echo "MARKER $marker: absent"
  fi
done
