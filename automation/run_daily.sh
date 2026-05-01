#!/bin/bash
# Fincare SMM — Daily Pipeline
# Runs: research → write posts → render video → send Telegram briefing
# Scheduled via crontab at 8am daily

# ── Paths ────────────────────────────────────────────────────────────────────
PYTHON="/Users/fayeznajib/.pyenv/versions/3.11.9/bin/python"
PROJECT="/Users/fayeznajib/Downloads/Fincare SMM /automation"
LOG_DIR="$PROJECT/logs"
LOG_FILE="$LOG_DIR/daily_$(date +%Y%m%d).log"

# ── Setup ─────────────────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"
cd "$PROJECT" || exit 1

# Make sure PATH includes Node (for Remotion) and Homebrew (for ffmpeg)
export PATH="/usr/local/bin:/opt/homebrew/bin:/Users/fayeznajib/.pyenv/versions/3.11.9/bin:$PATH"

# ── Pause background bot to avoid Telegram token conflict ─────────────────────
# Both main.py and run_bot.py use getUpdates — only one can poll at a time.
# launchctl unload stops the shell wrapper but not the Python child process,
# so we explicitly kill any lingering run_bot.py processes too.
launchctl unload ~/Library/LaunchAgents/com.fincare.bot.plist 2>/dev/null
pkill -f "python.*run_bot.py" 2>/dev/null
sleep 3

# ── Run ───────────────────────────────────────────────────────────────────────
echo "=== Fincare SMM Daily Run: $(date) ===" >> "$LOG_FILE"
"$PYTHON" src/main.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "Pipeline failed (exit $EXIT_CODE) — check $LOG_FILE" >> "$LOG_FILE"
fi

echo "=== Done: $(date) ===" >> "$LOG_FILE"

# ── Resume background bot ─────────────────────────────────────────────────────
launchctl load ~/Library/LaunchAgents/com.fincare.bot.plist 2>/dev/null
