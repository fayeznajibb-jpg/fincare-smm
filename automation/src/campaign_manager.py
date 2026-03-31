"""
Fincare Campaign Manager
=========================
Lets you prepare posts in advance for planned events:
product launches, milestones, holidays, announcements.

On the scheduled date, the campaign overrides the daily pipeline —
no research, no auto-writing. Your prepared content goes out instead.

How to create a campaign:
  Send this to your Telegram bot (anytime, even outside approval windows):
  CAMPAIGN 2026-05-01 Fincare beta launch — we hit 1000 users

The bot picks it up, writes the posts using Haiku, sends a preview,
you approve it, and it sits waiting until that date.
"""

import os
import json
import anthropic
from datetime import datetime, date
from utils.logger import SecureLogger

logger = SecureLogger("campaign_manager")

CAMPAIGNS_PATH = "logs/campaigns.json"


# ─────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────

def load_campaigns() -> list:
    try:
        with open(CAMPAIGNS_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def save_campaigns(campaigns: list):
    os.makedirs("logs", exist_ok=True)
    with open(CAMPAIGNS_PATH, "w") as f:
        json.dump(campaigns, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────
# CAMPAIGN CHECK (called daily from main.py)
# ─────────────────────────────────────────

def check_for_campaign_today() -> dict | None:
    """
    Called at the start of main.py run().
    If today has an approved campaign, returns it so main.py skips
    research + writing and uses the campaign content directly.
    """
    today_str = date.today().isoformat()
    campaigns = load_campaigns()

    for c in campaigns:
        if c.get("date") == today_str and c.get("status") == "approved":
            logger.success(f"Campaign found for today: {c['name']}")
            return c

    return None


def mark_campaign_posted(campaign_date: str):
    """Marks a campaign as posted after the daily run completes."""
    campaigns = load_campaigns()
    for c in campaigns:
        if c.get("date") == campaign_date:
            c["status"] = "posted"
            c["posted_at"] = datetime.now().isoformat()
            break
    save_campaigns(campaigns)
    logger.info(f"Campaign {campaign_date} marked as posted.")


# ─────────────────────────────────────────
# CAMPAIGN CREATION
# ─────────────────────────────────────────

def parse_campaign_command(text: str) -> dict | None:
    """
    Parses a CAMPAIGN command from Telegram text.
    Format: CAMPAIGN YYYY-MM-DD campaign name / description
    Example: CAMPAIGN 2026-05-01 Fincare beta launch milestone
    Returns {"date": str, "name": str} or None if invalid.
    """
    parts = text.strip().split(" ", 2)
    if len(parts) < 3:
        return None
    try:
        campaign_date = datetime.strptime(parts[1], "%Y-%m-%d").date()
        if campaign_date < date.today():
            return None   # Past date
        return {"date": parts[1], "name": parts[2].strip()}
    except ValueError:
        return None


def write_campaign_posts(name: str, campaign_date: str) -> tuple[dict, dict]:
    """
    Uses Claude Haiku to write posts for a campaign.
    Returns (topic_dict, posts_dict) — same structure as the daily pipeline.
    Cost: ~$0.002 per campaign (Haiku).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    # Build the topic dict
    topic = {
        "topic":           name,
        "angle":           f"Fincare milestone/announcement: {name}",
        "hook":            f"Something big is happening at Fincare.",
        "key_stat":        "aifincare.com",
        "emotional_trigger": "FOMO",
        "why_today":       f"Scheduled campaign: {name} on {campaign_date}",
        "content_pillar":  "STORY",
        "is_campaign":     True,
        "campaign_date":   campaign_date,
    }

    if not api_key:
        logger.warning("No API key — using placeholder campaign posts.")
        return topic, _placeholder_posts(name)

    prompt = f"""You are the social media copywriter for Fincare (aifincare.com — AI investing co-pilot).

Write posts for a planned campaign/announcement: "{name}"

This is a special post — not the usual behavioral finance content.
Tone: exciting but grounded. Celebrate the milestone/announcement without hype.
Always end with a CTA to visit aifincare.com.
NEVER use: delve, crucial, leverage, comprehensive, game-changer.

Return ONLY raw JSON with these exact keys:
{{
  "linkedin_company": "company page post under 700 chars — sharp, proud, inviting",
  "instagram_caption": "caption under 2200 chars — visual storytelling, hook first 125 chars, CTA at end",
  "tiktok_caption": "caption under 2200 chars — POV or behind-the-scenes angle",
  "threads_post": "under 500 chars — punchy, conversational, ends with question or CTA",
  "instagram_slide_texts": ["Slide 1 hook", "Slide 2", "Slide 3", "Slide 4", "Slide 5", "Slide 6", "Slide 7 CTA"],
  "hashtags_linkedin": "#Fincare #AIFinance #InvestingApp #FinTech #Milestone",
  "hashtags_instagram": "#fincare #aifinance #investingapp #fintech #milestone #personalfinance",
  "hashtags_tiktok": "#fincare #aifinance #fintech #milestone"
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        posts = json.loads(raw)
        logger.success(f"Campaign posts written for: {name}")
        return topic, posts
    except Exception as e:
        logger.error(f"Campaign write failed: {type(e).__name__} — using placeholder.")
        return topic, _placeholder_posts(name)


def _placeholder_posts(name: str) -> dict:
    return {
        "linkedin_company":    f"Big news from Fincare: {name}.\n\nVisit aifincare.com to learn more.",
        "instagram_caption":   f"{name}\n\nWe built Fincare to be the calm in every investor's storm. Today, that mission takes a new step.\n\nVisit aifincare.com 🔗",
        "tiktok_caption":      f"POV: Something big just happened at Fincare.\n\n{name}\n\naifincare.com",
        "threads_post":        f"{name} — visit aifincare.com to see what we've been building.",
        "instagram_slide_texts": [name, "We started with a mission.", "Help investors master their minds.", "Not just their portfolios.", "Today that mission grows.", "Thank you for being part of it.", "Visit aifincare.com"],
        "hashtags_linkedin":   "#Fincare #AIFinance #InvestingApp #FinTech",
        "hashtags_instagram":  "#fincare #aifinance #investingapp #fintech",
        "hashtags_tiktok":     "#fincare #aifinance #fintech",
    }


# ─────────────────────────────────────────
# FULL CREATION FLOW
# ─────────────────────────────────────────

def create_campaign(date_str: str, name: str) -> bool:
    """
    Full campaign creation flow:
    1. Write posts with Haiku
    2. Save as 'pending_approval'
    3. Send Telegram preview for approval
    Returns True if created successfully.
    """
    from src.telegram_bot import send_notification, _api

    logger.step(f"Creating campaign: {name} on {date_str}")
    send_notification(f"✍️ <b>Writing campaign posts for:</b>\n<i>{name} — {date_str}</i>\nGive me a moment...")

    topic, posts = write_campaign_posts(name, date_str)

    # Build preview
    linkedin_c = posts.get("linkedin_company", "")[:200]
    threads    = posts.get("threads_post", "")[:200]
    preview_msg = (
        f"<b>📅 CAMPAIGN PREVIEW</b>\n"
        f"{'─'*35}\n"
        f"<b>Name:</b> {name}\n"
        f"<b>Date:</b> {date_str}\n\n"
        f"<b>LinkedIn (company):</b>\n<i>{linkedin_c}...</i>\n\n"
        f"<b>Threads:</b>\n<i>{threads}</i>\n\n"
        f"{'─'*35}\n"
        f"<i>Tap ✅ to save this campaign or ❌ to cancel.</i>"
    )

    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Save Campaign", "callback_data": f"camp_save_{date_str}"},
        {"text": "❌ Cancel",        "callback_data": f"camp_cancel_{date_str}"},
    ]]}

    try:
        _api(token, "sendMessage", {
            "chat_id":      chat_id,
            "text":         preview_msg,
            "parse_mode":   "HTML",
            "reply_markup": keyboard,
        })
    except Exception as e:
        logger.error(f"Failed to send campaign preview: {type(e).__name__}")
        return False

    # Wait for save/cancel decision (30 min)
    import time, uuid
    from utils.validators import validate_telegram_chat_id
    elapsed, last_id = 0, 0
    while elapsed < 1800:
        time.sleep(20)
        elapsed += 20
        try:
            result = _api(token, "getUpdates", {"offset": last_id + 1, "timeout": 15,
                                                 "allowed_updates": ["callback_query"]})
        except Exception:
            continue
        for update in result.get("result", []):
            last_id = update["update_id"]
            if "callback_query" not in update:
                continue
            cb      = update["callback_query"]
            from_id = str(cb.get("from", {}).get("id", ""))
            data    = cb.get("data", "")
            if not validate_telegram_chat_id(from_id, chat_id):
                continue
            if data == f"camp_save_{date_str}":
                _save_campaign_entry(date_str, name, topic, posts)
                send_notification(f"✅ <b>Campaign saved!</b>\n📅 <b>{name}</b> will go out on <b>{date_str}</b>.\nIt will override the daily pipeline on that date.")
                logger.success(f"Campaign saved: {name} on {date_str}")
                return True
            elif data == f"camp_cancel_{date_str}":
                send_notification(f"❌ Campaign cancelled.")
                return False

    logger.warning("Campaign creation timed out.")
    return False


def _save_campaign_entry(date_str: str, name: str, topic: dict, posts: dict):
    campaigns = load_campaigns()
    # Remove any existing campaign for same date
    campaigns = [c for c in campaigns if c.get("date") != date_str]
    campaigns.append({
        "date":       date_str,
        "name":       name,
        "status":     "approved",
        "created_at": datetime.now().isoformat(),
        "topic":      topic,
        "posts":      posts,
    })
    campaigns.sort(key=lambda c: c["date"])
    save_campaigns(campaigns)


# ─────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────

def list_upcoming_campaigns() -> list:
    """Returns all campaigns scheduled from today onwards."""
    today     = date.today().isoformat()
    campaigns = load_campaigns()
    return [c for c in campaigns if c.get("date", "") >= today and c.get("status") != "posted"]


def get_campaign_summary_text() -> str:
    """Returns a short text summary of upcoming campaigns for the weekly calendar."""
    upcoming = list_upcoming_campaigns()
    if not upcoming:
        return ""
    lines = ["\n<b>📅 UPCOMING CAMPAIGNS:</b>"]
    for c in upcoming[:5]:
        lines.append(f"  • {c['date']}: {c['name']} ({c['status']})")
    return "\n".join(lines)
