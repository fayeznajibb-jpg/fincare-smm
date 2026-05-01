#!/bin/bash
# Fincare Founder Bot — keeps run_bot.py alive in the background.
# Run this once at login (or add to Login Items).
# If the bot crashes it auto-restarts after 10 seconds.

PYTHON="/Users/fayeznajib/.pyenv/versions/3.11.9/bin/python"
PROJECT="/Users/fayeznajib/Downloads/Fincare SMM /automation"
LOG_DIR="$PROJECT/logs"
LOG_FILE="$LOG_DIR/bot_$(date +%Y%m%d).log"

export PATH="/usr/local/bin:/opt/homebrew/bin:/Users/fayeznajib/.pyenv/versions/3.11.9/bin:$PATH"

mkdir -p "$LOG_DIR"
cd "$PROJECT" || exit 1

echo "=== Founder Bot started: $(date) ===" >> "$LOG_FILE"

while true; do
    "$PYTHON" run_bot.py >> "$LOG_FILE" 2>&1
    EXIT=$?
    echo "Bot exited (code $EXIT) — restarting in 10s... $(date)" >> "$LOG_FILE"
    sleep 10
done
