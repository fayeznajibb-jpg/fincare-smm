"""
Fincare Founder Bot — Always-On Listener
=========================================
Handles TWO things:
  1. Morning briefing callbacks — loads session from drafts/session_*.json,
     routes all button taps (approve, edit, video, etc.) and calls publish_all()
  2. Founder video editing — /script, /chart, /topic, raw video uploads

Usage:
  cd "/Users/fayeznajib/Downloads/Fincare SMM /automation"
  python run_bot.py

Caption tips when sending a video:
  SPY        → adds S&P 500 chart overlay
  fa         → Persian language
  no captions → skip auto-captions
  pip        → face-in-corner PiP mode
  cut first 5 seconds → trim start
"""

import os
import sys
import time
import json
import glob
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from utils.logger import SecureLogger

logger = SecureLogger("run_bot")

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DRAFTS  = os.path.abspath(os.path.join(os.path.dirname(__file__), "drafts"))

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

PLATFORM_LABELS = {
    "linkedin_company": ("🏢", "LinkedIn Company"),
    "instagram":        ("📸", "Instagram"),
    "tiktok":           ("🎵", "TikTok"),
    "threads":          ("🧵", "Threads"),
}

SKIP_REASONS = [
    ("🎯 Not relevant today", "not_relevant"),
    ("✍️ Poor quality",       "poor_quality"),
    ("🗣️ Wrong tone",         "wrong_tone"),
    ("💾 Save for later",     "save_later"),
]


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM API HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _api(method: str, payload: dict = None, files=None, timeout=30) -> dict:
    url = TELEGRAM_API.format(token=TOKEN, method=method)
    try:
        if files:
            resp = requests.post(url, data=payload or {}, files=files, timeout=timeout)
        else:
            resp = requests.post(url, json=payload or {}, timeout=timeout)
        return resp.json()
    except Exception as e:
        logger.warning(f"API error ({method}): {type(e).__name__}")
        return {}


def _send(text: str, parse_mode: str = "HTML"):
    MAX = 4096
    while text:
        chunk, text = text[:MAX], text[MAX:]
        _api("sendMessage", {"chat_id": CHAT_ID, "text": chunk, "parse_mode": parse_mode})


def _send_keyboard(text: str, keyboard: list):
    _api("sendMessage", {
        "chat_id":      CHAT_ID,
        "text":         text,
        "parse_mode":   "HTML",
        "reply_markup": {"inline_keyboard": keyboard},
    })


def _answer(callback_id: str, text: str = ""):
    _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def _typing():
    try:
        _api("sendChatAction", {"chat_id": CHAT_ID, "action": "typing"})
    except Exception:
        pass


