#!/bin/sh
# Moves the scheduled jobs from cron → ★launchd★.
#
# ⛔ Why (measured 2026-08-31)
# ------------------------------------------------------------------
# Two cron jobs ★never once ran for two straight days★ after 08-29. The registration was fine — the
# machine was just asleep at 09:00. macOS's `cron` ★doesn't catch up★ on a run it missed while asleep.
# `launchd`'s `StartCalendarInterval` ★runs a missed job once, on wake★.
#
# For a laptop user this isn't the exception — it's ★the normal state★ — a day where the laptop is
# awake at 09:00 is rare. So cron doesn't fit a team rollout.
#
# ⛔ This script ★deletes★ the cron lines (running both means judging the same day twice).
#    It backs up crontab before deleting — deleting someone else's config is the worst possible failure.
set -e
DIR=$(cd "$(dirname "$0")/.." && pwd)
LA="$HOME/Library/LaunchAgents"
mkdir -p "$LA"
# ⛔ the brain says where its home is (§store.brain_home) — this used to write into `~/.claude/brain`
BHOME=$(env PYTHONPATH="$DIR" python3 -c 'from brain import store; print(store.brain_home())')
mkdir -p "$BHOME"

plist() {  # $1=name $2=script $3=hour $4=minute
  cat > "$LA/com.local-brain.$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.local-brain.$1</string>
  <key>ProgramArguments</key><array><string>$DIR/bin/$2</string></array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$3</integer><key>Minute</key><integer>$4</integer>
  </dict>
  <!-- ⛔ catching a missed run up on wake is the whole reason this file exists -->
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$BHOME/$1.launchd.log</string>
  <key>StandardErrorPath</key><string>$BHOME/$1.launchd.log</string>
</dict></plist>
EOF
  launchctl unload "$LA/com.local-brain.$1.plist" 2>/dev/null || true
  launchctl load "$LA/com.local-brain.$1.plist"
  echo "  registered com.local-brain.$1  ($3:$4)"
}

echo "moving to launchd (catches a missed run up on wake)"
plist vec-daily   brain-vec-daily   9 0
plist rules-daily brain-rules-daily 9 30

# ★remove the cron lines — back up first★ (this checkout's own scripts, wherever it was cloned)
if crontab -l 2>/dev/null | grep -qF "$DIR/bin/brain-"; then
  BK="$BHOME/crontab.backup.$(date +%Y%m%d%H%M%S)"
  crontab -l > "$BK"
  echo "  crontab backed up → $BK"
  crontab -l | grep -vF "$DIR/bin/brain-" | crontab -
  echo "  removed the brain lines from crontab (so it doesn't run twice)"
fi
echo
echo "check:  launchctl list | grep local-brain"
echo "⛔ verify with ★the log★, not the registration — schedules in ./bin/brain status"
