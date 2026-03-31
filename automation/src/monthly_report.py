"""
Fincare Monthly Performance Report
=====================================
Runs on the 1st of every month. Reads all tracking data and sends
a comprehensive analysis to Telegram.
Zero Claude API cost — pure data analysis.
"""

import os
import json
from datetime import datetime, timedelta
from utils.logger import SecureLogger

logger = SecureLogger("monthly_report")


def _load_json(path: str) -> any:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _get_last_month_range() -> tuple[datetime, datetime]:
    today     = datetime.now()
    first_day = today.replace(day=1)
    last_month_end   = first_day - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    return last_month_start, last_month_end


def _analyze_posts(start: datetime, end: datetime) -> dict:
    """Analyzes LinkedIn post performance for last month."""
    log = _load_json("logs/performance_log.json") or []

    monthly = [
        e for e in log
        if e.get("stats_fetched")
        and start <= datetime.fromisoformat(e["posted_at"]) <= end
    ]

    if not monthly:
        return {"count": 0}

    total_impressions = sum(e["stats"].get("impressionCount", 0) for e in monthly)
    total_likes       = sum(e["stats"].get("likeCount", 0)       for e in monthly)
    total_comments    = sum(e["stats"].get("commentCount", 0)    for e in monthly)
    total_clicks      = sum(e["stats"].get("clickCount", 0)      for e in monthly)

    best  = max(monthly, key=lambda e: e["stats"].get("impressionCount", 0))
    worst = min(monthly, key=lambda e: e["stats"].get("impressionCount", 0))

    # Breakdown by pillar
    pillar_data = {}
    for e in monthly:
        p = e.get("pillar", "Unknown")
        if p not in pillar_data:
            pillar_data[p] = {"impressions": 0, "likes": 0, "comments": 0, "count": 0}
        pillar_data[p]["impressions"] += e["stats"].get("impressionCount", 0)
        pillar_data[p]["likes"]       += e["stats"].get("likeCount", 0)
        pillar_data[p]["comments"]    += e["stats"].get("commentCount", 0)
        pillar_data[p]["count"]       += 1

    best_pillar  = max(pillar_data, key=lambda p: pillar_data[p]["impressions"]) if pillar_data else "—"
    worst_pillar = min(pillar_data, key=lambda p: pillar_data[p]["impressions"]) if pillar_data else "—"

    return {
        "count":              len(monthly),
        "total_impressions":  total_impressions,
        "total_likes":        total_likes,
        "total_comments":     total_comments,
        "total_clicks":       total_clicks,
        "avg_impressions":    total_impressions // len(monthly) if monthly else 0,
        "best_post":          {"topic": best["topic"], "impressions": best["stats"].get("impressionCount", 0),
                               "pillar": best.get("pillar","—"), "date": best["posted_at"][:10]},
        "worst_post":         {"topic": worst["topic"], "impressions": worst["stats"].get("impressionCount", 0),
                               "pillar": worst.get("pillar","—"), "date": worst["posted_at"][:10]},
        "pillar_breakdown":   pillar_data,
        "best_pillar":        best_pillar,
        "worst_pillar":       worst_pillar,
    }


def _analyze_hashtags() -> dict:
    """Summarizes which hashtag pool was active last month."""
    state = _load_json("logs/hashtag_rotation_state.json")
    if not state:
        return {}
    return {"week": state.get("week"), "pools": state.get("active_pools", {})}


def _analyze_competitors(start: datetime, end: datetime) -> dict:
    """Summarizes competitor intel from last month's spy reports."""
    month_str  = start.strftime("%Y_W")
    import glob
    files = glob.glob("logs/competitor_spy_*.json")
    monthly_files = [f for f in files if month_str in f]

    all_headlines = {}
    for filepath in monthly_files:
        data = _load_json(filepath) or {}
        for comp, headlines in data.get("competitors", {}).items():
            if comp not in all_headlines:
                all_headlines[comp] = []
            all_headlines[comp].extend(headlines)

    top_per_competitor = {}
    for comp, headlines in all_headlines.items():
        top_per_competitor[comp] = len(headlines)

    return {"reports_found": len(monthly_files), "activity": top_per_competitor}