def _send_photo(path: str, caption: str = ""):
    with open(path, "rb") as f:
        _api("sendPhoto",
             {"chat_id": CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
             files={"photo": f}, timeout=60)


def _send_video_file(path: str, caption: str = ""):
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > 45:
        try:
            from src.video_agent import _compress_for_telegram_path
            path = _compress_for_telegram_path(path)
        except Exception:
            pass
    with open(path, "rb") as f:
        _api("sendVideo",
             {"chat_id": CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML",
              "supports_streaming": "true"},
             files={"video": f}, timeout=180)


def _download_file(file_id: str, subfolder: str = "founder_videos") -> str:
    result = _api("getFile", {"file_id": file_id})
    if "result" not in result:
        err = result.get("description", "unknown error")
        raise ValueError(
            f"Telegram file download blocked: {err}\n\n"
            "📌 Telegram bots can only receive files under 20MB.\n"
            "Please compress your video before sending:\n"
            "• iPhone: use iMessage to send (auto-compresses)\n"
            "• Or trim to under 60 seconds before sending"
        )
    file_path  = result["result"]["file_path"]
    ext        = file_path.split(".")[-1] if "." in file_path else "mp4"
    url        = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    resp       = requests.get(url, timeout=120)
    resp.raise_for_status()
    save_dir   = os.path.join(DRAFTS, subfolder)
    os.makedirs(save_dir, exist_ok=True)
    ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
    local_path = os.path.join(save_dir, f"founder_{ts}.{ext}")
    with open(local_path, "wb") as f:
        f.write(resp.content)
    size_mb = os.path.getsize(local_path) / (1024 * 1024)
    logger.success(f"Downloaded: {local_path} ({size_mb:.1f}MB)")
    return local_path


# ─────────────────────────────────────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def _load_pending_session() -> dict | None:
    """Scan drafts/ for the most recent pending session (by created_at, max 48h old)."""
    os.makedirs(DRAFTS, exist_ok=True)
    from datetime import timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    candidates = []
    for f in glob.glob(os.path.join(DRAFTS, "session_*.json")):
        try:
            with open(f) as fp:
                data = json.load(fp)
            if data.get("status") != "pending":
                continue
            try:
                created = datetime.fromisoformat(data.get("created_at", ""))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if created < cutoff:
                continue
            candidates.append((created, data))
        except Exception:
            pass
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    data = candidates[0][1]
    logger.info(f"Loaded pending session: {data['session_id']}")
    return data


def _load_session_by_id(session_id: str) -> dict | None:
    """Load a specific session from disk by its ID — used to recover from bot restarts."""
    path = os.path.join(DRAFTS, f"session_{session_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("status") == "pending":
            data.setdefault("state",            "picker")
            data.setdefault("waiting_for_text", None)
            data.setdefault("current_posts",    data.get("posts", {}))
            data.setdefault("current_topic",    data.get("topic", {}))
            data.setdefault("current_video",    data.get("video_result", {}))
            data.setdefault("rewrites",         0)
            logger.info(f"Recovered session from disk: {session_id}")
            return data
    except Exception:
        pass
    return None


def _extract_session_id_from_callback(data: str) -> str | None:
    """Extract 8-char session ID from callback data like 'approve_bb4115ba'."""
    parts = data.split("_")
    for part in reversed(parts):
        if len(part) == 8 and all(c in "0123456789abcdef" for c in part):
            return part
    return None


def _save_session(session: dict):
    """Persist updated session state to disk."""
    path = os.path.join(DRAFTS, f"session_{session['session_id']}.json")
    try:
        with open(path, "w") as f:
            json.dump(session, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Could not save session: {type(e).__name__}")


def _mark_session_done(session_id: str, outcome: str = "done"):
    path = os.path.join(DRAFTS, f"session_{session_id}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            data["status"]       = outcome
            data["completed_at"] = datetime.now().isoformat()
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# PUBLISH PIPELINE — called when Fayez approves posts
# ─────────────────────────────────────────────────────────────────────────────

def _do_publish(session: dict, platforms: list = None):
    """Run publish_all() and send the daily report. Marks session done."""
    posts  = session.get("current_posts") or session.get("posts", {})
    topic  = session.get("current_topic") or session.get("topic", {})
    image  = session.get("image_path")

    label = f"platforms: {', '.join(platforms)}" if platforms else "all platforms"
    _send(f"⏳ <b>Publishing to {label}...</b> Give me a moment.")

    try:
        from src.publisher import publish_all
        results = publish_all(posts, image, platforms=platforms)
    except Exception as e:
        logger.error(f"Publishing failed: {type(e).__name__}: {str(e)[:200]}")
        _send(f"⚠️ <b>Publishing failed:</b> <code>{type(e).__name__}</code>\nCheck logs.")
        return

    # Post-publish hooks
    try:
        from src.performance_tracker import record_post
        urn = results.get("linkedin_company_urn")
        if urn and urn != "posted":
            record_post(urn, "linkedin_company",
                        topic.get("topic", ""), topic.get("content_pillar", ""))
    except Exception:
        pass

    try:
        from src.weekly_calendar import mark_day_posted
        mark_day_posted(datetime.now().strftime("%A"))
    except Exception:
        pass

    if platforms is None or "instagram" in (platforms or []):
        try:
            from src.image_brief import send_image_brief
            send_image_brief(topic, posts)
        except Exception:
            pass

    if platforms is None or "tiktok" in (platforms or []):
        try:
            from src.tiktok_script import send_tiktok_script
            send_tiktok_script(topic, posts)
        except Exception:
            pass

    succeeded = [k for k, v in results.items() if (v is True or v == "manual") and k != "linkedin_company_urn"]
    failed    = [k for k, v in results.items() if v is False and k != "linkedin_company_urn"]
    manual    = [k for k, v in results.items() if v == "manual" and k != "linkedin_company_urn"]

    succeeded_labels = []
    for k in succeeded:
        lbl = k.replace("_", " ").title()
        if k in manual:
            lbl += " (manual)"
        succeeded_labels.append(lbl)

    try:
        from src.telegram_bot import _get_streak, _streak_message
        streak = _get_streak() if succeeded else 0
        streak_msg = _streak_message(streak)
    except Exception:
        streak_msg = ""

    report = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>Fincare SMM — Daily Report</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 {topic.get('topic', '')[:80]}\n\n"
        f"✅ Posted to: {', '.join(succeeded_labels) if succeeded_labels else 'none'}\n"
    )
    if failed:
        report += f"⚠️ Failed: {', '.join(failed)}\n"
    report += streak_msg
    _send(report)
    logger.success("Publishing complete.")

    _mark_session_done(session["session_id"], "done")


def _send_picker(session: dict):
    """Re-send the morning briefing keyboard for the active session."""
    try:
        from src.telegram_bot import _send_platform_picker
        posts        = session.get("current_posts") or session.get("posts", {})
        video_result = session.get("current_video") or session.get("video_result", {})
        is_monday    = datetime.now().weekday() == 0
        is_month_end = datetime.now().day == 1
        _send_platform_picker(TOKEN, CHAT_ID, session["session_id"], posts,
                              is_monday=is_monday, is_month_end=is_month_end,
                              video_result=video_result)
    except Exception as e:
        logger.warning(f"_send_picker failed: {type(e).__name__}")
        _send("👇 Use the buttons from the morning briefing to continue.")


# ─────────────────────────────────────────────────────────────────────────────
# CALLBACK HANDLER — routes all morning briefing button taps
# ─────────────────────────────────────────────────────────────────────────────

def _handle_callback(cb: dict, session: dict) -> bool:
    """
    Handle a callback_query for the active session.
    Returns True if session is complete (approved / skipped).
    Mutates session in-place (state, current_posts, current_video, waiting_for_text).
    """
    callback_id = cb["id"]
    data        = cb.get("data", "")
    session_id  = session["session_id"]

    # ── Noop (divider button) ──────────────────────────────────────────────
    if data == "noop":
        _answer(callback_id, "")
        return False

    # ── Mark as Done (manual posting — no auto-publish yet) ─────────────────
    if data == f"approve_{session_id}":
        _answer(callback_id, "✅ Marked as done!")
        topic_name = session.get("topic", {}).get("topic", "today's content")[:50]
        _send(
            f"✅ <b>Marked as done!</b>\n\n"
            f"<b>Topic:</b> {topic_name}\n\n"
            f"📁 <b>Your files:</b>\n"
            f"• Video: <code>~/Downloads/fincare-video/out/</code>\n"
            f"• Image: <code>drafts/images/</code>\n"
            f"• Posts: tap any platform button above to copy\n\n"
            f"Post manually — auto-publishing coming once APIs are connected."
        )
        _mark_session_done(session_id, "done")
        return True

    if data == f"do_post_all_{session_id}":
        _answer(callback_id, "✅ Posting now...")
        _do_publish(session)
        return True

    # ── Post single platform ──────────────────────────────────────────────
    for platform in PLATFORM_LABELS:
        if data == f"do_post_{platform}_{session_id}":
            _answer(callback_id, f"✅ Posting {PLATFORM_LABELS[platform][1]}...")
            _do_publish(session, platforms=[platform])
            return True

    # ── Cancel post ──────────────────────────────────────────────────────
    if data == f"cancel_post_{session_id}":
        _answer(callback_id, "↩️ Cancelled.")
        _send("No problem — back to the menu.")
        session["state"] = "picker"
        _send_picker(session)
        return False

    # ── Skip today ────────────────────────────────────────────────────────
    if data == f"reject_{session_id}":
        _answer(callback_id, "Got it.")
        from src.telegram_bot import _send_skip_reasons
        _send_skip_reasons(TOKEN, CHAT_ID, session_id)
        session["state"] = "skip_reason"
        return False

    # ── Skip reason selected ──────────────────────────────────────────────
    for label, code in SKIP_REASONS:
        if data == f"skip_{code}_{session_id}":
            _answer(callback_id, "Got it 👍")
            if code == "save_later":
                _send("Saved to drafts. I'll include this topic again next week. 📁")
            else:
                _send("Got it — skipping today. See you tomorrow 👋")
            _mark_session_done(session_id, f"skipped:{code}")
            return True

    # ── Edit all posts ────────────────────────────────────────────────────
    if data == f"edit_{session_id}":
        _answer(callback_id, "✏️ Tell me what to change.")
        _send(
            "✏️ <b>What should I change?</b>\n\n"
            "Type your feedback and I'll rewrite all posts.\n\n"
            "<i>Example: \"Make the hook more direct\" or \"Different angle about FOMO\"</i>"
        )
        session["waiting_for_text"] = "edit"
        session["state"] = "picker"
        return False

    # ── My Idea ───────────────────────────────────────────────────────────
    if data == f"idea_{session_id}":
        _answer(callback_id, "💡 Tell me your idea.")
        _send(
            "💡 <b>What's your topic idea?</b>\n\n"
            "Type it and I'll write fresh posts around it.\n\n"
            "<i>Example: \"Why people panic sell\" or \"Power of DCA\"</i>"
        )
        session["waiting_for_text"] = "idea"
        session["state"] = "picker"
        return False

    # ── Back to picker ────────────────────────────────────────────────────
    if data == f"back_{session_id}":
        _answer(callback_id, "↩️ Back.")
        session["state"] = "picker"
        _send_picker(session)
        return False

    # ── View platform ─────────────────────────────────────────────────────
    for platform in PLATFORM_LABELS:
        if data == f"view_{platform}_{session_id}":
            _answer(callback_id, "⏳ Loading preview...")
            _typing()
            session["state"] = f"viewing:{platform}"
            current_posts = session.get("current_posts") or session.get("posts", {})
            current_topic = session.get("current_topic") or session.get("topic", {})
            image_path    = session.get("image_path")
            # TikTok: show video if available, otherwise text
            if platform == "tiktok":
                current_video = session.get("current_video") or session.get("video_result", {})
                has_video = current_video.get("916") and os.path.exists(str(current_video.get("916", "")))
                if has_video:
                    try:
                        from src.video_agent import skill_send_preview
                        skill_send_preview(
                            current_video, current_video.get("props", {}),
                            current_topic, TOKEN, CHAT_ID, session_id,
                        )
                        return False
                    except Exception:
                        pass
            from src.telegram_bot import _send_platform_preview
            _send_platform_preview(TOKEN, CHAT_ID, session_id, platform,
                                   current_posts, current_topic, image_path)
            return False

    # ── Confirm post this platform ────────────────────────────────────────
    for platform in PLATFORM_LABELS:
        if data == f"confirm_post_{platform}_{session_id}":
            _answer(callback_id, f"✅ Posting {PLATFORM_LABELS[platform][1]}...")
            _do_publish(session, platforms=[platform])
            return True

    # ── Edit this platform only ────────────────────────────────────────────
    for platform in PLATFORM_LABELS:
        if data == f"edit_this_{platform}_{session_id}":
            _answer(callback_id, "✏️ What should I change?")
            _send(
                f"✏️ <b>What should I change in the {PLATFORM_LABELS[platform][1]} post?</b>\n\n"
                "<i>Just type your feedback.</i>"
            )
            session["waiting_for_text"] = f"edit_platform:{platform}"
            return False

    # ── Regen image ───────────────────────────────────────────────────────
    for platform in PLATFORM_LABELS:
        if data == f"regen_{platform}_{session_id}":
            _answer(callback_id, "🔄 Generating new image...")
            _typing()
            current_posts = session.get("current_posts") or session.get("posts", {})
            current_topic = session.get("current_topic") or session.get("topic", {})
            from src.telegram_bot import _send_platform_preview
            _send_platform_preview(TOKEN, CHAT_ID, session_id, platform,
                                   current_posts, current_topic, None)
            return False

    # ── 📹 VIDEO: Review auto-generated video ──────────────────────────────
    if data == f"video_review_{session_id}":
        _answer(callback_id, "🎬 Loading video...")
        _typing()
        session["state"] = "video"
        current_video = session.get("current_video") or session.get("video_result", {})
        from src.telegram_bot import _send_video_preview
        _send_video_preview(TOKEN, CHAT_ID, session_id, current_video)
        return False

    # ── 🎥 VIDEO: Record My Own — send script ──────────────────────────────
    if data == f"video_record_{session_id}":
        _answer(callback_id, "📝 Sending your script...")
        current_video = session.get("current_video") or session.get("video_result", {})
        from src.telegram_bot import _send_record_prompt
        _send_record_prompt(TOKEN, CHAT_ID, session_id, current_video)
        session["state"] = "awaiting_founder_video"
        return False

    # ── 🔄 VIDEO: New Version — full re-render ─────────────────────────────
    if data == f"video_new_{session_id}":
        _answer(callback_id, "🔄 Generating new version...")
        _send(
            "🔄 <b>Generating a new video version...</b>\n"
            "This takes 3–8 minutes. I'll send it when ready."
        )
        try:
            from src.video_agent import run as _run_agent
            from src.viral_spy import load_latest_viral_intel as _load_intel
            current_topic = session.get("current_topic") or session.get("topic", {})
            current_posts = session.get("current_posts") or session.get("posts", {})
            new_result    = _run_agent(current_topic, current_posts, _load_intel())
            current_video = session.get("current_video") or {}
            current_video.update(new_result)
            session["current_video"] = current_video
            from src.telegram_bot import _send_video_preview
            _send_video_preview(TOKEN, CHAT_ID, session_id, current_video)
        except Exception as e:
            _send(f"⚠️ Regeneration failed: <code>{type(e).__name__}</code>. Try again.")
        return False

    # ── 🎯 VIDEO: Different Hook ────────────────────────────────────────────
    if data == f"video_hook_{session_id}":
        _answer(callback_id, "🎯 Trying a different hook...")
        current_video = session.get("current_video") or session.get("video_result", {})
        variants      = current_video.get("variants", [])
        current_props = current_video.get("props") or {}
        next_props    = next(
            (v for v in variants if v.get("hook") != current_props.get("hook")), None
        )
        if next_props:
            _send(f"🎯 <b>Rendering:</b> <i>{next_props.get('hook','')}</i>\n~3–8 min...")
            try:
                from src.video_agent import skill_render, skill_extract_thumbnail
                ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
                new_paths = skill_render(next_props, formats=["916"], timestamp=f"{ts}_v2")
                thumb     = skill_extract_thumbnail(new_paths.get("916"), 3.0) if new_paths.get("916") else None
                current_video.update({"916": new_paths.get("916"), "thumbnail": thumb, "props": next_props})
                current_video["variants"] = [v for v in variants if v.get("hook") != next_props.get("hook")]
                session["current_video"]  = current_video
                from src.telegram_bot import _send_video_preview
                _send_video_preview(TOKEN, CHAT_ID, session_id, current_video)
            except Exception as e:
                _send(f"⚠️ Hook swap failed: <code>{type(e).__name__}</code>")
        else:
            _send("No more variants — tap 🔄 New Version for a full regenerate.")
        return False

    # ── ✅ VIDEO: Use This ──────────────────────────────────────────────────
    if data == f"video_use_{session_id}":
        _answer(callback_id, "✅ Video approved!")
        current_video = session.get("current_video") or session.get("video_result", {})
        current_posts = session.get("current_posts") or session.get("posts", {})
        os.makedirs(DRAFTS, exist_ok=True)
        with open(os.path.join(DRAFTS, f"approved_video_{datetime.now().strftime('%Y%m%d')}.json"), "w") as f:
            json.dump({
                "props":       current_video.get("props"),
                "916":         current_video.get("916"),
                "11":          current_video.get("11"),
                "topic":       session.get("topic", {}),
                "approved_at": datetime.now().isoformat(),
            }, f, indent=2)
        _send("🎬 <b>Video approved and saved!</b>\n\nSending your posting guide...")
        try:
            from src.video_agent import skill_copy_script
            _send(skill_copy_script(current_video.get("props", {}), current_posts))
        except Exception:
            pass
        return False

    # ── 📋 VIDEO: Copy Script ──────────────────────────────────────────────
    if data == f"video_script_{session_id}":
        _answer(callback_id, "📋 Building posting guide...")
        current_video = session.get("current_video") or session.get("video_result", {})
        current_posts = session.get("current_posts") or session.get("posts", {})
        try:
            from src.video_agent import skill_copy_script
            _send(skill_copy_script(current_video.get("props", {}), current_posts))
        except Exception as e:
            _send(f"⚠️ Script failed: <code>{type(e).__name__}</code>")
        return False

    # ── 🔄 VIDEO: Re-edit founder video ───────────────────────────────────
    if data == f"video_reedit_{session_id}":
        _answer(callback_id, "🔄 Re-editing...")
        _send(
            "🔄 <b>Re-editing your video.</b>\n\n"
            "Reply with editing notes, e.g.:\n"
            "  <code>no captions</code> · <code>SPY</code> · <code>fa</code> · <code>pip</code>"
        )
        session["state"] = "awaiting_founder_video"
        return False

    # ── Info menu: Weekly Plan ─────────────────────────────────────────────
    if data == f"menu_weekly_{session_id}":
        _answer(callback_id, "📅 Loading weekly plan...")
        _typing()
        from src.telegram_bot import _menu_weekly
        _menu_weekly(TOKEN, CHAT_ID)
        return False

    # ── Info menu: Performance ─────────────────────────────────────────────
    if data == f"menu_performance_{session_id}":
        _answer(callback_id, "📊 Loading performance data...")
        _typing()
        from src.telegram_bot import _menu_performance
        _menu_performance(TOKEN, CHAT_ID)
        return False

    # ── Info menu: Competitors ──────────────────────────────────────────────
    if data == f"menu_competitors_{session_id}":
        _answer(callback_id, "🕵️ Loading competitor intel...")
        from src.telegram_bot import _menu_competitors
        _menu_competitors(TOKEN, CHAT_ID)
        return False

    # ── Info menu: Monthly Report ───────────────────────────────────────────
    if data == f"menu_monthly_{session_id}":
        _answer(callback_id, "📈 Loading monthly report...")
        _typing()
        from src.telegram_bot import _menu_monthly
        _menu_monthly(TOKEN, CHAT_ID)
        return False

    # Unknown callback — just clear the spinner
    _answer(callback_id, "")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# TEXT HANDLER — processes text when session is waiting for feedback/idea
# ─────────────────────────────────────────────────────────────────────────────

def _handle_session_text(text: str, session: dict):
    """Handle a text message when the session has a waiting_for_text state."""
    waiting    = session.get("waiting_for_text")
    session_id = session["session_id"]
    current_posts = session.get("current_posts") or session.get("posts", {})
    current_topic = session.get("current_topic") or session.get("topic", {})

    if waiting == "edit" and text:
        _send("✏️ <b>Rewriting posts...</b> Give me a moment.")
        _typing()
        try:
            from src.writer import rewrite_posts
            new_posts = rewrite_posts(current_topic, current_posts, text)
            session["current_posts"]    = new_posts
            session["waiting_for_text"] = None
            session["rewrites"]         = session.get("rewrites", 0) + 1
            _save_session(session)
            _send("✅ <b>Posts rewritten!</b>")
            _send_picker(session)
        except Exception as e:
            _send(f"⚠️ Rewrite failed: <code>{type(e).__name__}</code>")
        return

    if waiting == "idea" and text:
        _send("💡 <b>Writing fresh posts from your idea...</b> Give me a moment.")
        _typing()
        try:
            from src.writer import write_posts_from_idea
            new_topic, new_posts = write_posts_from_idea(text)
            session["current_topic"]    = new_topic
            session["current_posts"]    = new_posts
            session["waiting_for_text"] = None
            session["rewrites"]         = session.get("rewrites", 0) + 1
            _save_session(session)
            _send("✅ <b>Posts written!</b>")
            _send_picker(session)
        except Exception as e:
            _send(f"⚠️ Writing from idea failed: <code>{type(e).__name__}</code>")
        return

    if waiting and waiting.startswith("edit_platform:") and text:
        platform = waiting.split(":", 1)[1]
        _send(f"✏️ <b>Rewriting {PLATFORM_LABELS.get(platform, ('',''))[1] or platform} post...</b>")
        _typing()
        try:
            from src.writer import rewrite_single_platform
            new_posts = rewrite_single_platform(current_topic, current_posts, platform, text)
            session["current_posts"]    = new_posts
            session["waiting_for_text"] = None
            session["rewrites"]         = session.get("rewrites", 0) + 1
            _save_session(session)
            _send(f"✅ <b>{platform} rewritten!</b>")
            from src.telegram_bot import _send_platform_preview
            _send_platform_preview(TOKEN, CHAT_ID, session_id, platform,
                                   new_posts, current_topic, session.get("image_path"))
        except Exception as e:
            _send(f"⚠️ Platform rewrite failed: <code>{type(e).__name__}</code>")
        return


# ─────────────────────────────────────────────────────────────────────────────
# FOUNDER VIDEO HANDLER (Mode 2)
# ─────────────────────────────────────────────────────────────────────────────

def _handle_video(msg: dict, session: dict = None):
    """Founder sends raw video → full edit pipeline."""
    raw_video = msg.get("video") or msg.get("video_note")
    if not raw_video:
        return False

    file_id = raw_video.get("file_id")
    caption = msg.get("caption", "").strip()

    _send(
        "🎬 <b>Video received!</b> Processing now...\n\n"
        "0️⃣ Cropping to 9:16\n"
        "1️⃣ Studio audio (noise removal + EQ)\n"
        "2️⃣ Language detection + auto-captions\n"
        "3️⃣ FinCare lower third + overlays\n"
        "4️⃣ Chart data (if ticker in caption)\n\n"
        "<i>~2–3 minutes...</i>"
    )

    try:
        raw_path = _download_file(file_id)

        from src.founder_studio import skill_parse_caption_instructions, edit_founder_video
        instr = skill_parse_caption_instructions(caption) if caption else {}

        notes = []
        if instr.get("ticker"):                      notes.append(f"📊 Chart: <b>{instr['ticker']}</b>")
        if instr.get("language") and instr["language"] != "en":
                                                      notes.append(f"🌐 Language: <b>{instr['language']}</b>")
        if not instr.get("show_captions", True):     notes.append("💬 Captions: <b>off</b>")
        if instr.get("pip"):                          notes.append(f"🖼 PiP: <b>{instr.get('pip_position','bottom_right')}</b>")
        if instr.get("trim_start", 0):               notes.append(f"✂️ Trim start: -{instr['trim_start']}s")
        if instr.get("accent_color"):                notes.append(f"🎨 Color: {instr['accent_color']}")
        if notes:
            _send("🧠 <b>Caption understood:</b>\n" + "\n".join(notes))

        props = None
        if session:
            cv    = session.get("current_video") or session.get("video_result", {})
            props = cv.get("props") if cv else None

        final = edit_founder_video(raw_path, props=props, instructions=instr)

        if final and os.path.exists(final):
            size_mb = os.path.getsize(final) / (1024 * 1024)
            if size_mb > 45:
                try:
                    from src.video_agent import _compress_for_telegram_path
                    final = _compress_for_telegram_path(final)
                except Exception:
                    pass

            applied = ["9:16 crop", "studio audio"]
            if instr.get("show_captions", True):    applied.append("auto-captions")
            if instr.get("show_lower_third", True): applied.append("FinCare lower third")
            if instr.get("ticker"):                  applied.append(f"{instr['ticker']} chart")
            if instr.get("pip"):                     applied.append("PiP face")

            if session:
                from src.telegram_bot import _send_founder_video_actions
                _send_founder_video_actions(TOKEN, CHAT_ID, session["session_id"], final, applied)
                session["state"]              = "founder_video_review"
                session["last_founder_video"] = final
                _save_session(session)
            else:
                _send_video_file(final,
                    f"✅ <b>Done! ({size_mb:.1f}MB)</b>\n\n"
                    f"🎨 Applied: {' · '.join(applied)}\n\n"
                    "Send another video or use /script for today's script."
                )
            logger.success(f"Founder edit delivered: {os.path.basename(final)}")
        else:
            _send("⚠️ Edit pipeline failed. Check that ffmpeg is installed.")

    except ValueError as e:
        _send(f"⚠️ {str(e)}")
    except Exception as e:
        logger.error(f"Video handle error: {type(e).__name__}: {str(e)[:200]}")
        _send(f"⚠️ Unexpected error: <code>{type(e).__name__}: {str(e)[:100]}</code>")

    return True


# ─────────────────────────────────────────────────────────────────────────────
# SLASH COMMAND HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _handle_command(text: str, session: dict = None) -> bool:
    cmd = text.lower().strip()

    if cmd in ("/help", "/start"):
        _send(
            "👋 <b>Fincare Founder Bot</b>\n\n"
            "Commands:\n"
            "/resend — resend today's pending briefing\n"
            "/script — get today's script to record\n"
            "/topic <idea> — auto-generate video from idea\n"
            "/chart SPY 1mo — fetch real stock chart\n"
            "/status — show active briefing session\n"
            "/help — show this message\n\n"
            "📹 <b>Send any video</b> → I'll edit it:\n"
            "  • Studio audio enhancement\n"
            "  • Auto-captions (any language)\n"
            "  • FinCare lower third\n"
            "  • Chart overlay (add ticker in caption)\n\n"
            "<b>Caption tips:</b>\n"
            "  <code>SPY</code> → adds S&P 500 chart\n"
            "  <code>fa</code> → Persian · <code>es</code> → Spanish\n"
            "  <code>no captions</code> → disable captions\n"
            "  <code>pip</code> → face-in-corner mode\n"
            "  <code>cut first 5 seconds</code> → trim start"
        )
        return True

    if cmd == "/resend":
        pending = _load_pending_session()
        if pending:
            from src.telegram_bot import send_approval_request
            try:
                send_approval_request(
                    topic        = pending.get("topic", {}),
                    posts        = pending.get("posts", {}),
                    image_path   = pending.get("image_path"),
                    video_result = pending.get("video_result", {}),
                    qc_summary   = pending.get("quality_summary", ""),
                    session_id   = pending["session_id"],
                )
                _send(f"✅ Briefing resent! Buttons are live.")
                # Return the loaded session so the polling loop can update it
                return True, pending
            except Exception as e:
                _send(f"⚠️ Resend failed: <code>{type(e).__name__}: {str(e)[:80]}</code>")
        else:
            _send("No pending session found. Run the daily pipeline first.")
        return True, None

    if cmd == "/status":
        if session:
            _send(
                f"📋 <b>Active session: {session['session_id']}</b>\n\n"
                f"Topic: {session.get('topic', {}).get('topic', '—')[:60]}\n"
                f"State: {session.get('state', 'picker')}\n"
                f"Rewrites: {session.get('rewrites', 0)}\n"
                f"Created: {session.get('created_at', '—')[:16]}"
            )
        else:
            _send("No active session. Morning pipeline will send a new briefing tomorrow.")
        return True

    if cmd == "/script":
        props = None
        if session:
            cv    = session.get("current_video") or session.get("video_result", {})
            props = cv.get("props") if cv else None
        if not props:
            files = sorted(glob.glob(os.path.join(DRAFTS, "video_props_*.json")), reverse=True)
            if files:
                try:
                    with open(files[0]) as f:
                        data = json.load(f)
                    props = data.get("best") or data.get("props") or {}
                except Exception:
                    pass
        if props:
            hook    = props.get("hook", "")
            insight = props.get("insight", "")
            stat    = props.get("stat", "")
            cta     = props.get("ctaText", "")
            bullets = props.get("bullets", [])
            lines   = ["📝 <b>Your Script — Record this:</b>\n"]
            if hook:    lines.append(f"🎣 <b>Hook:</b>\n<i>{hook}</i>\n")
            if bullets:
                lines.append("📌 <b>Key points to cover:</b>")
                for b in bullets:
                    lines.append(f"  • {b}")
                lines.append("")
            elif insight:
                lines.append(f"💡 <b>Core insight:</b>\n{insight}\n")
            if stat: lines.append(f"📊 <b>Stat to mention:</b> {stat}\n")
            if cta:  lines.append(f"📣 <b>Close with:</b>\n{cta} — aifincare.com")
            lines += [
                "\n━━━━━━━━━━━━━━━━━",
                "Record yourself, then send the video here.",
                "Add ticker (e.g. <code>SPY</code>) or language (<code>fa</code>, <code>es</code>) in caption.",
            ]
            _send("\n".join(lines))
        else:
            _send("⚠️ No script yet. Run the morning pipeline first or use:\n<code>/topic Your topic here</code>")
        return True

    if cmd.startswith("/chart"):
        parts  = text.split()
        ticker = parts[1].upper() if len(parts) > 1 else "SPY"
        period = parts[2] if len(parts) > 2 else "1mo"
        _send(f"📊 Fetching <b>{ticker}</b> ({period})...")
        try:
            from src.founder_studio import skill_fetch_chart_data, skill_render_chart_png
            data = skill_fetch_chart_data(ticker, period)
            if data:
                style = "crash" if data["trend"] == "down" else "terminal"
                png   = skill_render_chart_png(data, style=style)
                if png:
                    sign = "+" if data["change_pct"] >= 0 else ""
                    _send_photo(png,
                        f"📈 <b>{ticker}</b> — {period}\n"
                        f"<b>{sign}{data['change_pct']}%</b> | Current: ${data['current']}\n"
                        f"Peak: ${data['peak_price']} on {data['peak_date']}\n"
                        f"Low:  ${data['trough_price']} on {data['trough_date']}"
                    )
                else:
                    _send(f"⚠️ Chart render failed (data: {data['change_pct']:+.2f}%)")
            else:
                _send(f"⚠️ No data for <b>{ticker}</b>. Try: SPY, AAPL, TSLA, BTC-USD")
        except Exception as e:
            _send(f"⚠️ Chart error: <code>{type(e).__name__}</code>")
        return True

    if cmd.startswith("/topic "):
        idea = text[7:].strip()
        if idea:
            _send(f"💡 Got it: <b>{idea}</b>\n\nGenerating video props...")
            try:
                from src.video_agent import (skill_generate_props, skill_select_format,
                                             skill_render, skill_extract_thumbnail, skill_send_preview)
                topic_obj = {
                    "topic":             idea,
                    "angle":             f"Behavioural finance angle on: {idea}",
                    "content_pillar":    "STORY",
                    "emotional_trigger": "anxiety",
                    "is_breaking":       "breaking" in idea.lower() or "crash" in idea.lower(),
                }
                fmt      = skill_select_format(topic_obj)
                variants = skill_generate_props(topic_obj, None, n=3, video_format=fmt)
                best     = variants[0]
                _send(
                    f"✅ <b>Props (Format {fmt}):</b>\n\n"
                    f"🎣 <b>Hook:</b> {best['hook']}\n"
                    f"📊 <b>Stat:</b> {best.get('stat', '')}\n"
                    f"💡 <b>Insight:</b> {best.get('insight', '')}\n"
                    "Rendering video now (3–8 min)..."
                )
                render_paths = skill_render(best, video_format=fmt)
                thumb = skill_extract_thumbnail(render_paths.get("916"), 3.0) if render_paths.get("916") else None
                paths = dict(render_paths, thumbnail=thumb)
                ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
                skill_send_preview(paths, best, topic_obj, TOKEN, CHAT_ID, f"bot_{ts}")
            except Exception as e:
                logger.error(f"/topic error: {type(e).__name__}: {str(e)[:200]}")
                _send(f"⚠️ Generation failed: <code>{type(e).__name__}: {str(e)[:80]}</code>")
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN POLLING LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run():
    """Main polling loop — handles briefing callbacks AND founder commands forever."""
    logger.step("Fincare Founder Bot starting...")

    # ── Load any pending morning briefing session ──────────────────────────
    session = _load_pending_session()
    if session:
        session.setdefault("state",            "picker")
        session.setdefault("waiting_for_text", None)
        session.setdefault("current_posts",    session.get("posts", {}))
        session.setdefault("current_topic",    session.get("topic", {}))
        session.setdefault("current_video",    session.get("video_result", {}))
        session.setdefault("rewrites",         0)
        logger.success(f"Active session: {session['session_id']} — buttons ready")
        _send(
            f"🤖 <b>Bot back online!</b>\n\n"
            f"Active briefing: <b>{session.get('topic', {}).get('topic', '—')[:60]}</b>\n"
            f"Session: <code>{session['session_id']}</code>\n\n"
            "Tap any button from the morning briefing to continue."
        )
    else:
        _send(
            "🤖 <b>Founder Bot is online!</b>\n\n"
            "Send me a video to edit, or use /help to see all commands."
        )

    last_update_id = 0

    while True:
        try:
            resp = _api("getUpdates", {
                "offset":          last_update_id + 1,
                "timeout":         25,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=30)

            updates = resp.get("result", [])
            for update in updates:
                last_update_id = update["update_id"]

                # ── Callback query (button tap) ───────────────────────────
                if "callback_query" in update:
                    cb        = update["callback_query"]
                    from_chat = str(cb.get("from", {}).get("id", ""))
                    data      = cb.get("data", "")

                    if from_chat != CHAT_ID:
                        _answer(cb["id"], "")
                        continue

                    if session and session["session_id"] in data:
                        done = _handle_callback(cb, session)
                        _save_session(session)
                        if done:
                            session = None
                    elif data == "noop":
                        _answer(cb["id"], "")
                    else:
                        # Bot restarted — try to recover session from disk
                        recovered_id = _extract_session_id_from_callback(data)
                        recovered    = _load_session_by_id(recovered_id) if recovered_id else None
                        if recovered:
                            session = recovered
                            logger.info(f"Session recovered mid-callback: {recovered_id}")
                            done = _handle_callback(cb, session)
                            _save_session(session)
                            if done:
                                session = None
                        else:
                            _answer(cb["id"], "⚠️ Session not found — run /resend to get the briefing again.")
                    continue

                # ── Text / video message ──────────────────────────────────
                if "message" in update:
                    msg       = update["message"]
                    from_chat = str(msg.get("chat", {}).get("id", ""))
                    if from_chat != CHAT_ID:
                        continue

                    text = msg.get("text", "").strip()

                    # Slash commands
                    if text.startswith("/"):
                        result = _handle_command(text, session)
                        # /resend returns (True, new_session) to update in-memory session
                        if isinstance(result, tuple):
                            _, new_session = result
                            if new_session:
                                new_session.setdefault("state",            "picker")
                                new_session.setdefault("waiting_for_text", None)
                                new_session.setdefault("current_posts",    new_session.get("posts", {}))
                                new_session.setdefault("current_topic",    new_session.get("topic", {}))
                                new_session.setdefault("current_video",    new_session.get("video_result", {}))
                                new_session.setdefault("rewrites",         0)
                                session = new_session
                        continue

                    # Video upload — Mode 2
                    if msg.get("video") or msg.get("video_note"):
                        _handle_video(msg, session)
                        continue

                    # Text while session is waiting for feedback/idea
                    if session and session.get("waiting_for_text") and text:
                        _handle_session_text(text, session)
                        _save_session(session)
                        continue

                    # Fallback
                    if text:
                        _send(
                            f"💬 Got it. To generate a video from this idea:\n"
                            f"<code>/topic {text[:60]}</code>"
                        )

        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            _send("👋 Founder Bot going offline.")
            break
        except Exception as e:
            logger.warning(f"Poll error: {type(e).__name__} — retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        sys.exit(1)
    run()
