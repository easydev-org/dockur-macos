#!/bin/bash
# Publish the disk that stage 1 left behind: macOS installed, stopped at Setup Assistant.
#
# WHY THIS EXISTS. The manual branch returns as soon as Setup Assistant appears, and every
# later step in the workflow is disabled for it - 'what is actually on the disk' and
# 'extract the golden disk' both carry `if: manual_install != 'true'`. Two runs reached the
# boundary (14 at step 77, 15 at step 96) and both threw the disk away with the runner.
#
# Usage: publish-setup-disk.sh <version> <tag>
#   tag defaults to "<version>-setup"
set -eux

V="${1:?version required}"
TAG="${2:-}"
[ -n "$TAG" ] || TAG="${V}-setup"

REPO="ghcr.io/easydev-org/golden-macos"

# Publish only a disk that reached the boundary. The driver writes the marker to its own
# log, so this asks the log rather than trusting the step's status - the step ends in
# '|| true' and always reports success.
if ! grep -q "SETUP ASSISTANT REACHED" frames/stage1-coords.log 2>/dev/null; then
  echo "::warning::the driver never reached Setup Assistant; not publishing"
  exit 0
fi

# qemu holds data.img open with cache=none, so converting against a live file yields a
# torn image.
sudo docker rm -f macinst >/dev/null 2>&1 || true
sudo pkill -f "qemu-system-x86_64" 2>/dev/null || true

# Process check that cannot match itself.
#
# 'pgrep -f qemu-system-x86_64' matches its OWN command line - the pattern is in pgrep's
# argv - so the first publish aborted with 'qemu still running' and the disk perfectly
# idle. 'pgrep -x' avoids that but warns that a name over 15 characters matches nothing,
# and qemu-system-x86_64 is 18.
#
# So ask /proc directly: read each process's comm, which is the executable name and can
# never contain our search string. qemu's comm is 'qemu-system-x86' (15 chars, truncated by
# the kernel), so the prefix test is the correct one.
qemu_running() {
  for c in /proc/[0-9]*/comm; do
    [ -r "$c" ] || continue
    case "$(cat "$c" 2>/dev/null)" in
      qemu-system-*) return 0 ;;
    esac
  done
  return 1
}

for _ in $(seq 1 30); do
  qemu_running || break
  sleep 2
done

if qemu_running; then
  echo "::error::qemu still running, refusing to touch the disk"
  for c in /proc/[0-9]*/comm; do
    [ -r "$c" ] || continue
    case "$(cat "$c" 2>/dev/null)" in qemu-system-*) echo "  $c: $(cat "$c")" ;; esac
  done
  exit 1
fi

SRC="vm/data.img"
if [ ! -s "$SRC" ]; then
  echo "::error::no vm/data.img"
  ls -la vm/ || true
  exit 1
fi
ls -la "$SRC"

# Compressed qcow2: the raw disk is 64 GiB and a fresh install leaves most of it empty.
qemu-img convert -O qcow2 -c "$SRC" data.qcow2
ls -la data.qcow2
qemu-img info data.qcow2

# The evidence travels with the disk so a consumer can see why it is trusted.
{
  echo "version: $V"
  echo "stage: setup-assistant"
  echo "driver: scripts/erase-by-coords.py - fixed coordinates, OCR only locates controls"
  echo "erase: $(grep -o 'erase reported complete.*' frames/stage1-coords.log | head -1)"
  echo "install: $(grep -o 'will be installed on the disk.*' frames/stage1-coords.log | head -1)"
  echo "setup_assistant: $(grep -o 'SETUP ASSISTANT REACHED.*' frames/stage1-coords.log | head -1)"
  echo "sha256: $(sha256sum data.qcow2 | cut -d' ' -f1)"
} > data.txt
cat data.txt

# oras rather than docker push: the payload is a bare qcow2, not a container image.
curl -sSL https://github.com/oras-project/oras/releases/download/v1.2.0/oras_1.2.0_linux_amd64.tar.gz \
  | sudo tar -xz -C /usr/local/bin oras
echo "$GITHUB_TOKEN" | oras login ghcr.io -u "$GITHUB_ACTOR" --password-stdin

oras push "$REPO:$TAG" \
  --artifact-type application/vnd.easydev.golden-disk.v1+qcow2 \
  --annotation "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" \
  --annotation "org.opencontainers.image.revision=$GITHUB_SHA" \
  data.qcow2:application/vnd.easydev.qcow2 \
  data.txt:text/plain

echo "=== pushed $REPO:$TAG ==="
oras manifest fetch "$REPO:$TAG" | head -30
