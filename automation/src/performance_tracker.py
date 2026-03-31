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
    Builds a weekly performance summary for posts from the last 7 days.
    Highlights best performing post and overall trends.
    """
    log = load_performance_log()
    cutoff = datetime.now() - timedelta(days=7)

    recent = [
        e for e in log
        if e.get("stats_fetched") and datetime.fromisoformat(e["posted_at"]) >= cutoff
    ]

    if not recent:
        return (
            "📊 <b>Fincare SMM — Weekly Report</b>\n\n"
            "<i>No performance data yet for this week.\n"
            "Stats will appear here after the first approved posts go live.</i>"
        )

    # Find best post
    best = max(recent, key=lambda e: e["stats"].get("impressionCount", 0))

    total_impressions = sum(e["stats"].get("impressionCount", 0) for e in recent)
    total_likes       = sum(e["stats"].get("likeCount", 0) for e in recent)
    total_comments    = sum(e["stats"].get("commentCount", 0) for e in recent)

    lines = [
        "📊 <b>Fincare SMM — Weekly Performance Report</b>",
        f"<i>{datetime.now().strftime('%B %d, %Y')}</i>",
        f"{'─'*35}\n",
        f"<b>Posts this week:</b> {len(recent)}",
        f"<b>Total impressions:</b> {total_impressions:,}",
        f"<b>Total likes:</b> {total_likes:,}",
        f"<b>Total comments:</b> {total_comments:,}",
        f"\n{'─'*35}",
        f"<b>🏆 BEST POST THIS WEEK:</b>",
        f"📌 {best['topic'][:80]}",
        f"🏛️ Pillar: {best.get('pillar', '—')}",
        f"👁️ Impressions: {best['stats']['impressionCount']:,}",
        f"❤️ Likes: {best['stats']['likeCount']:,}",
        f"💬 Comments: {best['stats']['commentCount']:,}",
        f"🔗 Clicks: {best['stats']['clickCount']:,}",
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
        lines.append(f"\n{'─'*35}\n<b>📊 BY CONTENT PILLAR:</b>")
        for pillar, data in sorted(pillar_stats.items(), key=lambda x: x[1]["impressions"], reverse=True):
            avg = data["impressions"] // data["count"] if data["count"] else 0
            lines.append(f"  {pillar}: {data['impressions']:,} impressions ({data['count']} posts, avg {avg:,})")

    lines.append(f"\n{'─'*35}")
    lines.append("<i>Next report: Monday.</i>")
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
    Fetches pending stats + sends weekly report on Mondays + monthly report on 1st.
    """
    logger.step("Running stats check...")
    updated = check_pending_stats()
    logger.info(f"Stats updated for {updated} posts.")

    if datetime.now().weekday() == 0:  # Monday
        logger.step("Monday detected — sending weekly performance report...")
        send_weekly_performance_report()

    # Monthly report on the 1st of each month
    from src.monthly_report import run_monthly_report_if_due
    run_monthly_report_if_due()
