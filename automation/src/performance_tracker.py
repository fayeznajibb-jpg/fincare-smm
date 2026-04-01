"""
Fincare Post Performance Tracker
==================================
Fetches LinkedIn company post analytics 24h after posting.
Sends weekly performance report every Monday via Telegram.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from utils.logger import SecureLogger

logger = SecureLogger("performance_tracker")

PERFORMANCE_LOG_PATH = "logs/performance_log.json"
LINKEDIN_STATS_API   = "https://api.linkedin.com/rest/organizationalEntityShareStatistics"
LINKEDIN_API_VERSION = "202304"


def load_performance_log() -> list:
    """Loads performance log. Returns empty list if file missing or corrupt."""
    try:
        with open(PERFORMANCE_LOG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_performance_log(log: list):
    """Saves performance log to file."""
    os.makedirs("logs", exist_ok=True)
    with open(PERFORMANCE_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def record_post(urn: str, platform: str, topic: str, pillar: str = ""):
    """
    Records a newly published post for later stats fetching.
    Called from main.py immediately after a successful LinkedIn publish.
    """
    log = load_performance_log()
    fetch_after = (datetime.now() + timedelta(hours=24)).isoformat()
    entry = {
        "urn":           urn,
        "platform":      platform,
        "topic":         topic[:100],
        "pillar":        pillar,
        "posted_at":     datetime.now().isoformat(),
        "fetch_after":   fetch_after,
        "stats_fetched": False,
        "stats":         None,
    }
    log.append(entry)
    save_performance_log(log)
    logger.info(f"Post recorded for tracking: {urn[:40]}...")


def fetch_linkedin_stats(urn: str) -> dict | None:
    """
    Fetches post analytics from LinkedIn API.
    Returns stats dict or None on failure.
    """
    token  = os.getenv("LINKEDIN_ACCESS_TOKEN")
    org_id = os.getenv("LINKEDIN_ORGANIZATION_ID")

    if not token or not org_id:
        logger.warning("LinkedIn credentials missing — cannot fetch stats.")
        return None

    headers = {
        "Authorization":              f"Bearer {token}",
        "LinkedIn-Version":           LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version":  "2.0.0",
    }

    params = {
        "q":                    "organizationalEntity",
        "organizationalEntity": f"urn:li:organization:{org_id}",
        "shares":               f"List({urn})",
    }

    try:
        resp = requests.get(LINKEDIN_STATS_API, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logger.error(f"LinkedIn stats API returned {resp.status_code}")
            return None

        data = resp.json()
        elements = data.get("elements", [])
        if not elements:
            return None

        stats = elements[0].get("totalShareStatistics", {})
        return {
            "impressionCount": stats.get("impressionCount", 0),
            "likeCount":       stats.get("likeCount", 0),
            "clickCount":      stats.get("clickCount", 0),
            "commentCount":    stats.get("commentCount", 0),
            "shareCount":      stats.get("shareCount", 0),
            "engagementRate":  round(stats.get("engagement", 0) * 100, 2),
        }
    except Exception as e:
        logger.error(f"LinkedIn stats fetch error: {type(e).__name__}")
        return None


def check_pending_stats() -> int:
    """
    Checks all posts where stats haven't been fetched yet and 24h has passed.
    Updates the log with fetched stats. Returns count of updated entries.
    """
    log = load_performance_log()
    updated = 0
    now = datetime.now()

    for entry in log:
        if entry.get("stats_fetched"):
            continue
        fetch_after = datetime.fromisoformat(entry.get("fetch_after", now.isoformat()))
        if now < fetch_after:
            continue

        urn   = entry.get("urn", "")
        stats = fetch_linkedin_stats(urn)

        if stats:
            entry["stats"]         = stats
            entry["stats_fetched"] = True
            entry["fetched_at"]    = now.isoformat()
            logger.success(f"Stats fetched for {urn[:40]}: {stats['impressionCount']} impressions")
            updated += 1
        else:
            logger.warning(f"Could not fetch stats for {urn[:40]} — will retry next run.")

    if updated:
        save_performance_log(log)

    return updated


def build_weekly_report() -> str:
    """
    Builds a daily post review with:
    - Today's most recent post stats (if available)
    - This week's rolling summary
    Updated daily at 9am by the stats check job.
    """
    log    = load_performance_log()
    now    = datetime.now()
    cutoff = now - timedelta(days=7)
    today  = now.strftime("%Y-%m-%d")

    recent = [
        e for e in log
        if e.get("stats_fetched") and datetime.fromisoformat(e["posted_at"]) >= cutoff
    ]

    # Sort recent by posted_at descending to find today's post
    recent_sorted = sorted(recent, key=lambda e: e["posted_at"], reverse=True)
    today_post    = next((e for e in recent_sorted if e["posted_at"][:10] == today), None)

    # Find most recently fetched entry to show last-updated time
    fetched_entries = [e for e in log if e.get("fetched_at")]
    last_updated = (
        max(fetched_entries, key=lambda e: e["fetched_at"])["fetched_at"][:16].replace("T", " ") + " UTC"
        if fetched_entries else "not yet fetched"
    )

    if not recent:
        return (
            "📊 <b>Fincare — Post Performance</b>\n"
            f"<i>{now.strftime('%B %d, %Y')}</i>\n\n"
            "<i>No performance data yet.\n"
            "Stats appear here 24h after the first post goes live.</i>\n\n"
            f"<i>🕐 Stats last updated: {last_updated}</i>"
        )

    lines = [
        "📊 <b>Fincare — Post Performance</b>",
        f"<i>{now.strftime('%A, %B %d, %Y')}</i>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    # ── Today's post ──────────────────────────────────────
    if today_post:
        s = today_post["stats"]
        lines += [
            "<b>📅 TODAY'S POST:</b>",
            f"📌 {today_post['topic'][:80]}",
            f"👁️ {s.get('impressionCount', 0):,} impressions  "
            f"❤️ {s.get('likeCount', 0):,}  "
            f"💬 {s.get('commentCount', 0):,}  "
            f"🔗 {s.get('clickCount', 0):,} clicks",
            f"📈 Engagement: {s.get('engagementRate', 0)}%\n",
            f"{'─'*35}\n",
        ]
    else:
        lines.append("<i>Today's post stats will appear after 24h.</i>\n")
        lines.append(f"{'─'*35}\n")

    # ── This week's rolling summary ────────────────────────
    total_impressions = sum(e["stats"].get("impressionCount", 0) for e in recent)
    total_likes       = sum(e["stats"].get("likeCount", 0) for e in recent)
    total_comments    = sum(e["stats"].get("commentCount", 0) for e in recent)
    best = max(recent, key=lambda e: e["stats"].get("impressionCount", 0))

    lines += [
        f"<b>📆 THIS WEEK ({len(recent)} posts):</b>",
        f"👁️ Total impressions: <b>{total_impressions:,}</b>",
        f"❤️ Likes: {total_likes:,}  💬 Comments: {total_comments:,}",
        f"\n<b>🏆 Best post:</b>",
        f"📌 {best['topic'][:80]}",
        f"👁️ {best['stats']['impressionCount']:,} impressions · 🏛️ {best.get('pillar', '—')}",
    ]

    # Pillar breakdown
    pillar_stats = {}
    for e in recent:
        p = e.get("pillar", "Unknown")
        if p not in pillar_stats:
            pillar_stats[p] = {"impressions": 0, "count": 0}
        pillar_stats[p]["impressions"] += e["stats"].get("impressionCount", 0)
        pillar_stats[p]["count"] += 1

    if len(pillar_stats) > 1:
        lines.append(f"\n{'─'*35}\n<b>📊 BY PILLAR:</b>")
        for pillar, data in sorted(pillar_stats.items(), key=lambda x: x[1]["impressions"], reverse=True):
            avg = data["impressions"] // data["count"] if data["count"] else 0
            lines.append(f"  {pillar}: {data['impressions']:,} impr · {data['count']} posts · avg {avg:,}")

    lines.append(f"\n{'─'*35}")
    lines.append(f"<i>🕐 Stats last updated: {last_updated}</i>")
    return "\n".join(lines)


def send_weekly_performance_report():
    """Sends the weekly report to Telegram. Called every Monday by GitHub Actions."""
    from src.telegram_bot import send_notification
    report = build_weekly_report()
    send_notification(report)
    logger.success("Weekly performance report sent.")


def run_stats_check():
    """
    Main orchestrator called by GitHub Actions daily at 9am UTC.
    Fetches pending stats and updates the performance log.
    Weekly and monthly reports are served on-demand via the morning briefing buttons —
    no automatic Telegram sends here.
    """
    logger.step("Running stats check...")
    updated = check_pending_stats()
    logger.info(f"Stats updated for {updated} posts.")
    logger.success("Performance log updated — data available via 📊 Post Review button in morning briefing.")
