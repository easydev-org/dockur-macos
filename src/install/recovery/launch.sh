#!/bin/bash
set -u

PATH="/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

LOCAL_LOG="/var/log/launch.log"
STATE_DIR="/Volumes/installstate"
STATE_LOG="$STATE_DIR/install.log"
APPLE_INSTALL_LOG="$STATE_DIR/apple.log"
STATE_LOG_LIMIT=$((1 * 1024 * 1024))
APPLE_INSTALL_LOG_LIMIT=$((4 * 1024 * 1024))
STARTED="$STATE_DIR/started"
TARGET_VOLUME="/Volumes/Macintosh HD"
ADMIN_PACKAGE="$STATE_DIR/admin.pkg"
SETUP_PACKAGE="$STATE_DIR/skipsetup.pkg"
FIRSTBOOT_SCRIPT="$STATE_DIR/firstboot.sh"
FIRSTBOOT_PLIST="$STATE_DIR/com.dockur.macos.firstboot.plist"
MIN_TARGET_SIZE=$((16 * 1024 * 1024 * 1024))

BOOTSTRAP_READY="$STATE_DIR/bootstrap.ready"
BOOTSTRAP_DONE="$STATE_DIR/bootstrap.done"
BOOTSTRAP_FAILED="$STATE_DIR/bootstrap.failed"

exec >> "$LOCAL_LOG" 2>&1

echo "[log] unattended installation starting"

mount_state_share() {

  local count=0

  while (( count < 120 )); do

    if [ -d "$STATE_DIR" ]; then
      return 0
    fi

    /sbin/mount_9p installstate >/dev/null 2>&1 || :

    [ -d "$STATE_DIR" ] && return 0

    count=$((count + 1))
    sleep 1
  done

  return 1
}

snapshot_log() {

  local source="$1"
  local dest="$2"
  local limit="$3"

  [ -f "$source" ] || return 0
  /usr/bin/tail -c "$limit" "$source" > "$dest" 2>/dev/null || :
  return 0
}

mirror_log() {

  local source="$1"
  local dest="$2"
  local limit="$3"

  while :; do
    snapshot_log "$source" "$dest" "$limit"
    sleep 2
  done
}

fail() {

  local message="$1"

  echo "[log] ERROR: $message"
  if [ -d "$STATE_DIR" ]; then
    snapshot_log "$LOCAL_LOG" "$STATE_LOG" "$STATE_LOG_LIMIT"
    snapshot_log "/var/log/install.log" "$APPLE_INSTALL_LOG" "$APPLE_INSTALL_LOG_LIMIT"
    rm -f "$STARTED"
  fi
  exec /usr/libexec/recoveryosd
  exit 1
}

select_target_disk() {

  local disk info size
  local best=""
  local best_size=0

  while IFS= read -r disk; do

    [ -n "$disk" ] || continue

    info=$(/usr/sbin/diskutil info "/dev/$disk" 2>/dev/null || :)
    [ -n "$info" ] || continue

    if printf '%s\n' "$info" |
       /usr/bin/grep -Eq '^[[:space:]]*(Read-Only (Media|Device)|(Media|Device) Read-Only):[[:space:]]*Yes'; then
      continue
    fi

    size=$(printf '%s\n' "$info" |
      /usr/bin/sed -nE 's/^[[:space:]]*Disk Size:.*\(([0-9]+) Bytes\).*/\1/p' |
      /usr/bin/head -n 1)

    [[ "$size" =~ ^[0-9]+$ ]] || continue
    (( size >= MIN_TARGET_SIZE )) || continue

    echo "[log] candidate target /dev/$disk ($size bytes)" >&2

    if (( size > best_size )); then
      best="$disk"
      best_size="$size"
    fi

  done < <(
    /usr/sbin/diskutil list physical 2>/dev/null |
      /usr/bin/sed -nE 's#^/dev/(disk[0-9]+).*#\1#p'
  )

  [ -n "$best" ] || return 1
  printf '/dev/%s\n' "$best"
}

