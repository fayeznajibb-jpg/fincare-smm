"""
Fincare Post Scheduler
======================
Manages optimal posting times per platform.
Uses industry benchmarks as defaults. Learns from performance data over time.
"""

import os
import json
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("post_scheduler")

SCHEDULE_PATH = "logs/posting_schedule.json"

# Industry benchmark posting windows (UTC)
BENCHMARK_SCHEDULE = {
    "last_updated": "2026-04-01",
    "source": "benchmark",
    "linkedin_company": {
        "days": [0, 1, 2, 3, 4],
        "hour_utc_start": 8,
        "hour_utc_end": 10,
        "note": "Tue–Thu 8–10am UTC performs best for B2B finance content"
    },
    "threads": {
        "days": [0, 1, 2, 3, 4, 5, 6],
        "hour_utc_start": 19,
        "hour_utc_end": 21,
        "note": "Evening 7–9pm UTC — highest Threads engagement window"
    },
    "tiktok": {
        "days": [0, 1, 2, 3, 4, 5, 6],
        "hour_utc_start": 19,
        "hour_utc_end": 21,
        "note": "7–9pm UTC and 6–8am UTC are peak TikTok windows"
    },
}


def init_schedule_if_missing():
    """Creates the schedule file with benchmark data if it doesn't exist yet."""
    os.makedirs("logs", exist_ok=True)
    if not os.path.exists(SCHEDULE_PATH):
        save_schedule(BENCHMARK_SCHEDULE)
        logger.info("Posting schedule initialised from benchmarks.")


def load_schedule() -> dict:
    """Loads schedule from file. Falls back to benchmark if file is missing or corrupt."""
    try:
        with open(SCHEDULE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        logger.warning("Schedule file missing or corrupt — using benchmark defaults.")
        return BENCHMARK_SCHEDULE


def save_schedule(schedule: dict):
    """Saves schedule dict to file."""
    os.makedirs("logs", exist_ok=True)
    with open(SCHEDULE_PATH, "w") as f:
        json.dump(schedule, f, indent=2)


def get_optimal_schedule() -> dict:
    """Returns the full active posting schedule."""
    schedule = load_schedule()
    for platform, window in schedule.items():
        if isinstance(window, dict) and "hour_utc_start" in window:
            logger.info(f"{platform}: {window['hour_utc_start']}–{window['hour_utc_end']} UTC | {window.get('note','')}")
    return schedule


def is_optimal_time_now(platform: str) -> bool:
    """Returns True if current UTC time falls within the platform's optimal window."""
    schedule = load_schedule()
    window = schedule.get(platform)
    if not window or not isinstance(window, dict):
        return True  # Default: always post if no schedule data
    now = datetime.utcnow()
    day_ok  = now.weekday() in window.get("days", list(range(7)))
    hour_ok = window["hour_utc_start"] <= now.hour < window["hour_utc_end"]
    return day_ok and hour_ok


def generate_cron_string(hour_utc: int, days: list) -> str:
    """Converts a schedule entry into a cron expression string."""
    if days == list(range(7)):
        day_str = "*"
    elif days == [0, 1, 2, 3, 4]:
        day_str = "1-5"
    else:
        day_str = ",".join(str(d + 1) for d in days)  # cron uses 1=Mon
    return f"0 {hour_utc} * * {day_str}"


def update_schedule_from_performance(platform: str, best_hour_utc: int):
    """
    Updates the schedule with a learned optimal hour from performance data.
    Called by performance_tracker.py once enough data exists.
    """
    schedule = load_schedule()
    if platform not in schedule:
        return
    current = schedule[platform].get("hour_utc_start", 8)
    if abs(current - best_hour_utc) > 2:
        logger.warning(f"Performance data suggests {platform} posts at {best_hour_utc}h UTC "
                       f"(currently {current}h). Consider updating the schedule.")
    schedule[platform]["hour_utc_start"] = best_hour_utc
    schedule[platform]["hour_utc_end"]   = best_hour_utc + 2
    schedule[platform]["source"]         = "learned"
    schedule[platform]["last_updated"]   = datetime.now().strftime("%Y-%m-%d")
    save_schedule(schedule)
    logger.success(f"Schedule updated for {platform}: now {best_hour_utc}h UTC")
