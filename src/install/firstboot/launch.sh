#!/bin/bash
set -u

PATH="/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

LABEL="com.dockur.macos.firstboot"
BASE="/Library/Application Support/macos-unattended"
ADMIN_PACKAGE="$BASE/admin.pkg"
DAEMON="/Library/LaunchDaemons/$LABEL.plist"
USER_DIR="/private/var/db/dslocal/nodes/Default/users"
ADMIN_PLIST="/private/var/db/dslocal/nodes/Default/groups/admin.plist"
LOG="/var/log/macos-unattended-firstboot.log"

log() {
  printf '%s\n' "[firstboot] $1" >> "$LOG" 2>/dev/null || :
}

fail() {
  log "ERROR: $1"
  exit 1
}

log "first-boot account setup started"

# Suppress Setup Assistant before anything else.
#
# This is the only reliable moment. The three earlier attempts all failed:
#
#   setup/postinstall wrote /private/var/db/.AppleSetupDone during the prepare
#   phase, and macOS rewrote the system volume afterwards, discarding it.
#
#   skipsetup.pkg delivered via --installpackage runs against the install target
#   ($3), but Setup Assistant reads /private/var/db on the running system, so the
#   marker landed where nothing looks.
#
#   firstboot.sh, as written, never touched the marker at all - it only created
#   the account and removed itself.
#
# So the markers go here, at RunAtLoad, before Setup Assistant starts: the
# .AppleSetupDone sentinel, the .skipbuddy flag inside the user template, and the
# DidSee* keys that recent macOS versions also consult before deciding to show
# the country picker.
SUPPRESS_LOG="/var/log/macos-unattended-setupassistant.log"

suppress_setup_assistant() {
  local marker="/private/var/db/.AppleSetupDone"

  /bin/mkdir -p /private/var/db 2>/dev/null || :
  if /usr/bin/touch "$marker"; then
    /usr/sbin/chown 0:0 "$marker" 2>/dev/null || :
    /bin/chmod 0644 "$marker" 2>/dev/null || :
    log "wrote $marker"
  else
    log "ERROR: could not write $marker"
  fi

  # The user template flag stops the per-user "buddy" from running at first login.
  local skipbuddy="/Library/User Template/English.lproj/.skipbuddy"
  /bin/mkdir -p "${skipbuddy%/*}" 2>/dev/null || :
  /usr/bin/touch "$skipbuddy" 2>/dev/null && log "wrote $skipbuddy" || :

  # macOS 11+ consults these before showing the country picker. Write them for
  # both the template and the account being created, because which one Setup
  # Assistant reads depends on the version.
  local plist="/Library/User Template/Non_localized/Library/Preferences/com.apple.SetupAssistant.plist"
  /bin/mkdir -p "${plist%/*}" 2>/dev/null || :
  for key in DidSeeCloudSetup DidSeeSiriSetup DidSeeTrueToneSetup DidSeeDisplayTrueTone \
             DidSeePrivacy DidSeeAccessibility DidSeeScreenTime DidSeeSetupAssistant \
             DidSeeAppStore DidSeeTouchIDSetup DidSeeiCloudAnalyticsOptIn; do
    /usr/bin/defaults write "$plist" "$key" -bool true 2>/dev/null || :
  done
  /usr/bin/defaults write "$plist" "LastSeenCloudProductVersion" -string "14.0" 2>/dev/null || :
  /usr/bin/defaults write "$plist" "LastSeenBuddyBuildVersion" -string "23A344" 2>/dev/null || :
  log "wrote Setup Assistant preference keys"
} >> "$SUPPRESS_LOG" 2>&1

suppress_setup_assistant

[ -s "$ADMIN_PACKAGE" ] || fail "prebuilt account package is missing"
[ -x /usr/sbin/installer ] || fail "installer is not available"

count=0
while (( count < 300 )); do
  if [ -d "$USER_DIR" ] && [ -f "$ADMIN_PLIST" ]; then
    break
  fi
  count=$((count + 1))
  sleep 1
done

[ -d "$USER_DIR" ] || fail "local user database did not become available"
[ -f "$ADMIN_PLIST" ] || fail "admin group database did not become available"

log "local user database is available; installing prebuilt account package"

if ! /usr/sbin/installer -pkg "$ADMIN_PACKAGE" -target / -verboseR >> "$LOG" 2>&1; then
  fail "prebuilt account package installation failed"
fi

log "prebuilt account package installed successfully"

# Write the markers again now that the account exists, this time into the
# account's own preferences. The template version covers users created later;
# this covers the one just installed, which is the one Setup Assistant would
# otherwise greet at first login.
apply_suppression_to_account() {
  local user_plist
  user_plist=$(/bin/ls -d /Users/*/Library/Preferences 2>/dev/null | /usr/bin/head -1 || :)
  [ -n "$user_plist" ] || { log "no user preferences directory yet"; return 0; }

  local plist="$user_plist/com.apple.SetupAssistant.plist"
  for key in DidSeeCloudSetup DidSeeSiriSetup DidSeeTrueToneSetup DidSeeDisplayTrueTone \
             DidSeePrivacy DidSeeAccessibility DidSeeScreenTime DidSeeSetupAssistant \
             DidSeeAppStore DidSeeTouchIDSetup DidSeeiCloudAnalyticsOptIn; do
    /usr/bin/defaults write "$plist" "$key" -bool true 2>/dev/null || :
  done
  /usr/bin/defaults write "$plist" "LastSeenCloudProductVersion" -string "14.0" 2>/dev/null || :
  /usr/bin/defaults write "$plist" "LastSeenBuddyBuildVersion" -string "23A344" 2>/dev/null || :

  # The per-user sentinel that suppresses the setup "buddy" at login.
  local skipbuddy="$user_plist/../.skipbuddy"
  /usr/bin/touch "$skipbuddy" 2>/dev/null || :

  /usr/sbin/chown -R 501:20 "$user_plist" 2>/dev/null || :
  log "applied Setup Assistant suppression to $user_plist"
} >> "$SUPPRESS_LOG" 2>&1

apply_suppression_to_account

if ! /bin/rm -f "$DAEMON"; then
  fail "failed to remove first-boot LaunchDaemon"
fi

if ! /bin/rm -rf "$BASE"; then
  fail "failed to remove first-boot staging directory"
fi

log "first-boot account setup completed"
exit 0
