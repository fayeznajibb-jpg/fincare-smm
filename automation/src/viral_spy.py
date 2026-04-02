"""
Fincare Viral Content Intelligence
=====================================
Monitors @grahamstephan, @andreijikh, @humphreytalks, @barebone.ai
on TikTok and Instagram. Downloads metadata + thumbnails via yt-dlp,
analyses with Claude Haiku Vision to identify viral hook patterns.

Runs every Monday at 6am UTC (1 hour before main pipeline).
Saves to logs/viral_intel_{YYYY_WXX}.json — read by:
  - telegram_bot._menu_competitors() (appended to competitor report)
  - video_generator._build_video_props() (informs Remotion content)

Cost: ~$0.06/week (20 videos × $0.003 Claude Vision per thumbnail)
No API keys needed for yt-dlp.
"""

import os
import json
import base64
import subprocess
import anthropic
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("viral_spy")

VIRAL_ACCOUNTS = {
    "tiktok": [
        {"handle": "humphreytalks",  "url": "https://www.tiktok.com/@humphreytalks"},
        {"handle": "andreijikh",     "url": "https://www.tiktok.com/@andreijikh"},
        {"handle": "grahamstephan",  "url": "https://www.tiktok.com/@grahamstephan"},
        {"handle": "barebone.ai",    "url": "https://www.tiktok.com/@barebone.ai"},
    ],
    "instagram": [
        {"handle": "humphreytalks",  "url": "https://www.instagram.com/humphreytalks/reels/"},
        {"handle": "andreijikh",     "url": "https://www.instagram.com/andreijikh/reels/"},
        {"handle": "grahamstephan",  "url": "https://www.instagram.com/grahamstephan/reels/"},
    ],
}

MAX_VIDEOS_PER_ACCOUNT = 5
THUMBNAIL_DIR = "drafts/viral_downloads"


def _fetch_metadata(handle: str, url: str, platform: str) -> list[dict]:
    """
    Uses yt-dlp to extract metadata for the latest videos from an account.
    Downloads thumbnails for Vision analysis.
    Returns list of video metadata dicts.
    """
    out_dir = os.path.join(THUMBNAIL_DIR, platform, handle)
    os.makedirs(out_dir, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "--skip-download",           # metadata only — no video file
        "--write-thumbnail",         # download thumbnail for Vision analysis
        "--convert-thumbnails", "jpg",
        "--playlist-items", f"1-{MAX_VIDEOS_PER_ACCOUNT}",
        "--print-json",
        "--output", f"{out_dir}/%(id)s.%(ext)s",
        url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                view_count    = data.get("view_count") or 0
                like_count    = data.get("like_count") or 0
                comment_count = data.get("comment_count") or 0
                engagement    = (like_count + comment_count) / max(view_count, 1)
                videos.append({
                    "id":           data.get("id", ""),
                    "title":        data.get("title", "")[:120],
                    "description":  (data.get("description") or "")[:200],
                    "duration":     data.get("duration", 0),
                    "view_count":   view_count,
                    "like_count":   like_count,
                    "comment_count":comment_count,
                    "engagement_rate": round(engagement * 100, 3),
                    "upload_date":  data.get("upload_date", ""),
                    "platform":     platform,
                    "handle":       handle,
                    "thumbnail_path": os.path.join(out_dir, f"{data.get('id', '')}.jpg"),
                })
            except json.JSONDecodeError:
                continue

        logger.info(f"{platform}/@{handle}: {len(videos)} videos fetched")
        return videos

    except subprocess.TimeoutExpired:
        logger.warning(f"{platform}/@{handle}: yt-dlp timeout")
        return []
    except FileNotFoundError:
        logger.error("yt-dlp not installed. Run: pip install yt-dlp")
        return []
    except Exception as e:
        logger.warning(f"{platform}/@{handle}: fetch error — {type(e).__name__}")
        return []


def _analyse_thumbnail(video: dict, client: anthropic.Anthropic) -> dict:
    """
    Sends thumbnail to Claude Haiku Vision for hook pattern analysis.
    Returns analysis dict. Falls back to caption-only analysis if no thumbnail.
    """
    thumb_path = video.get("thumbnail_path", "")
    image_data = None

    if thumb_path and os.path.exists(thumb_path):
        try:
            with open(thumb_path, "rb") as f:
                image_data = base64.standard_b64encode(f.read()).decode("utf-8")
        except Exception:
            pass

    content = []
    if image_data:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": image_data,
            }
        })

    caption_text = video.get("description") or video.get("title") or ""
    prompt = f"""You are analysing a viral finance TikTok/Instagram video for a social media team.

Caption/title: "{caption_text[:200]}"
Duration: {video.get('duration', 0)} seconds
Engagement rate: {video.get('engagement_rate', 0):.2f}%

{"Analyse the thumbnail image AND caption." if image_data else "Analyse the caption only (no thumbnail available)."}

Return ONLY a raw JSON object:
{{
  "hook_type": "shock_stat | question | story | pov | controversy | comparison | before_after | mistake",
  "content_angle": "one-line description of the content angle",
  "emotional_trigger": "fear | FOMO | anxiety | overconfidence | shame | curiosity | inspiration",
  "pacing": "fast | medium | slow",
  "format": "talking_head | screen_record | animation | text_overlay | b_roll",
  "viral_reason": "one sentence — why this would get shares/saves",
  "fincare_adaptation": "how Fincare could use this hook style for behavioural finance content"
}}"""

    content.append({"type": "text", "text": prompt})

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": content}]
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.warning(f"Vision analysis failed for {video.get('id', '')}: {type(e).__name__}")
        return {
            "hook_type": "unknown",
            "content_angle": caption_text[:80],
            "emotional_trigger": "curiosity",
            "pacing": "medium",
            "format": "unknown",
            "viral_reason": "Could not analyse",
            "fincare_adaptation": "N/A"
        }


