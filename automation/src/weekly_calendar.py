"""
Fincare Weekly Content Calendar
=================================
Every Monday morning, generates a full 5-day content plan and sends it
to Telegram. You see the whole week before it runs — override any day
by tapping the day and typing your own idea.
Zero API cost — pure logic from existing pillars + hook library.
"""

import os
import json
from datetime import datetime, timedelta
from utils.logger import SecureLogger

logger = SecureLogger("weekly_calendar")

CALENDAR_PATH = "logs/weekly_calendar.json"

# Day plans pulled from fallback topics + pillar rotation
# Each day maps to a pillar (fixed Mon–Fri schedule)
DAY_PILLARS = {
    0: "STORY",    # Monday
    1: "DATA",     # Tuesday
    2: "OPINION",  # Wednesday
    3: "QUESTION", # Thursday
    4: "INSIGHT",  # Friday
}

# Curated weekly topic bank — rotates by ISO week number
# 4 weeks of variety, then repeats with fresh hooks
TOPIC_BANK = [
    # Week A
    [
        {"topic": "The 2am portfolio check spiral",             "trigger": "anxiety",        "hook": "It's 2am. You're checking your portfolio again. Here's what that's costing you."},
        {"topic": "The stat that predicts your investing fate", "trigger": "overconfidence", "hook": "One number predicts your investing success better than IQ. Most people don't know it."},
        {"topic": "Why financial news is making you poorer",    "trigger": "fear",           "hook": "Unpopular opinion: the more financial news you consume, the worse you invest."},
        {"topic": "What do you do when you're down 20%?",      "trigger": "fear",           "hook": "Your portfolio is down 20%. What do you actually do? No judgment — genuine question."},
        {"topic": "The 10-second rule before any trade",       "trigger": "anxiety",        "hook": "Before your next trade, try this 10-second rule. It's embarrassingly simple."},
    ],
    # Week B
    [
        {"topic": "The FOMO sell — a story",                   "trigger": "FOMO",           "hook": "Everyone in the group chat was buying. So were you. Here's what happened next."},
        {"topic": "71% of retail investors lose on timing",    "trigger": "overconfidence", "hook": "71% of investors who time the market lose money. You probably think you're in the 29%."},
        {"topic": "The market is not your enemy",              "trigger": "fear",           "hook": "Unpopular opinion: the market isn't trying to hurt you. Your emotions are."},
        {"topic": "What's your biggest money regret?",         "trigger": "shame",          "hook": "What's the investing decision you still think about? Most people have one they never share."},
        {"topic": "The compounding patience mental model",     "trigger": "anxiety",        "hook": "The most powerful investing concept isn't compound interest. It's compound patience."},
    ],
    # Week C
    [
        {"topic": "The Sunday night dread before markets open","trigger": "anxiety",        "hook": "Sunday 9pm. Markets open tomorrow. That feeling in your chest? Let's talk about it."},
        {"topic": "How panic selling performs vs holding",     "trigger": "fear",           "hook": "We tracked what happened to every investor who panic-sold in 2020. The data is brutal."},
        {"topic": "Dollar cost averaging isn't boring",        "trigger": "FOMO",           "hook": "Unpopular opinion: DCA isn't boring. It's the only strategy that consistently beats FOMO."},
        {"topic": "How often do you check your portfolio?",    "trigger": "anxiety",        "hook": "Honest question: how many times a day do you check your portfolio? And what does that number say?"},
        {"topic": "The loss aversion trap",                    "trigger": "fear",           "hook": "Your brain weighs losses twice as heavily as gains. Here's how that's wrecking your returns."},
    ],
    # Week D
    [
        {"topic": "The friend who went all-in — a story",      "trigger": "FOMO",           "hook": "Your friend went all-in on one stock. It doubled. Here's the part they're not telling you."},
        {"topic": "Overconfident investors trade 45% more",    "trigger": "overconfidence", "hook": "Overconfident investors trade 45% more and earn 3.7% less. Every single year."},
        {"topic": "AI won't replace your investing instincts", "trigger": "anxiety",        "hook": "Unpopular opinion: AI won't make you a better investor. Knowing yourself will."},
        {"topic": "What emotion drives your worst trades?",    "trigger": "shame",          "hook": "Fear, FOMO, or shame — which emotion has cost you the most? I want to know."},
        {"topic": "The 'set and forget' mental model",         "trigger": "anxiety",        "hook": "The investors who did the least in 2020 made the most. Here's the mental model behind it."},
    ],
]


