"""
Fincare Topic Researcher — FREE version
=======================================
Uses RSS feeds from Yahoo Finance, Reuters, and CNBC to find trending
finance topics. No API calls — zero cost.
"""

import os
import json
import random
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("researcher")

# Free RSS feeds — no API key needed
RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
]

# Behavioral finance angles mapped to common finance keywords
TOPIC_ANGLES = {
    "market": {
        "angle": "How market noise triggers emotional decision-making in retail investors",
        "emotional_trigger": "fear",
        "key_stat": "71% of retail investors sell at a loss during market downturns due to emotional panic"
    },
    "inflation": {
        "angle": "How inflation anxiety pushes investors into impulsive, low-return decisions",
        "emotional_trigger": "anxiety",
        "key_stat": "Investors who react to inflation headlines underperform the market by 3.2% annually"
    },
    "stock": {
        "angle": "Why chasing individual stocks is a psychological trap, not a strategy",
        "emotional_trigger": "FOMO",
        "key_stat": "Over 90% of active retail traders underperform a simple index fund over 10 years"
    },
    "invest": {
        "angle": "The hidden emotional patterns that sabotage long-term investing success",
        "emotional_trigger": "overconfidence",
        "key_stat": "Overconfident investors trade 45% more and earn 3.7% less annually"
    },
    "fed": {
        "angle": "How Fed announcements trigger irrational investor behaviour rooted in fear",
        "emotional_trigger": "fear",
        "key_stat": "Markets swing 2-3x more than fundamentals justify on Fed announcement days"
    },
    "rate": {
        "angle": "Why interest rate changes cause investors to make predictably bad decisions",
        "emotional_trigger": "anxiety",
        "key_stat": "Investors who react to rate changes sell at the worst possible time 68% of the time"
    },
    "recession": {
        "angle": "The psychology of recession fear and why it costs investors more than the recession itself",
        "emotional_trigger": "fear",
        "key_stat": "Investors who pulled out in 2020 missed a 100% recovery in 12 months"
    },
    "crypto": {
        "angle": "Why crypto volatility is the ultimate test of investor emotional control",
        "emotional_trigger": "FOMO",
        "key_stat": "Average crypto investor buys near peaks and sells near bottoms, losing 54% on timing alone"
    },
    "ai": {
        "angle": "How AI is changing personal finance — and what it means for how you invest",
        "emotional_trigger": "anxiety",
        "key_stat": "AI-assisted investors make 40% fewer emotional trades than those flying solo"
    },
    "debt": {
        "angle": "The shame spiral of financial debt and why it stops people from investing at all",
        "emotional_trigger": "shame",
        "key_stat": "64% of people with debt avoid investing entirely, even when they could afford to start small"
    },
}

# Fallback topics if RSS feeds fail or have no relevant headlines
FALLBACK_TOPICS = [
    {
        "topic": "Why investors sell exactly when they should be holding",
        "angle": "The panic-selling pattern that costs retail investors billions every year",
        "hook": "You didn't lose money. You locked in the loss.",
        "key_stat": "Investors who panic-sold in March 2020 missed a 93% market recovery",
        "emotional_trigger": "fear",
        "why_today": "Market volatility is always present, and the emotional response to it is the #1 wealth destroyer for retail investors."
    },
    {
        "topic": "The FOMO trap: why buying at the top feels so right",
        "angle": "How social media-driven FOMO pushes investors into peak-price purchases",
        "hook": "Everyone's getting rich. Except you. (Or so it feels.)",
        "key_stat": "FOMO-driven investors buy 73% of the time within 2 weeks of an all-time high",
        "emotional_trigger": "FOMO",
        "why_today": "Social media constantly surfaces winning portfolios, making FOMO the defining emotion of modern retail investing."
    },
    {
        "topic": "Financial anxiety is not a personality flaw — it's a design flaw",
        "angle": "The financial system was not built with human psychology in mind — Fincare fixes that",
        "hook": "You're not bad with money. Money is bad for your brain.",
        "key_stat": "87% of adults report money as their #1 source of stress",
        "emotional_trigger": "anxiety",
        "why_today": "Financial stress is at an all-time high, making this the most resonant topic for Fincare's audience right now."
    },
    {
        "topic": "Why checking your portfolio every day is making you poorer",
        "angle": "The over-monitoring trap and how it leads to reactive, loss-making decisions",
        "hook": "The more you check, the worse you do. Here's the data.",
        "key_stat": "Investors who check portfolios daily trade 50% more and earn 1.5% less annually",
        "emotional_trigger": "anxiety",
        "why_today": "With real-time apps showing second-by-second movements, over-monitoring is the silent portfolio killer."
    },
    {
        "topic": "The overconfidence bias: why smart people make dumb investment decisions",
        "angle": "Intelligence does not protect you from behavioral finance traps — it can make them worse",
        "hook": "The smarter you are, the better your brain lies to you.",
        "key_stat": "High-IQ investors are 35% more likely to be overconfident and 28% more likely to overtrade",
        "emotional_trigger": "overconfidence",
        "why_today": "Fintech tools have made it easier than ever for retail investors to act on false confidence."
    },
]