def _build_viral_brief(all_videos: list[dict]) -> dict:
    """
    Aggregates all video analyses to identify the top 3 winning patterns.
    Returns a brief dict for use in video_generator.py.
    """
    if not all_videos:
        return {"top_pattern": "shock_stat", "top_trigger": "fear", "top_accounts": []}

    # Sort by engagement rate
    sorted_videos = sorted(all_videos, key=lambda v: v.get("engagement_rate", 0), reverse=True)
    top_3 = sorted_videos[:3]

    # Count hook types and triggers across all videos
    hook_counts    = {}
    trigger_counts = {}
    for v in all_videos:
        analysis = v.get("analysis", {})
        h = analysis.get("hook_type", "unknown")
        t = analysis.get("emotional_trigger", "unknown")
        hook_counts[h]    = hook_counts.get(h, 0) + 1
        trigger_counts[t] = trigger_counts.get(t, 0) + 1

    top_hook    = max(hook_counts,    key=hook_counts.get)    if hook_counts    else "shock_stat"
    top_trigger = max(trigger_counts, key=trigger_counts.get) if trigger_counts else "fear"

    best = sorted_videos[0] if sorted_videos else {}
    best_analysis = best.get("analysis", {})

    return {
        "top_pattern":          top_hook,
        "top_trigger":          top_trigger,
        "top_hook_counts":      hook_counts,
        "top_trigger_counts":   trigger_counts,
        "best_performer": {
            "handle":          best.get("handle", ""),
            "platform":        best.get("platform", ""),
            "view_count":      best.get("view_count", 0),
            "engagement_rate": best.get("engagement_rate", 0),
            "hook_type":       best_analysis.get("hook_type", ""),
            "viral_reason":    best_analysis.get("viral_reason", ""),
            "fincare_adaptation": best_analysis.get("fincare_adaptation", ""),
        },
        "top_3_fincare_adaptations": [
            v.get("analysis", {}).get("fincare_adaptation", "")
            for v in top_3
        ],
        "week": datetime.now().strftime("%Y_W%W"),
        "generated_at": datetime.now().isoformat(),
    }


