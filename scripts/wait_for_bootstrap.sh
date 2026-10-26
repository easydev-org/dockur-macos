#!/usr/bin/env bash
# Wait for the whole macOS install, in two stages, and photograph it throughout.
#
# THE MISTAKE THIS FIXES. bootstrap.done was treated as "installed". It is not.
# The screenshot taken the instant it appeared showed an Apple logo, a progress
# bar at about 5%, and the line "About 29 minutes remaining...". So bootstrap.done
# means:
#
#   launch.sh: [ -e "$BOOTSTRAP_DONE" ] && { kill -USR1 "$STARTOSINSTALL_PID"; break; }
#
# i.e. "the bootstrap packages are in place, release startosinstall's reboot
# delay". macOS then reboots INSIDE the guest and spends another half hour
# writing the system. Stopping the container at that moment is what truncated
# every run - the disk went from 28563 MiB at bootstrap.done down to 18340 MiB by
# the time it was booted, because the container was killed mid-install.
#
# So:
#   stage 1  wait for bootstrap.done            (the guest's own marker)
#   stage 2  keep waiting, sampling the screen, because the only progress
#            indicator after that is the guest's own UI - "About N minutes
#            remaining" while writing, then a desktop when done
#
# The container is never stopped here. Stage 2 ends when the screen stops looking
# like an installer, or when the deadline passes.
#
# usage: wait_for_bootstrap.sh <container> <storage> <stage1-secs> <stage2-secs> <shot-prefix>
set -uo pipefail

NAME="${1:?container}"
STORAGE="${2:?storage dir}"
STAGE1="${3:-1800}"
STAGE2="${4:-2700}"
PREFIX="${5:-/tmp/wait}"
STATE=/run/shm/installstate

probe() {
  docker exec "$NAME" sh -c "
    d=$STATE
    [ -e \$d/bootstrap.done ]   && { echo DONE;   exit 0; }
    [ -e \$d/bootstrap.failed ] && { echo FAILED; exit 0; }
    [ -e \$d/bootstrap.ready ]  && { echo READY;  exit 0; }
    echo NOTSTARTED
  " 2>/dev/null || echo NOCONTAINER
}

shot() {
  local out="$1"
  docker cp scripts/shotsock.py "$NAME":/tmp/shotsock.py >/dev/null 2>&1 || return 1
  docker exec "$NAME" python3 /tmp/shotsock.py /run/shm/monitor.sock "/tmp/$out.ppm" >/dev/null 2>&1 || return 1
  docker cp "$NAME:/tmp/$out.ppm" "./$out.ppm" >/dev/null 2>&1 || return 1
  python3 scripts/ppm2png.py "./$out.ppm" "./$out.png" 2>/dev/null || true
  [ -s "./$out.png" ]
}

# ---------------------------------------------------------------------------
# Stage 1: the guest's own marker.
# ---------------------------------------------------------------------------
echo "=== stage 1: waiting for $STATE/bootstrap.done (timeout ${STAGE1}s) ==="
start=$(date +%s)
stage1=timeout
while :; do
  elapsed=$(( $(date +%s) - start ))
  [ "$elapsed" -ge "$STAGE1" ] && { echo "stage 1 TIMEOUT after ${elapsed}s"; break; }

  st=$(probe)
  used=$(du -sm "$STORAGE" 2>/dev/null | cut -f1 || echo 0)
  echo "[s1 ${elapsed}s] bootstrap=${st} storage=${used}MiB"

  case "$st" in
    DONE)   echo "STAGE 1 DONE after ${elapsed}s"; stage1=done; break ;;
    FAILED) echo "STAGE 1 FAILED after ${elapsed}s"
            docker exec "$NAME" sh -c "tail -40 $STATE/install.log 2>/dev/null" || true
            stage1=failed; break ;;
    NOCONTAINER) echo "container gone after ${elapsed}s"; break ;;
  esac
  sleep 30
done

# ---------------------------------------------------------------------------
# Stage 2: macOS reboots inside the guest and installs for another ~30 minutes.
# Nothing on the host reports this, so sample the screen.
# ---------------------------------------------------------------------------
if [ "$stage1" = "done" ]; then
  echo "=== stage 2: $(( STAGE2 / 60 )) minutes of the guest finishing its own install ==="
  start=$(date +%s)
  round=0
  desktop=no
  while :; do
    elapsed=$(( $(date +%s) - start ))
    [ "$elapsed" -ge "$STAGE2" ] && { echo "stage 2 TIMEOUT after ${elapsed}s"; break; }

    round=$((round + 1))
    used=$(du -sm "$STORAGE" 2>/dev/null | cut -f1 || echo 0)
    echo "[s2 ${elapsed}s] round ${round} storage=${used}MiB"

    if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
      echo "container exited during stage 2"
      docker logs --tail 40 "$NAME" || true
      break
    fi

    if shot "s2-${round}"; then
      # Judge the screen by ppm2png's EXIT CODE, not by grepping its output.
      #
      # The previous version captured `... | head -3` and looked for "PICTURE OK"
      # in it. ppm2png prints the size line and up to five colour lines before
      # that verdict, so the marker sat on line 6 or 7 and head -3 threw it away -
      # the loop reported NOT A DESKTOP every round while the screen had in fact
      # been a desktop since round 8.
      #
      # ppm2png already encodes the answer properly: it SystemExits non-zero on
      # "NOT A DESKTOP" and exits 0 after printing "PICTURE OK". So use that.
      if python3 scripts/ppm2png.py "./s2-${round}.ppm" "./s2-${round}.png" > "./s2-${round}.txt" 2>&1; then
        sed -n '1p;$p' "./s2-${round}.txt" | sed 's/^/  screen: /'
        echo "DESKTOP REACHED at stage-2 round ${round} (${elapsed}s into stage 2)"
        desktop=yes
        break
      else
        sed -n '1p;$p' "./s2-${round}.txt" | sed 's/^/  screen: /'
      fi
    else
      echo "  screenshot failed this round"
    fi

    sleep 120
  done
  if [ "$desktop" = "yes" ]; then
    echo "=== stage 2 ended because the desktop appeared ==="
  else
    echo "=== stage 2 window elapsed without a desktop; final look ==="
    shot "final" || echo "final screenshot failed"
  fi
fi

echo "RESULT stage1=${stage1}"
[ "$stage1" = "done" ] || exit 1