def get_week_plan() -> list[dict]:
    """
    Returns the 5-day plan for the current week.
    Rotates topic bank by ISO week number.
    """
    iso_week  = datetime.now().isocalendar()[1]
    bank_idx  = iso_week % len(TOPIC_BANK)
    week_bank = TOPIC_BANK[bank_idx]

    monday = datetime.now() - timedelta(days=datetime.now().weekday())
    plan   = []

    for day_offset, (weekday, pillar) in enumerate(DAY_PILLARS.items()):
        date    = monday + timedelta(days=day_offset)
        topic   = week_bank[day_offset]
        plan.append({
            "day":       date.strftime("%A"),
            "date":      date.strftime("%b %d"),
            "pillar":    pillar,
            "topic":     topic["topic"],
            "trigger":   topic["trigger"],
            "hook":      topic["hook"],
            "override":  None,   # Set if user sends a custom idea for this day
            "status":    "planned",
        })

    return plan


def save_calendar(plan: list):
    """Saves this week's plan to logs/ for reference and override tracking."""
    os.makedirs("logs", exist_ok=True)
    week = datetime.now().isocalendar()[1]
    data = {"week": week, "year": datetime.now().year, "generated_at": datetime.now().isoformat(), "days": plan}
    with open(CALENDAR_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_calendar() -> list:
    """Loads this week's saved calendar. Returns empty list if not found."""
    try:
        with open(CALENDAR_PATH) as f:
            data = json.load(f)
        saved_week = data.get("week")
        current_week = datetime.now().isocalendar()[1]
        if saved_week == current_week:
            return data.get("days", [])
    except Exception:
        pass
    return []


def mark_day_posted(day_name: str):
    """Marks a day as posted in the calendar. Called from main.py."""
    plan = load_calendar()
    for day in plan:
        if day["day"] == day_name:
            day["status"] = "posted"
            break
    save_calendar(plan)


def build_telegram_calendar_message(plan: list) -> str:
    """Formats the weekly plan as a Telegram message."""
    iso_week = datetime.now().isocalendar()[1]
    lines = [
        f"<b>📅 FINCARE — WEEK {iso_week} CONTENT PLAN</b>",
        f"<i>Here's what's scheduled. Reply with a day name + your idea to override any day.</i>",
        f"{'─'*35}\n",
    ]

    pillar_emojis = {"STORY": "📖", "DATA": "📊", "OPINION": "💬", "QUESTION": "❓", "INSIGHT": "💡"}

    for day in plan:
        emoji   = pillar_emojis.get(day["pillar"], "📌")
        status  = "✅ Posted" if day["status"] == "posted" else "⏳ Scheduled"
        override= f"\n   ✏️ Override: <i>{day['override']}</i>" if day["override"] else ""
        lines.append(
            f"{emoji} <b>{day['day']} {day['date']}</b> — {day['pillar']} | {day['trigger'].upper()}\n"
            f"   📌 {day['topic']}\n"
            f"   🎯 Hook: <i>{day['hook'][:80]}...</i>\n"
            f"   {status}{override}\n"
        )

    lines += [
        f"{'─'*35}",
        "<i>Reply: \"Wednesday: your topic idea here\" to override any day.</i>",
    ]
    return "\n".join(lines)


def send_weekly_calendar():
    """Generates and sends the weekly calendar. Called every Monday by GitHub Actions."""
    from src.telegram_bot import send_notification
    logger.step("Generating weekly content calendar...")
    plan = get_week_plan()
    save_calendar(plan)
    message = build_telegram_calendar_message(plan)
    send_notification(message)
    logger.success(f"Weekly calendar sent — {len(plan)} days planned.")


def get_todays_topic_override() -> dict | None:
    """
    Returns the override topic for today if one was set.
    Called by researcher.py to check if user manually set today's topic.
    """
    plan = load_calendar()
    today = datetime.now().strftime("%A")
    for day in plan:
        if day["day"] == today and day.get("override"):
            return {"topic": day["override"], "user_override": True}
    return None