def build_telegram_viral_summary(brief: dict) -> str:
    """Formats viral intel brief as HTML for Telegram (appended to competitor report)."""
    if not brief or not brief.get("best_performer"):
        return ""

    best    = brief["best_performer"]
    top3    = brief.get("top_3_fincare_adaptations", [])
    hooks   = brief.get("top_hook_counts", {})
    sorted_hooks = sorted(hooks.items(), key=lambda x: x[1], reverse=True)[:3]

    lines = [
        f"\n{'─'*35}",
        f"<b>🔥 VIRAL PATTERNS THIS WEEK:</b>\n",
        f"Top hook style: <b>{brief.get('top_pattern', '—').replace('_', ' ')}</b>",
        f"Top trigger: <b>{brief.get('top_trigger', '—')}</b>",
        f"",
        f"<b>🏆 Best performer:</b>",
        f"@{best.get('handle','—')} ({best.get('platform','—')})",
        f"👁️ {best.get('view_count', 0):,} views · {best.get('engagement_rate', 0):.2f}% engagement",
        f"<i>{best.get('viral_reason', '—')}</i>",
    ]

    if sorted_hooks:
        lines.append(f"\n<b>Hook breakdown:</b>")
        for hook, count in sorted_hooks:
            lines.append(f"  · {hook.replace('_', ' ')}: {count} videos")

    if top3 and top3[0]:
        lines.append(f"\n<b>💡 Fincare content ideas from viral patterns:</b>")
        for i, idea in enumerate(top3[:3], 1):
            if idea and idea != "N/A":
                lines.append(f"  {i}. {idea}")

    lines.append(f"\n<i>🕐 Analysed: {datetime.now().strftime('%A, %b %d')}</i>")
    return "\n".join(lines)


def run_viral_spy():
    """
    Main entry point. Called by GitHub Actions every Monday at 6am UTC.
    Fetches metadata → analyses thumbnails → saves intel file.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — cannot run Vision analysis.")
        return

    client = anthropic.Anthropic(api_key=api_key)
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    logger.step("Starting viral content intelligence run...")
    all_videos = []

    # Fetch from TikTok
    for account in VIRAL_ACCOUNTS["tiktok"]:
        videos = _fetch_metadata(account["handle"], account["url"], "tiktok")
        all_videos.extend(videos)

    # Fetch from Instagram
    for account in VIRAL_ACCOUNTS["instagram"]:
        videos = _fetch_metadata(account["handle"], account["url"], "instagram")
        all_videos.extend(videos)

    logger.info(f"Total videos fetched: {len(all_videos)}")

    # Analyse each with Claude Haiku Vision
    for i, video in enumerate(all_videos):
        logger.step(f"Analysing {i+1}/{len(all_videos)}: @{video['handle']} ({video['platform']})...")
        video["analysis"] = _analyse_thumbnail(video, client)

    # Build weekly brief
    brief = _build_viral_brief(all_videos)

    # Save full intel file
    week_key  = datetime.now().strftime("%Y_W%W")
    log_path  = f"logs/viral_intel_{week_key}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({"brief": brief, "videos": all_videos}, f, indent=2, ensure_ascii=False)

    logger.success(f"Viral intel saved: {log_path}")
    logger.success(f"Top pattern: {brief.get('top_pattern')} | Top trigger: {brief.get('top_trigger')}")
    logger.success(f"Best performer: @{brief.get('best_performer', {}).get('handle', '—')}")

    total = len(all_videos)
    return {"success": True, "videos_analysed": total, "brief": brief}


def load_latest_viral_intel() -> dict | None:
    """
    Loads the most recent viral intel file.
    Called by video_generator.py and telegram_bot._menu_competitors().
    Returns the brief dict or None if no file found.
    """
    import glob
    files = sorted(glob.glob("logs/viral_intel_*.json"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
        return data.get("brief")
    except Exception:
        return None
