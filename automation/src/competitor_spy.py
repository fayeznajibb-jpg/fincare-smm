"""
Fincare Competitor Spy — FREE version
======================================
Monitors competitor fintech apps weekly via Google News RSS.
No API key needed. Sends a Telegram intelligence report every Monday.
"""

import os
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("competitor_spy")

COMPETITOR_FEEDS = {
    "Robinhood":   "https://news.google.com/rss/search?q=Robinhood+investing+app&hl=en-US&gl=US&ceid=US:en",
    "Acorns":      "https://news.google.com/rss/search?q=Acorns+investing+app&hl=en-US&gl=US&ceid=US:en",
    "Betterment":  "https://news.google.com/rss/search?q=Betterment+investing&hl=en-US&gl=US&ceid=US:en",
    "Wealthfront": "https://news.google.com/rss/search?q=Wealthfront+investing&hl=en-US&gl=US&ceid=US:en",
}

# Also monitor the broader niche for content angle ideas
NICHE_FEEDS = {
    "BehavioralFinance": "https://news.google.com/rss/search?q=behavioral+finance+investing+psychology&hl=en-US&gl=US&ceid=US:en",
    "RetailInvestors":   "https://news.google.com/rss/search?q=retail+investors+emotional+investing&hl=en-US&gl=US&ceid=US:en",
}

ANGLE_KEYWORDS = {
    "new feature":        ["launch", "new", "update", "feature", "release", "introduce"],
    "controversy":        ["ban", "lawsuit", "fine", "issue", "problem", "outage", "complaint"],
    "growth":             ["growth", "users", "million", "record", "surge", "expand"],
    "education content":  ["guide", "tips", "how to", "learn", "explain", "understand"],
    "market commentary":  ["market", "crash", "rally", "bull", "bear", "volatility"],
    "partnership":        ["partner", "deal", "acquisition", "merge", "collaborate"],
}


def _fetch_headlines(name: str, url: str, max_items: int = 5) -> list[dict]:
    """Fetches top headlines from a Google News RSS feed."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FincareBot/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        items = []
        for item in root.iter("item"):
            title = item.find("title")
            pub   = item.find("pubDate")
            if title is not None and title.text:
                items.append({
                    "title":     title.text.strip(),
                    "published": pub.text.strip() if pub is not None else "",
                    "source":    name,
                })
            if len(items) >= max_items:
                break
        logger.info(f"{name}: {len(items)} headlines fetched")
        return items
    except Exception as e:
        logger.warning(f"{name} feed failed: {type(e).__name__}")
        return []


def _detect_angle(title: str) -> str:
    """Detects the content angle of a headline."""
    title_lower = title.lower()
    for angle, keywords in ANGLE_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            return angle
    return "general"


def _build_report(competitor_data: dict, niche_data: dict) -> str:
    """Formats the weekly intelligence report as HTML for Telegram."""
    week = datetime.now().strftime("Week %W, %Y")
    lines = [f"<b>🕵️ FINCARE — WEEKLY COMPETITOR INTELLIGENCE</b>\n<i>{week}</i>\n{'─'*35}\n"]

    # Competitor section
    lines.append("<b>📊 WHAT COMPETITORS ARE UP TO:</b>\n")
    for name, headlines in competitor_data.items():
        if not headlines:
            lines.append(f"<b>{name}:</b> No news this week.\n")
            continue
        lines.append(f"<b>{name}:</b>")
        for h in headlines[:3]:
            angle = _detect_angle(h["title"])
            lines.append(f"  • [{angle}] {h['title'][:90]}")
        lines.append("")

    # Niche angles section
    lines.append(f"\n{'─'*35}\n<b>🎯 WHAT'S RESONATING IN THE NICHE:</b>\n")
    all_niche = []
    for headlines in niche_data.values():
        all_niche.extend(headlines)

    angle_counts = {}
    for h in all_niche:
        angle = _detect_angle(h["title"])
        angle_counts[angle] = angle_counts.get(angle, 0) + 1

    if angle_counts:
        sorted_angles = sorted(angle_counts.items(), key=lambda x: x[1], reverse=True)
        lines.append("Top content angles this week:")
        for angle, count in sorted_angles[:4]:
            lines.append(f"  • {angle} ({count} articles)")

    lines.append(f"\n{'─'*35}")
    lines.append("<i>Use these angles for next week's content. See logs/ for full data.</i>")
    return "\n".join(lines)


def run_competitor_spy():
    """
    Main function. Fetches competitor + niche headlines and saves to logs/.
    Called by GitHub Actions every Monday morning (7am).
    Does NOT send to Telegram — the main pipeline (8am) serves the data
    via the 🕵️ Competitors button in the morning briefing.
    """
    logger.step("Starting weekly competitor intelligence run...")

    competitor_data = {}
    for name, url in COMPETITOR_FEEDS.items():
        competitor_data[name] = _fetch_headlines(name, url)

    niche_data = {}
    for name, url in NICHE_FEEDS.items():
        niche_data[name] = _fetch_headlines(name, url)

    # Save raw data — read by telegram_bot._menu_competitors() on demand
    os.makedirs("logs", exist_ok=True)
    week_key = datetime.now().strftime("%Y_W%W")
    log_path = f"logs/competitor_spy_{week_key}.json"
    with open(log_path, "w") as f:
        json.dump({"competitors": competitor_data, "niche": niche_data}, f, indent=2)
    logger.info(f"Raw data saved: {log_path}")

    logger.success("Competitor data ready — will be served via morning briefing button.")
    total = sum(len(v) for v in competitor_data.values())
    return {"success": True, "competitors_checked": len(competitor_data), "headlines_found": total}
