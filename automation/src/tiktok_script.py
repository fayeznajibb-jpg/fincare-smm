"""
Fincare TikTok Video Script Generator
=======================================
Generates a full TikTok script: hook timing, spoken words, on-screen text,
delivery notes — ready for filming or Remotion (Phase 2).
Uses Claude Haiku for personalization. ~$0.001 per script.
"""

import os
import json
import anthropic
from utils.logger import SecureLogger

logger = SecureLogger("tiktok_script")

SCRIPT_DURATION = 30  # seconds — optimal TikTok length for finance content

SCRIPT_STRUCTURE = {
    "hook":    {"seconds": "0–3s",   "goal": "Stop the scroll. Create instant curiosity or recognition."},
    "problem": {"seconds": "3–8s",   "goal": "Name the exact feeling/situation. Make them nod."},
    "insight": {"seconds": "8–22s",  "goal": "Deliver the reframe or data. The 'aha' moment."},
    "fincare": {"seconds": "22–27s", "goal": "Position Fincare as the solution, naturally."},
    "cta":     {"seconds": "27–30s", "goal": "One clear action. Comment, follow, or visit."},
}


def generate_tiktok_script(topic: dict, tiktok_caption: str) -> dict:
    """
    Generates a full TikTok video script using Claude Haiku.
    Returns structured script dict with spoken words + on-screen text per section.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("No ANTHROPIC_API_KEY — using caption as fallback script.")
        return _fallback_script(topic, tiktok_caption)

    pillar  = topic.get("content_pillar", "STORY")
    trigger = topic.get("emotional_trigger", "anxiety")
    hook    = topic.get("hook", "")
    stat    = topic.get("key_stat", "")

    prompt = f"""Write a 30-second TikTok script for Fincare (aifincare.com — AI investing co-pilot).

Topic: {topic.get('topic', '')}
Hook line: {hook}
Key stat: {stat}
Content pillar: {pillar}
Emotional trigger: {trigger}
Existing caption context: {tiktok_caption[:300]}

Write a script with EXACTLY this JSON structure. No markdown, raw JSON only:
{{
  "hook": {{
    "timing": "0-3s",
    "spoken": "exact words said out loud (under 10 words, punchy)",
    "on_screen_text": "text that appears on screen (under 6 words)",
    "delivery": "delivery note (e.g. 'pause after first word', 'fast and urgent')"
  }},
  "problem": {{
    "timing": "3-8s",
    "spoken": "exact words (1-2 sentences, relatable scenario)",
    "on_screen_text": "1-line text overlay",
    "delivery": "delivery note"
  }},
  "insight": {{
    "timing": "8-22s",
    "spoken": "the reframe or data point (2-3 sentences max)",
    "on_screen_text": "key stat or insight as text overlay",
    "delivery": "delivery note"
  }},
  "fincare": {{
    "timing": "22-27s",
    "spoken": "natural Fincare mention (1 sentence, not salesy)",
    "on_screen_text": "aifincare.com",
    "delivery": "delivery note"
  }},
  "cta": {{
    "timing": "27-30s",
    "spoken": "single clear CTA (comment / follow / visit)",
    "on_screen_text": "CTA text",
    "delivery": "delivery note"
  }},
  "total_word_count": 0,
  "filming_tip": "one practical tip for filming this specific script"
}}

Rules:
- Total spoken words must be under 80 (people speak ~130 words/minute, 30s = ~65 words)
- NEVER use AI giveaway words: delve, crucial, leverage, utilize, comprehensive
- Sound like a real person talking, not a corporate script
- The hook must create immediate recognition or curiosity"""

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        script = json.loads(raw)
        logger.success("TikTok script generated.")
        return script

    except Exception as e:
        logger.error(f"Script generation failed: {type(e).__name__} — using fallback.")
        return _fallback_script(topic, tiktok_caption)


def _fallback_script(topic: dict, caption: str) -> dict:
    """Returns a basic script structure when API is unavailable."""
    hook = topic.get("hook", "Your brain is your biggest investing risk.")
    return {
        "hook":    {"timing": "0-3s",   "spoken": hook[:60], "on_screen_text": hook[:30], "delivery": "direct, confident"},
        "problem": {"timing": "3-8s",   "spoken": "You've felt this before. Most investors have.", "on_screen_text": "Sound familiar?", "delivery": "relatable, calm"},
        "insight": {"timing": "8-22s",  "spoken": topic.get("key_stat", "The data tells a different story."), "on_screen_text": "The data:", "delivery": "measured, authoritative"},
        "fincare": {"timing": "22-27s", "spoken": "That's exactly what Fincare helps you manage.", "on_screen_text": "aifincare.com", "delivery": "natural, not salesy"},
        "cta":     {"timing": "27-30s", "spoken": "Follow for more.", "on_screen_text": "Follow ↑", "delivery": "quick, direct"},
        "total_word_count": 45,
        "filming_tip": "Film in portrait mode, good lighting, look directly at camera for the hook."
    }


def build_telegram_script_message(script: dict, topic: dict) -> str:
    """Formats the script as a clean Telegram message for easy filming reference."""
    sections = ["hook", "problem", "insight", "fincare", "cta"]

    lines = [
        f"<b>🎬 TIKTOK SCRIPT — {topic.get('content_pillar','')}</b>",
        f"<i>{topic.get('topic','')[:70]}</i>",
        f"{'─'*35}\n",
    ]

    for section in sections:
        data = script.get(section, {})
        timing   = data.get("timing", "")
        spoken   = data.get("spoken", "")
        on_screen= data.get("on_screen_text", "")
        delivery = data.get("delivery", "")
        lines += [
            f"<b>[{timing}] {section.upper()}</b>",
            f"🎙️ <i>{spoken}</i>",
            f"📱 On screen: <b>{on_screen}</b>",
            f"🎭 Delivery: {delivery}\n",
        ]

    lines += [
        f"{'─'*35}",
        f"📝 Words: ~{script.get('total_word_count', '?')} | Target: under 80",
        f"💡 Filming tip: {script.get('filming_tip', '')}",
        f"\n<i>Sound to search: see hashtag brief sent separately.</i>",
    ]
    return "\n".join(lines)


def send_tiktok_script(topic: dict, posts: dict):
    """Generates script and sends it to Telegram. Called from main.py after approval."""
    from src.telegram_bot import send_notification
    caption = posts.get("tiktok_caption", "")
    script  = generate_tiktok_script(topic, caption)
    message = build_telegram_script_message(script, topic)
    send_notification(message)
    logger.success("TikTok script sent to Telegram.")
    return script