def _generate_recommendation(post_analysis: dict) -> list[str]:
    """Generates 3 plain-English recommendations based on the data."""
    recs = []

    if post_analysis.get("count", 0) == 0:
        return ["Start posting consistently — no data yet to analyze."]

    best_pillar  = post_analysis.get("best_pillar", "—")
    worst_pillar = post_analysis.get("worst_pillar", "—")
    avg          = post_analysis.get("avg_impressions", 0)

    if best_pillar and best_pillar != "—":
        recs.append(f"Post more {best_pillar} content — it got the most impressions this month.")

    if worst_pillar and worst_pillar != best_pillar:
        recs.append(f"Rethink {worst_pillar} posts — lowest reach. Try changing the hook format.")

    if avg < 500:
        recs.append("Impressions are low overall — focus on stronger opening hooks and post at 8–9am UTC.")
    elif avg > 2000:
        recs.append("Strong month — maintain the posting cadence and keep the same tone that's working.")
    else:
        recs.append("Solid performance — test one new content angle next month to find a breakout format.")

    return recs[:3]


def build_monthly_report() -> str:
    """Builds the full monthly report as an HTML Telegram message."""
    start, end    = _get_last_month_range()
    month_name    = start.strftime("%B %Y")
    posts         = _analyze_posts(start, end)
    hashtags      = _analyze_hashtags()
    competitors   = _analyze_competitors(start, end)
    recommendations = _generate_recommendation(posts)

    lines = [
        f"<b>📊 FINCARE — MONTHLY REPORT</b>",
        f"<i>{month_name}</i>",
        f"{'─'*35}\n",
    ]

    # Post performance
    if posts.get("count", 0) == 0:
        lines.append("<b>📉 No post performance data yet.</b>\nKeep posting — data builds up after week 1.\n")
    else:
        lines += [
            f"<b>📈 POSTS THIS MONTH: {posts['count']}</b>",
            f"👁️ Total impressions: <b>{posts['total_impressions']:,}</b>",
            f"❤️ Total likes: {posts['total_likes']:,}",
            f"💬 Total comments: {posts['total_comments']:,}",
            f"🔗 Total clicks: {posts['total_clicks']:,}",
            f"📊 Avg impressions/post: {posts['avg_impressions']:,}\n",

            f"<b>🏆 BEST POST:</b>",
            f"📌 {posts['best_post']['topic'][:80]}",
            f"🏛️ Pillar: {posts['best_post']['pillar']} | 📅 {posts['best_post']['date']}",
            f"👁️ {posts['best_post']['impressions']:,} impressions\n",

            f"<b>📉 WORST POST:</b>",
            f"📌 {posts['worst_post']['topic'][:80]}",
            f"🏛️ Pillar: {posts['worst_post']['pillar']} | 📅 {posts['worst_post']['date']}",
            f"👁️ {posts['worst_post']['impressions']:,} impressions\n",
        ]

        # Pillar breakdown
        if posts.get("pillar_breakdown"):
            lines.append(f"<b>🏛️ BY PILLAR:</b>")
            for pillar, data in sorted(posts["pillar_breakdown"].items(),
                                       key=lambda x: x[1]["impressions"], reverse=True):
                avg = data["impressions"] // data["count"] if data["count"] else 0
                lines.append(f"  {pillar}: {data['impressions']:,} impr | {data['count']} posts | avg {avg:,}")
            lines.append("")

    # Hashtag rotation
    if hashtags:
        lines += [
            f"<b>🏷️ HASHTAG ROTATION:</b>",
            f"Active pool set: Week {hashtags.get('week','—')}",
            f"Pools: {', '.join(f'{k}={v}' for k,v in hashtags.get('pools',{}).items())}\n",
        ]

    # Competitor activity
    if competitors.get("reports_found", 0) > 0:
        lines += [f"<b>🕵️ COMPETITOR ACTIVITY:</b>"]
        for comp, count in competitors.get("activity", {}).items():
            lines.append(f"  {comp}: {count} articles tracked")
        lines.append("")

    # Recommendations
    lines += [
        f"{'─'*35}",
        f"<b>💡 RECOMMENDATIONS FOR NEXT MONTH:</b>",
    ]
    for i, rec in enumerate(recommendations, 1):
        lines.append(f"{i}. {rec}")

    lines += [f"\n{'─'*35}", "<i>Next report: 1st of next month.</i>"]
    return "\n".join(lines)


def send_monthly_report():
    """Sends the monthly report to Telegram. Called on the 1st of each month."""
    from src.telegram_bot import send_notification
    logger.step("Generating monthly performance report...")
    report = build_monthly_report()
    send_notification(report)
    logger.success("Monthly report sent.")


def run_monthly_report_if_due():
    """
    Checks if today is the 1st of the month and sends the report.
    Called by GitHub Actions daily stats check job.
    """
    if datetime.now().day == 1:
        logger.step("1st of month detected — sending monthly report...")
        send_monthly_report()
    else:
        logger.info("Not the 1st — skipping monthly report.")
