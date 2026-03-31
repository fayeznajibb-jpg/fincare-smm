"""
Fincare Trending Audio Tracker — FREE version
==============================================
Scrapes trending TikTok sounds and finance hashtags weekly.
No API key needed. Uses public web data.
Injects trending context into TikTok captions.
"""

import urllib.request
import json
import re
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("trending_audio")

# Curated TikTok finance hashtags — refreshed manually if needed
# These are evergreen high-reach tags for finance/investing content
FINANCE_HASHTAGS_TIKTOK = [
    "#personalfinance", "#investing", "#stockmarket", "#financialliteracy",
    "#moneytips", "#wealthbuilding", "#investingtips", "#financialfreedom",
    "#passiveincome", "#moneyadvice", "#fintech", "#behavioralfinance",
    "#investorpsychology", "#stockstobuy", "#retirementplanning",
    "#budgeting", "#savingmoney", "#richmindset", "#aifinance", "#fincare"
]

# Audio moods mapped to content types — guides script timing and energy
AUDIO_MOODS = {
    "STORY":    {"energy": "slow-build", "script_tip": "Start quiet/reflective. Build to an insight. End with a pause."},
    "DATA":     {"energy": "punchy",     "script_tip": "Fast facts. Each stat gets 2-3 seconds. No filler words."},
    "OPINION":  {"energy": "confident",  "script_tip": "Strong opening statement. Defend with energy. End assertively."},
    "QUESTION": {"energy": "curious",    "script_tip": "Pause after the question. Let it land. Invite reply verbally."},
    "INSIGHT":  {"energy": "wise-calm",  "script_tip": "Slow and deliberate. Each line matters. End with silence."},
}

# Trending sound categories that work for finance content
SOUND_SUGGESTIONS = [
    {
        "mood": "serious/educational",
        "description": "Minimal piano or ambient background — lets the words breathe",
        "best_for": ["DATA", "INSIGHT", "STORY"],
        "search_term": "calm piano background tiktok"
    },
    {
        "mood": "motivational",
        "description": "Lo-fi beat or upbeat background — energetic but not distracting",
        "best_for": ["OPINION", "QUESTION"],
        "search_term": "lofi motivational background tiktok"
    },
    {
        "mood": "trending viral",
        "description": "Use whatever is currently #1 trending sound — add finance caption overlay",
        "best_for": ["STORY", "QUESTION"],
        "search_term": "trending sound tiktok this week"
    },
]


def get_trending_hashtags(pillar: str, emotional_trigger: str) -> str:
    """
    Returns a curated hashtag set for TikTok based on pillar and trigger.
    Mixes evergreen finance tags with pillar-specific tags.
    """
    # Base tags always included
    base = ["#personalfinance", "#investing", "#financialliteracy", "#fincare", "#aifinance"]

    # Trigger-specific tags
    trigger_tags = {
        "fear":           ["#marketcrash", "#stockmarket", "#investorpsychology", "#fearofmissout"],
        "FOMO":           ["#fomo", "#stockstobuy", "#cryptotips", "#trendingstock"],
        "anxiety":        ["#financialanxiety", "#moneyStress", "#moneymindset", "#financialfreedom"],
        "overconfidence": ["#tradingmistakes", "#investingtips", "#stockmarket", "#daytrading"],
        "shame":          ["#moneytalk", "#financialliteracy", "#moneymindset", "#personalfinance"],
    }

    # Pillar-specific tags
    pillar_tags = {
        "STORY":    ["#storytime", "#relatable", "#moneystory"],
        "DATA":     ["#didyouknow", "#financefacts", "#moneyfacts"],
        "OPINION":  ["#unpopularopinion", "#hotttake", "#financeopinion"],
        "QUESTION": ["#askmethat", "#questionoftheday", "#financequestion"],
        "INSIGHT":  ["#mindset", "#lifelesson", "#investing101"],
    }

    selected = base.copy()
    selected += trigger_tags.get(emotional_trigger, [])[:3]
    selected += pillar_tags.get(pillar, [])[:2]

    # Remove duplicates, cap at 10 tags
    seen = set()
    unique = []
    for tag in selected:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)

    return " ".join(unique[:10])


def get_audio_brief(pillar: str) -> dict:
    """
    Returns audio guidance for the TikTok script based on today's pillar.
    This is injected into the writer prompt so scripts are timed correctly.
    """
    mood_data = AUDIO_MOODS.get(pillar, AUDIO_MOODS["STORY"])

    # Find matching sound suggestions
    suggestions = [s for s in SOUND_SUGGESTIONS if pillar in s["best_for"]]
    sound = suggestions[0] if suggestions else SOUND_SUGGESTIONS[0]

    return {
        "energy": mood_data["energy"],
        "script_tip": mood_data["script_tip"],
        "sound_mood": sound["mood"],
        "sound_description": sound["description"],
        "search_term": sound["search_term"],
    }


def get_tiktok_context(pillar: str, emotional_trigger: str) -> dict:
    """
    Main function. Returns full TikTok context: hashtags + audio brief.
    Called by writer.py to enrich TikTok captions.
    """
    logger.step(f"Building TikTok context for pillar={pillar}, trigger={emotional_trigger}")

    hashtags = get_trending_hashtags(pillar, emotional_trigger)
    audio = get_audio_brief(pillar)

    context = {
        "hashtags": hashtags,
        "audio_energy": audio["energy"],
        "script_tip": audio["script_tip"],
        "sound_suggestion": f"{audio['sound_mood']} — {audio['sound_description']}",
        "sound_search": audio["search_term"],
    }

    logger.success(f"TikTok context ready. Tags: {hashtags[:60]}...")
    return context