find_startosinstall() {

  local file

  for file in \
    /Volumes/*/Install\ macOS*.app/Contents/Resources/startosinstall \
    /Volumes/*/Install\ OS\ X*.app/Contents/Resources/startosinstall \
    /Volumes/*/Install\ Mac\ OS\ X*.app/Contents/Resources/startosinstall \
    /Install\ macOS*.app/Contents/Resources/startosinstall \
    /Install\ OS\ X*.app/Contents/Resources/startosinstall \
    /Install\ Mac\ OS\ X*.app/Contents/Resources/startosinstall \
    /Applications/Install\ macOS*.app/Contents/Resources/startosinstall \
    /Applications/Install\ OS\ X*.app/Contents/Resources/startosinstall \
    /Applications/Install\ Mac\ OS\ X*.app/Contents/Resources/startosinstall; do

    if [ -x "$file" ]; then
      printf '%s\n' "$file"
      return 0
    fi
  done

  return 1
}

install_bootstrap() {

  local support_dir="$TARGET_VOLUME/Library/Application Support/macos-unattended"
  local daemon_dir="$TARGET_VOLUME/Library/LaunchDaemons"
  local staged_admin="$support_dir/admin.pkg"
  local staged_script="$support_dir/firstboot.sh"
  local staged_plist="$daemon_dir/com.dockur.macos.firstboot.plist"

  echo "[log] prepare phase completed; installing unattended bootstrap"

  # Do NOT install skipsetup.pkg here any more.
  #
  # This used to run at the end of the prepare phase, against $TARGET_VOLUME, and
  # it reported success - but the marker it writes
  # (/private/var/db/.AppleSetupDone) does not survive macOS's own second stage,
  # which rewrites the system volume. That is why the finished golden disk booted
  # to "Select Your Country or Region" instead of skipping Setup Assistant.
  #
  # The package is now handed to startosinstall via --installpackage so Apple's
  # installer applies it once the system exists. Installing it twice would only
  # hide which mechanism actually worked.
  echo "[log] Setup Assistant package deferred to startosinstall --installpackage"

  if ! /bin/mkdir -p "$support_dir" "$daemon_dir"; then
    echo "[log] ERROR: failed to create first-boot staging directories"
    return 1
  fi

  if ! /bin/cp -f "$ADMIN_PACKAGE" "$staged_admin" ||
     ! /bin/cp -f "$FIRSTBOOT_SCRIPT" "$staged_script" ||
     ! /bin/cp -f "$FIRSTBOOT_PLIST" "$staged_plist"; then
    echo "[log] ERROR: failed to stage first-boot files"
    return 1
  fi

  /usr/sbin/chown 0:0 "$staged_admin" "$staged_script" "$staged_plist" 2>/dev/null || :

  if ! /bin/chmod 0644 "$staged_admin" "$staged_plist" ||
     ! /bin/chmod 0755 "$staged_script"; then
    echo "[log] ERROR: failed to set first-boot file permissions"
    return 1
  fi

  # Verify the staged copies, but do not assume /usr/bin/cmp exists.
  #
  # macOS Recovery does not ship cmp, and this code path is only ever reached
  # from Recovery. The original form
  #
  #   if ! /usr/bin/cmp -s "$ADMIN_PACKAGE" "$staged_admin" || ...
  #
  # therefore always took the failure branch: the shell could not execute cmp,
  # the ! turned that into success, and stage_firstboot returned 1 with
  #
  #   [log] ERROR: staged first-boot files failed byte-for-byte validation
  #   [log] ERROR: bootstrap installation failed; cancelling prepared install
  #
  # even though the packages had installed cleanly just before:
  #
  #   installer: The install was successful.
  #   [log] prebuilt Setup Assistant package installed successfully
  #
  # That is what aborted every bake: bootstrap.done never appeared, so launch.sh
  # never sent USR1 to startosinstall, the --rebootdelay 300 never elapsed, and
  # the disk stopped growing for good.
  #
  # Use shasum when present (it is part of the base system), and fall back to
  # accepting the copy when neither tool exists - the preceding cp already
  # checked its exit status, so a missing checksum tool must not be fatal.
  _same_file() {
    local a="$1" b="$2"
    if [ -x /usr/bin/cmp ]; then
      /usr/bin/cmp -s "$a" "$b"
    elif [ -x /usr/bin/shasum ]; then
      [ "$(/usr/bin/shasum -a 256 < "$a")" = "$(/usr/bin/shasum -a 256 < "$b")" ]
    elif [ -x /usr/bin/md5 ]; then
      [ "$(/usr/bin/md5 -q "$a")" = "$(/usr/bin/md5 -q "$b")" ]
    else
      # No checksum tool in this environment; sizes at least must agree.
      [ "$(wc -c < "$a")" = "$(wc -c < "$b")" ]
    fi
  }

  if ! _same_file "$ADMIN_PACKAGE" "$staged_admin" ||
     ! _same_file "$FIRSTBOOT_SCRIPT" "$staged_script" ||
     ! _same_file "$FIRSTBOOT_PLIST" "$staged_plist"; then
    echo "[log] ERROR: staged first-boot files failed byte-for-byte validation"
    return 1
  fi

  if ! /usr/bin/plutil -lint "$staged_plist" >/dev/null 2>&1; then
    echo "[log] ERROR: staged first-boot LaunchDaemon plist is invalid"
    return 1
  fi

  echo "[log] unattended bootstrap installed successfully"
  return 0
}

run_bootstrapper() {

  trap '
    if install_bootstrap; then
      : > "$BOOTSTRAP_DONE"
    else
      : > "$BOOTSTRAP_FAILED"
    fi
    exit 0
  ' USR1

  : > "$BOOTSTRAP_READY"

  while :; do
    sleep 60
  done
}

if ! mount_state_share; then
  fail "failed to mount installation state share"
fi

if ! : > "$STATE_DIR/.write-test" 2>/dev/null; then
  fail "installation state share is not writable"
fi
rm -f "$STATE_DIR/.write-test"

snapshot_log "$LOCAL_LOG" "$STATE_LOG" "$STATE_LOG_LIMIT"
mirror_log "$LOCAL_LOG" "$STATE_LOG" "$STATE_LOG_LIMIT" &

echo "[log] installation state share mounted"

# ---------------------------------------------------------------------------
# Repair mode.
#
# When the disk already holds a completed macOS, do not install again - just
# write the Setup Assistant markers onto the installed volume and stop. This is
# the only mechanism that does not depend on timing:
#
#   prepare-phase install   gets overwritten when macOS rewrites the volume
#   --installpackage        lands wherever $3 points, not reliably this disk
#   firstboot LaunchDaemon  runs after Setup Assistant has already started
#
# Here recovery is writing to the finished volume directly, which is exactly what
# startosinstall does successfully on this disk during a normal install. If
# /System/Library/CoreServices/SystemVersion.plist exists, the system is there.
#
# Set REPAIR_ONLY=1 to force this, or let it happen automatically when a system is
# found and nothing has been started yet.
# ---------------------------------------------------------------------------

write_setup_markers() {
  local vol="$1"
  local marker="$vol/private/var/db/.AppleSetupDone"

  echo "[repair] writing markers onto $vol"

  /bin/mkdir -p "$vol/private/var/db" || { echo "[repair] ERROR: mkdir failed"; return 1; }
  /usr/bin/touch "$marker" || { echo "[repair] ERROR: touch failed"; return 1; }
  /usr/sbin/chown 0:0 "$marker" 2>/dev/null || :
  /bin/chmod 0644 "$marker" 2>/dev/null || :
  echo "[repair] wrote $marker"

  # The per-user and template buddies.
  local skipbuddy="$vol/Library/User Template/English.lproj/.skipbuddy"
  /bin/mkdir -p "${skipbuddy%/*}" 2>/dev/null || :
  /usr/bin/touch "$skipbuddy" 2>/dev/null && echo "[repair] wrote $skipbuddy" || :

  # DidSee* keys, which macOS 11+ also consults before showing the country picker.
  local plist="$vol/Library/User Template/Non_localized/Library/Preferences/com.apple.SetupAssistant.plist"
  /bin/mkdir -p "${plist%/*}" 2>/dev/null || :
  local key
  for key in DidSeeCloudSetup DidSeeSiriSetup DidSeeTrueToneSetup DidSeeDisplayTrueTone \
             DidSeePrivacy DidSeeAccessibility DidSeeScreenTime DidSeeSetupAssistant \
             DidSeeAppStore DidSeeTouchIDSetup DidSeeiCloudAnalyticsOptIn; do
    /usr/bin/defaults write "$plist" "$key" -bool true 2>/dev/null || :
  done
  /usr/bin/defaults write "$plist" "LastSeenCloudProductVersion" -string "14.0" 2>/dev/null || :
  /usr/bin/defaults write "$plist" "LastSeenBuddyBuildVersion" -string "23A344" 2>/dev/null || :
  echo "[repair] wrote Setup Assistant preference keys"

  # Same keys for every real account already on the volume, because that is the
  # user Setup Assistant would greet.
  local upref
  for upref in "$vol"/Users/*/Library/Preferences; do
    [ -d "$upref" ] || continue
    case "$upref" in */Shared/*|*/Guest/*) continue ;; esac
    for key in DidSeeCloudSetup DidSeeSiriSetup DidSeePrivacy DidSeeAccessibility \
               DidSeeScreenTime DidSeeSetupAssistant DidSeeAppStore DidSeeiCloudAnalyticsOptIn; do
      /usr/bin/defaults write "$upref/com.apple.SetupAssistant.plist" "$key" -bool true 2>/dev/null || :
    done
    /usr/bin/defaults write "$upref/com.apple.SetupAssistant.plist" "LastSeenCloudProductVersion" -string "14.0" 2>/dev/null || :
    /usr/bin/touch "$upref/../.skipbuddy" 2>/dev/null || :
    echo "[repair] wrote markers for $upref"
  done

  # Prove it landed rather than trusting the exit status.
  if [ -e "$marker" ]; then
    echo "[repair] VERIFIED: $marker exists"
    return 0
  fi
  echo "[repair] ERROR: $marker still missing after writing"
  return 1
}

# Inspect mode: report what is actually on the disk, change nothing.
#
# This exists because repair reported success - it wrote
# /private/var/db/.AppleSetupDone and verified the file - and the disk still booted
# to the country picker. Either the marker is not sufficient on macOS 14, or the
# volume the marker was written to is not the volume that boots. Only an
# inspection of the disk can tell those apart, and guessing has already cost
# several runs.
#
# It lists, for the mounted system volume:
#   - the marker files and their timestamps
#   - the local user database, which is what makes Setup Assistant believe an
#     account already exists
#   - the Setup Assistant preference files, system-wide and per user
#   - which volume was actually mounted and at what path
if [ -e "$STATE_DIR/inspect" ]; then
  INSPECT_VOL=""
  count=0
  while (( count < 60 )); do
    if [ -f "$TARGET_VOLUME/System/Library/CoreServices/SystemVersion.plist" ]; then
      INSPECT_VOL="$TARGET_VOLUME"
      break
    fi
    count=$((count + 1))
    sleep 2
  done

  {
    echo "[inspect] ===== what is mounted ====="
    /bin/df -h 2>/dev/null || :
    echo
    echo "[inspect] ===== candidate volume: ${INSPECT_VOL:-<none>} ====="

    if [ -n "$INSPECT_VOL" ]; then
      echo "[inspect] --- system version ---"
      cat "$INSPECT_VOL/System/Library/CoreServices/SystemVersion.plist" 2>/dev/null | \
        /usr/bin/grep -A1 -E 'ProductVersion|ProductBuildVersion' || :

      echo
      echo "[inspect] --- Setup Assistant markers ---"
      for f in "$INSPECT_VOL/private/var/db/.AppleSetupDone" \
               "$INSPECT_VOL/Library/User Template/English.lproj/.skipbuddy" \
               "$INSPECT_VOL/Library/User Template/Non_localized/Library/Preferences/com.apple.SetupAssistant.plist"; do
        if [ -e "$f" ]; then
          /bin/ls -la "$f"
        else
          echo "MISSING: $f"
        fi
      done

      echo
      echo "[inspect] --- local users (dslocal) ---"
      /bin/ls -la "$INSPECT_VOL/private/var/db/dslocal/nodes/Default/users" 2>/dev/null || echo "no users directory"

      echo
      echo "[inspect] --- /Users ---"
      /bin/ls -la "$INSPECT_VOL/Users" 2>/dev/null || :

      echo
      echo "[inspect] --- admin group ---"
      /bin/ls -la "$INSPECT_VOL/private/var/db/dslocal/nodes/Default/groups/admin.plist" 2>/dev/null || echo "no admin.plist"

      echo
      echo "[inspect] --- firstboot log if any ---"
      /bin/cat "$INSPECT_VOL/var/log/macos-unattended-firstboot.log" 2>/dev/null | /usr/bin/tail -40 || echo "no firstboot log"

      echo
      echo "[inspect] --- staged bootstrap payload ---"
      /bin/ls -laR "$INSPECT_VOL/Library/Application Support/macos-unattended" 2>/dev/null | /usr/bin/head -20 || echo "no staged payload"

      echo
      echo "[inspect] --- LaunchDaemons of ours ---"
      /bin/ls -la "$INSPECT_VOL/Library/LaunchDaemons" 2>/dev/null | /usr/bin/grep -i dockur || echo "our daemon is gone (ran already)"
    fi
    echo "[inspect] ===== end ====="
  } >> "$STATE_DIR/inspect.log" 2>&1 || :

  : > "$BOOTSTRAP_DONE"
  /sbin/shutdown -h now 2>/dev/null || :
  while :; do sleep 60; done
fi

# Repair is OPT-IN only, never automatic.
#
# The signal is a "repair" sentinel in the 9p state share, written by image.sh
# when the container was started with REPAIR_ONLY=1. Environment variables cannot
# reach here - this runs inside Recovery, a different boot entirely - so the
# shared directory is the only channel.
#
# Opt-in matters: an automatic "a system is present, skip the install" test would
# also fire on a freshly erased disk as soon as startosinstall stages its first
# files, silently turning every future bake into a no-op.
if [ -e "$STATE_DIR/repair" ]; then
  REPAIR_VOL=""
  count=0
  while (( count < 60 )); do
    if [ -f "$TARGET_VOLUME/System/Library/CoreServices/SystemVersion.plist" ]; then
      REPAIR_VOL="$TARGET_VOLUME"
      break
    fi
    count=$((count + 1))
    sleep 2
  done

  if [ -z "$REPAIR_VOL" ]; then
    fail "REPAIR_ONLY was requested but no complete system is on $TARGET_VOLUME"
  fi

  echo "[repair] a complete macOS is on $REPAIR_VOL - writing markers, not installing"
  if write_setup_markers "$REPAIR_VOL"; then
    : > "$BOOTSTRAP_DONE"
    echo "[log] Setup Assistant markers written; no installation performed"
    /sbin/shutdown -h now 2>/dev/null || :
    while :; do sleep 60; done
  fi
  fail "failed to write Setup Assistant markers"
fi

if [ -e "$STARTED" ]; then
  echo "[log] installation was already started; refusing to erase the target disk again"
  exec /usr/libexec/recoveryosd
  exit 1
fi

[ -s "$ADMIN_PACKAGE" ] || fail "account package is missing"
[ -s "$SETUP_PACKAGE" ] || fail "Setup Assistant package is missing"
[ -s "$FIRSTBOOT_SCRIPT" ] || fail "first-boot script is missing"
[ -s "$FIRSTBOOT_PLIST" ] || fail "first-boot LaunchDaemon plist is missing"
[ -x /usr/sbin/installer ] || fail "installer is not available in Recovery"
/usr/bin/plutil -lint "$FIRSTBOOT_PLIST" >/dev/null 2>&1 ||
  fail "first-boot LaunchDaemon plist is invalid"

STARTOSINSTALL=""
count=0
while (( count < 120 )); do
  STARTOSINSTALL=$(find_startosinstall || :)
  [ -n "$STARTOSINSTALL" ] && break
  count=$((count + 1))
  sleep 1
done

[ -n "$STARTOSINSTALL" ] || fail "startosinstall was not found in the recovery image"

echo "[log] using $STARTOSINSTALL"

USAGE=$("$STARTOSINSTALL" --usage 2>&1 || :)
[ -n "$USAGE" ] || fail "startosinstall did not return usage information"

printf '%s\n' "$USAGE" | /usr/bin/grep -q -- '--rebootdelay' ||
  fail "startosinstall does not support --rebootdelay"

printf '%s\n' "$USAGE" | /usr/bin/grep -q -- '--pidtosignal' ||
  fail "startosinstall does not support --pidtosignal"

# --installpackage is what makes the Setup Assistant suppression survive.
#
# The original design installed skipsetup.pkg from the bootstrap helper, at the
# end of startosinstall's prepare phase, while the target volume was mounted.
# That is too early: macOS rewrites the system volume during its own second
# stage, which discards /private/var/db/.AppleSetupDone. The result is exactly
# what the golden disk showed - a fully bootable macOS sitting on
# "Select Your Country or Region" because Setup Assistant had no reason to skip.
#
# --installpackage is the supported way to hand Apple's installer a package and
# have it applied after the system is in place, so the marker it writes is the
# one the first real boot sees.
printf '%s\n' "$USAGE" | /usr/bin/grep -q -- '--installpackage' ||
  fail "startosinstall does not support --installpackage"

echo "[log] unattended bootstrap preflight passed"

TARGET_DISK=""
count=0
while (( count < 120 )); do
  TARGET_DISK=$(select_target_disk || :)
  [ -n "$TARGET_DISK" ] && break
  count=$((count + 1))
  sleep 1
done

[ -n "$TARGET_DISK" ] || fail "no writable installation disk of at least 16 GiB was found"

echo "[log] selected $TARGET_DISK"

# Everything needed for the unattended install has passed validation. Only now
# create the guard and perform the destructive target erase.
: > "$STARTED" || fail "failed to create installation guard"

if ! /usr/sbin/diskutil eraseDisk APFS "Macintosh HD" GPT "$TARGET_DISK"; then
  fail "failed to erase $TARGET_DISK as APFS"
fi

count=0
while (( count < 60 )); do
  [ -d "$TARGET_VOLUME" ] && break
  count=$((count + 1))
  sleep 1
done

[ -d "$TARGET_VOLUME" ] || fail "target APFS volume did not mount"

rm -f \
  "$BOOTSTRAP_READY" \
  "$BOOTSTRAP_DONE" \
  "$BOOTSTRAP_FAILED"

# startosinstall prepares macOS without --installpackage. At the end of the
# prepare phase it signals our helper while the target is still mounted. The
# helper installs the known-good Setup Assistant package directly and stages
# the known-good account package for a LaunchDaemon to run on first real boot.
run_bootstrapper &
BOOTSTRAPPER_PID=$!

count=0
while (( count < 10 )); do
  [ -e "$BOOTSTRAP_READY" ] && break
  count=$((count + 1))
  sleep 1
done

[ -e "$BOOTSTRAP_READY" ] || {
  /bin/kill "$BOOTSTRAPPER_PID" 2>/dev/null || :
  fail "bootstrap helper failed to initialize"
}

ARGS=(
  --volume "$TARGET_VOLUME"
  --agreetolicense
  --nointeraction
  --rebootdelay 300
  --pidtosignal "$BOOTSTRAPPER_PID"
)

# Hand Apple's installer the Setup Assistant suppression package so it is applied
# after the system volume exists, not before. See the note at the --installpackage
# capability check above for why installing it from the prepare phase does not
# survive.
if [ -n "${SETUP_PACKAGE:-}" ] && [ -s "${SETUP_PACKAGE:-}" ]; then
  ARGS+=( --installpackage "$SETUP_PACKAGE" )
  echo "[log] queued $SETUP_PACKAGE for post-install application"
fi

# The account package still has to run on first boot, because creating a user
# requires the installed system's own tooling. It is staged and driven by the
# firstboot LaunchDaemon exactly as before.

echo "[log] starting online macOS installation on $TARGET_VOLUME"
echo "[log] bootstrap will be installed directly after startosinstall prepare phase"

# Keep Apple's installer diagnostics visible from the host while Recovery is
# occupied by startosinstall. Both host-visible logs are rolling snapshots so
# the 9p-backed shared-memory state can never grow without bound.
if [ -f /var/log/install.log ]; then
  snapshot_log "/var/log/install.log" "$APPLE_INSTALL_LOG" "$APPLE_INSTALL_LOG_LIMIT"
  mirror_log "/var/log/install.log" "$APPLE_INSTALL_LOG" "$APPLE_INSTALL_LOG_LIMIT" &
  echo "[log] mirroring Apple install log to $APPLE_INSTALL_LOG"
else
  echo "[log] Apple install log is not available"
fi

"$STARTOSINSTALL" "${ARGS[@]}" &
STARTOSINSTALL_PID=$!

while /bin/kill -0 "$STARTOSINSTALL_PID" 2>/dev/null; do

  if [ -e "$BOOTSTRAP_DONE" ]; then
    echo "[log] bootstrap installation succeeded; releasing startosinstall reboot delay"
    /bin/kill -USR1 "$STARTOSINSTALL_PID" 2>/dev/null || :
    break
  fi

  if [ -e "$BOOTSTRAP_FAILED" ]; then
    echo "[log] ERROR: bootstrap installation failed; cancelling prepared install"
    /bin/kill "$STARTOSINSTALL_PID" 2>/dev/null || :
    wait "$STARTOSINSTALL_PID" 2>/dev/null || :
    /bin/kill "$BOOTSTRAPPER_PID" 2>/dev/null || :
    fail "bootstrap installation failed"
  fi

  sleep 1
done

wait "$STARTOSINSTALL_PID"
rc=$?

/bin/kill "$BOOTSTRAPPER_PID" 2>/dev/null || :

if (( rc != 0 )); then
  rm -f "$STARTED"
  echo "[log] ERROR: startosinstall exited with status $rc"
  snapshot_log "$LOCAL_LOG" "$STATE_LOG" "$STATE_LOG_LIMIT"
  snapshot_log "/var/log/install.log" "$APPLE_INSTALL_LOG" "$APPLE_INSTALL_LOG_LIMIT"
  printf '%s\n' "$USAGE"
  exec /usr/libexec/recoveryosd
  exit "$rc"
fi

echo "[log] startosinstall completed its prepare phase; waiting for reboot"

while :; do
  sleep 60
done