def _fetch_rss_headlines() -> list[str]:
    """Fetches headlines from multiple free RSS feeds. Returns a flat list of titles."""
    headlines = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FincareBot/1.0)"}

    for url in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            # Handle both RSS and Atom formats
            for item in root.iter("item"):
                title = item.find("title")
                if title is not None and title.text:
                    headlines.append(title.text.strip())
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title = entry.find("{http://www.w3.org/2005/Atom}title")
                if title is not None and title.text:
                    headlines.append(title.text.strip())

            logger.info(f"Fetched {len(headlines)} headlines so far...")
        except Exception as e:
            logger.warning(f"RSS feed failed ({url[:40]}...): {type(e).__name__}")
            continue

    return headlines


def _match_topic_from_headlines(headlines: list[str]) -> dict | None:
    """
    Scans headlines for finance keywords and maps to a Fincare behavioral angle.
    Returns a topic dict or None if no match found.
    """
    headline_text = " ".join(headlines).lower()

    # Score each keyword by how many headlines contain it
    keyword_scores = {}
    for keyword in TOPIC_ANGLES:
        count = headline_text.count(keyword)
        if count > 0:
            keyword_scores[keyword] = count

    if not keyword_scores:
        return None

    # Pick the most-mentioned keyword
    best_keyword = max(keyword_scores, key=keyword_scores.get)
    angle_data = TOPIC_ANGLES[best_keyword]

    # Find the most relevant headline for context
    matching_headlines = [h for h in headlines if best_keyword in h.lower()]
    best_headline = matching_headlines[0] if matching_headlines else f"{best_keyword} trends today"

    today = datetime.now().strftime("%B %d, %Y")

    topic = {
        "topic": f"{best_headline[:80]}... — what it means for your investing mindset",
        "angle": angle_data["angle"],
        "hook": _generate_hook(best_keyword),
        "key_stat": angle_data["key_stat"],
        "emotional_trigger": angle_data["emotional_trigger"],
        "why_today": f"'{best_headline[:60]}' is trending today ({today}), making this the right moment for Fincare to offer behavioral context."
    }

    logger.success(f"Topic matched from headlines — keyword: '{best_keyword}'")
    return topic


def _generate_hook(keyword: str) -> str:
    """Returns a scroll-stopping hook based on the matched keyword."""
    hooks = {
        "market": "The market moved. Your emotions moved faster.",
        "inflation": "Inflation is rising. Your decisions shouldn't be.",
        "stock": "Picking stocks feels smart. The data says otherwise.",
        "invest": "Most investors think they're rational. Most are wrong.",
        "fed": "The Fed spoke. Now your brain is doing something dangerous.",
        "rate": "Rates changed. Your investing strategy shouldn't.",
        "recession": "The R-word is back. Here's what it does to investor brains.",
        "crypto": "Crypto went wild. Did your emotions go with it?",
        "ai": "AI is reshaping finance. Is your mindset keeping up?",
        "debt": "Debt isn't just a financial problem. It's a psychological trap.",
    }
    return hooks.get(keyword, "Your brain is your biggest investing risk. Here's proof.")


def research_topic() -> dict:
    """
    Main research function.
    Step 1: Try free RSS feeds.
    Step 2: If RSS fails or no match, use a pre-written fallback topic.
    No API calls — zero cost.
    """
    logger.step("Starting topic research (free RSS method)...")

    headlines = _fetch_rss_headlines()
    logger.info(f"Total headlines fetched: {len(headlines)}")

    if headlines:
        topic = _match_topic_from_headlines(headlines)
        if topic:
            return topic
        logger.warning("Headlines fetched but no keyword match found — using fallback.")
    else:
        logger.warning("All RSS feeds failed — using fallback topic.")

    # Use a random fallback topic (rotates daily variety)
    day_index = datetime.now().timetuple().tm_yday % len(FALLBACK_TOPICS)
    topic = FALLBACK_TOPICS[day_index]
    logger.success(f"Using fallback topic: {topic['topic']}")
    return topic
