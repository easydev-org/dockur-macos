#!/usr/bin/env bash
# Wait for the installer to say it finished, then photograph the guest.
#
# WHY THIS REPLACES THE du POLLING. du over the storage tree is not a completion
# signal - it is noise. Measured over one run it went
#
#   765 -> 8065 -> 27306 -> 18555 -> 28159 -> 15637 -> 14891 MiB
#
# on a disk that was never reinstalled (dockur logged "Downloading macOS" once
# and never rebuilt the image). An APFS install writes and deletes temporary
# files, so "size stopped changing for N polls" measures the gap between two
# temporary files, not progress. Earlier runs that looked settled at 27306 MiB
# simply happened to stop between two such files.
#
# The only honest signal is the marker the guest writes:
#
#   install/recovery/launch.sh:
#     [ -e "$BOOTSTRAP_DONE" ]   && { kill -USR1 "$STARTOSINSTALL_PID"; break; }
#     [ -e "$BOOTSTRAP_FAILED" ] && fail "bootstrap installation failed"
#
# bootstrap.done is what releases startosinstall's --rebootdelay, so its
# appearance is both "the guest finished its part" and "a reboot is coming".
# bootstrap.failed means it gave up and the log beside it says why.
#
# usage: wait_for_bootstrap.sh <container> <timeout-seconds> <shot-prefix>
set -uo pipefail

NAME="${1:?container}"
TIMEOUT="${2:-1800}"
PREFIX="${3:-/tmp/wait}"
STORAGE_DIR="${4:-}"
STATE=/run/shm/installstate

echo "=== waiting for $STATE/bootstrap.done (timeout ${TIMEOUT}s) ==="
start=$(date +%s)
result=timeout

while :; do
  now=$(date +%s)
  elapsed=$((now - start))
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "TIMEOUT after ${elapsed}s"
    result=timeout
    break
  fi

  # One docker exec per poll, asking about both markers at once so the per-poll
  # cost stays low.
  state=$(docker exec "$NAME" sh -c "
    d=$STATE
    [ -e \$d/bootstrap.done ]   && echo DONE   && exit 0
    [ -e \$d/bootstrap.failed ] && echo FAILED && exit 0
    [ -e \$d/bootstrap.ready ]  && echo READY  && exit 0
    echo NOTSTARTED
  " 2>/dev/null) || state=NOCONTAINER

  # Storage path is passed in so the heartbeat stays honest about where the disk is.
  used=$(du -sm "${STORAGE_DIR:-/nonexistent}" 2>/dev/null | cut -f1 || echo 0)
  echo "[${elapsed}s] bootstrap=${state} storage=${used}MiB"

  case "$state" in
    DONE)
      echo "BOOTSTRAP DONE after ${elapsed}s"
      result=done
      break
      ;;
    FAILED)
      echo "BOOTSTRAP FAILED after ${elapsed}s - guest log follows"
      docker exec "$NAME" sh -c "cat $STATE/install.log 2>/dev/null | tail -40" || true
      docker exec "$NAME" sh -c "cat $STATE/packages.log 2>/dev/null | tail -20" || true
      result=failed
      break
      ;;
    NOCONTAINER)
      echo "container gone after ${elapsed}s"
      result=nocontainer
      break
      ;;
  esac

  sleep 30
done

# Whatever happened, a picture now says more than another number. Taken while
# the container is still alive - the earlier failures came from photographing a
# stopped container.
echo "=== screenshot before returning (result: $result) ==="
if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  docker cp scripts/shotsock.py "$NAME":/tmp/shotsock.py >/dev/null 2>&1 || true
  docker exec "$NAME" python3 /tmp/shotsock.py /run/shm/monitor.sock "/tmp/${PREFIX##*/}.ppm" >/dev/null 2>&1 || echo "screendump failed"
  docker cp "$NAME:/tmp/${PREFIX##*/}.ppm" "${PREFIX}.ppm" >/dev/null 2>&1 || echo "copy failed"
  if [ -s "${PREFIX}.ppm" ]; then
    python3 scripts/ppm2png.py "${PREFIX}.ppm" "${PREFIX}.png" || true
  fi
else
  echo "container not running - no screenshot"
fi

echo "RESULT=$result"
case "$result" in
  done) exit 0 ;;
  failed) exit 2 ;;
  *) exit 1 ;;
esac
