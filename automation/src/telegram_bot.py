import os
import re
import time
import uuid
import requests
from datetime import datetime
from utils.logger import SecureLogger
from utils.validators import validate_telegram_chat_id

logger = SecureLogger("telegram_bot")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

PLATFORM_LABELS = {
    "linkedin_company": ("🏢", "LinkedIn Company"),
    "instagram":        ("📸", "Instagram"),
    "tiktok":           ("🎵", "TikTok"),
    "threads":          ("🧵", "Threads"),
}

PLATFORM_POST_KEY = {
    "linkedin_company": "linkedin_company",
    "instagram":        "instagram_caption",
    "tiktok":           "tiktok_caption",
    "threads":          "threads_post",
}

PLATFORM_HASHTAG_KEY = {
    "linkedin_company": "hashtags_linkedin",
    "instagram":        "hashtags_instagram",
    "tiktok":           "hashtags_tiktok",
    "threads":          "hashtags_tiktok",
}

PLATFORM_CHAR_LIMIT = {
    "linkedin_company": 700,
    "instagram":        2200,
    "tiktok":           2200,
    "threads":          500,
}

# Skip reason labels shown when user taps ❌
SKIP_REASONS = [
    ("🎯 Not relevant today",  "not_relevant"),
    ("✍️ Poor quality",        "poor_quality"),
    ("🗣️ Wrong tone",          "wrong_tone"),
    ("💾 Save for later",      "save_later"),
]


# ─────────────────────────────────────────────────────────────────────────────
# LOW-LEVEL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _api(token: str, method: str, payload: dict = None) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    try:
        resp = requests.post(url, json=payload or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"Telegram API error on {method}: {type(e).__name__}")
        raise


def _send_message(token: str, chat_id: str, text: str):
    """Sends HTML message, splitting at 4096-char Telegram limit."""
    MAX = 4096
    while text:
        chunk, text = text[:MAX], text[MAX:]
        try:
            _api(token, "sendMessage", {
                "chat_id":    chat_id,
                "text":       chunk,
                "parse_mode": "HTML",
            })
        except Exception:
            plain = re.sub(r"<[^>]+>", "", chunk)
            try:
                _api(token, "sendMessage", {"chat_id": chat_id, "text": plain})
            except Exception:
                pass


def _send_photo(token: str, chat_id: str, image_path: str, caption: str):
    url = TELEGRAM_API.format(token=token, method="sendPhoto")
    try:
        with open(image_path, "rb") as img:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"photo": img},
                timeout=30
            )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Photo send failed ({type(e).__name__}) — sending text only.")
        _send_message(token, chat_id, caption)


def _send_video(token: str, chat_id: str, video_path: str, caption: str):
    url = TELEGRAM_API.format(token=token, method="sendVideo")
    try:
        with open(video_path, "rb") as vid:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"video": vid},
                timeout=120
            )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Video send failed ({type(e).__name__}) — sending text only.")
        _send_message(token, chat_id, caption)


def _answer_callback(token: str, callback_id: str, text: str):
    try:
        _api(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text
        })
    except Exception:
        pass


def _typing(token: str, chat_id: str):
    """Shows 'typing...' indicator — free, instant."""
    try:
        _api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except Exception:
        pass


def _get_streak() -> int:
    """Counts consecutive days posted from performance_log.json."""
    try:
        import json
        log_path = "logs/performance_log.json"
        if not os.path.exists(log_path):
            return 0
        with open(log_path) as f:
            posts = json.load(f)
        if not posts:
            return 0
        # Get unique posted dates sorted descending
        from datetime import date, timedelta
        dates = sorted(
            set(p["posted_at"][:10] for p in posts if "posted_at" in p),
            reverse=True
        )
        if not dates:
            return 0
        streak = 0
        check = date.today()
        for d in dates:
            if d == str(check) or d == str(check - timedelta(days=1)):
                streak += 1
                check = date.fromisoformat(d)
            else:
                break
        return streak
    except Exception:
        return 0


def _streak_message(streak: int) -> str:
    if streak <= 0:
        return ""
    if streak >= 20:
        return f"\n\n20 posts. Consistency is the strategy. 🧠"
    if streak >= 10:
        return f"\n\n10 posts in — you're building momentum. 💪"
    if streak >= 5:
        return f"\n\nFull week! 🏆 Keep it going."
    return f"\n\nThat's post #{streak} — keep the streak alive. 🔥"


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning Fayez! ☀️"
    if hour < 17:
        return "Hey Fayez 👋"
    return "Evening Fayez 🌙"


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL REQUEST — single greeting message + picker
# ─────────────────────────────────────────────────────────────────────────────

def send_approval_request(topic: dict, posts: dict, image_path: str = None) -> str:
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")

    session_id = str(uuid.uuid4())[:8]

    pillar  = topic.get("content_pillar", "—")
    trigger = topic.get("emotional_trigger", "—").upper()
    image_line = "  🖼️ Image ready" if image_path else ""

    platforms_written = []
    if posts.get("linkedin_company"):  platforms_written.append("LinkedIn")
    if posts.get("instagram_caption"): platforms_written.append("Instagram")
    if posts.get("tiktok_caption"):    platforms_written.append("TikTok")
    if posts.get("threads_post"):      platforms_written.append("Threads")

    platforms_str = " · ".join(platforms_written) if platforms_written else "none"
    manual_note   = "\n⚠️ <i>LinkedIn API pending — manual mode active</i>" if os.getenv("LINKEDIN_MANUAL") else ""

    # Detect day-specific context
    is_monday  = datetime.now().weekday() == 0
    is_month_end = datetime.now().day == 1
    today_name = datetime.now().strftime("%A, %B %d")

    # ── Marketing manager morning briefing ─────────────────
    greeting = (
        f"{_greeting()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📋 Your marketing brief for {today_name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>TODAY'S POST — ready for review:</b>\n"
        f"📌 {topic.get('topic', '')}\n"
        f"🎯 {topic.get('angle', '')}\n"
        f"📊 {topic.get('key_stat', '')}\n"
        f"🏛️ {pillar}  ⚡ {trigger}{image_line}\n\n"
        f"Posts written for: {platforms_str}"
        f"{manual_note}"
    )

    # Add what's available in the info menu
    menu_available = []
    menu_available.append("📅 Weekly Content Plan")
    menu_available.append("📊 Post Performance")
    if is_monday:
        menu_available.append("🕵️ Competitor Intel (fresh this morning)")
    else:
        menu_available.append("🕵️ Competitor Intel (last Monday)")
    if is_month_end:
        menu_available.append("📈 Monthly Report (new!)")

    greeting += f"\n\n<b>Also available:</b>\n" + "\n".join(f"  · {m}" for m in menu_available)
    greeting += "\n\n<i>Tap a button below to get started.</i>"

    _send_message(token, chat_id, greeting)

    # Platform picker + info menu
    _send_platform_picker(token, chat_id, session_id, posts, is_monday=is_monday, is_month_end=is_month_end)

    logger.success(f"Morning briefing sent (session: {session_id})")
    return session_id


def _send_platform_picker(token: str, chat_id: str, session_id: str, posts: dict,
                           is_monday: bool = False, is_month_end: bool = False):
    linkedin_c = posts.get("linkedin_company") or ""
    instagram  = posts.get("instagram_caption") or ""
    tiktok     = posts.get("tiktok_caption") or ""
    threads    = posts.get("threads_post") or ""

    view_row = []
    if linkedin_c:
        view_row.append({"text": "🏢 LinkedIn", "callback_data": f"view_linkedin_company_{session_id}"})
    if instagram:
        view_row.append({"text": "📸 Instagram", "callback_data": f"view_instagram_{session_id}"})

    view_row2 = []
    if tiktok:
        view_row2.append({"text": "🎵 TikTok", "callback_data": f"view_tiktok_{session_id}"})
    if threads:
        view_row2.append({"text": "🧵 Threads", "callback_data": f"view_threads_{session_id}"})

    # ── Post action rows ──
    keyboard_rows = [
        [
            {"text": "✅ Post ALL",   "callback_data": f"approve_{session_id}"},
            {"text": "❌ Skip Today", "callback_data": f"reject_{session_id}"}
        ],
    ]
    if view_row:
        keyboard_rows.append(view_row)
    if view_row2:
        keyboard_rows.append(view_row2)
    keyboard_rows.append([
        {"text": "✏️ Edit All", "callback_data": f"edit_{session_id}"},
        {"text": "💡 My Idea",  "callback_data": f"idea_{session_id}"}
    ])

    # ── Info menu rows ──
    keyboard_rows.append([{"text": "─────── Info & Reports ───────", "callback_data": "noop"}])
    info_row1 = [
        {"text": "📅 Weekly Plan",    "callback_data": f"menu_weekly_{session_id}"},
        {"text": "📊 Post Review",    "callback_data": f"menu_performance_{session_id}"},
    ]
    info_row2 = [
        {"text": "🕵️ Competitors",   "callback_data": f"menu_competitors_{session_id}"},
    ]
    if is_month_end:
        info_row2.append({"text": "📈 Monthly Report", "callback_data": f"menu_monthly_{session_id}"})
    keyboard_rows.append(info_row1)
    keyboard_rows.append(info_row2)

    _api(token, "sendMessage", {
        "chat_id":      chat_id,
        "text":         "👇 <b>Today's post actions</b> — or tap a report below:",
        "parse_mode":   "HTML",
        "reply_markup": {"inline_keyboard": keyboard_rows},
    })


def _send_platform_preview(token: str, chat_id: str, session_id: str,
                            platform: str, posts: dict, topic: dict, image_path: str | None):
    from src.image_generator import generate_post_image

    emoji, label  = PLATFORM_LABELS.get(platform, ("📌", platform))
    post_key      = PLATFORM_POST_KEY.get(platform, platform)
    hashtag_key   = PLATFORM_HASHTAG_KEY.get(platform, "")
    char_limit    = PLATFORM_CHAR_LIMIT.get(platform, 3000)
    post_text     = posts.get(post_key) or ""
    hashtags      = posts.get(hashtag_key, "") or ""
    pillar        = topic.get("content_pillar", "—")
    trigger       = topic.get("emotional_trigger", "—").upper()

    if not post_text:
        _send_message(token, chat_id, f"<i>No {label} post written yet.</i>")
        return

    _typing(token, chat_id)

    char_count  = len(post_text)
    char_status = "✅" if char_count <= char_limit else "⚠️"
    char_line   = f"{char_count} / {char_limit} chars {char_status}"
    manual_note = "\n📋 <i>Copy text above to post manually on LinkedIn</i>" if os.getenv("LINKEDIN_MANUAL") and platform == "linkedin_company" else ""

    if platform == "tiktok":
        caption = (
            f"{emoji} <b>{label} SCRIPT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{post_text}"
        )
        if hashtags:
            caption += f"\n\n─── Hashtags ───\n{hashtags}"
        caption += f"\n\n🏛️ {pillar} · ⚡ {trigger} · {char_line}"
        caption += "\n\n<i>ℹ️ TikTok saved as draft — publish manually until video API is live.</i>"
        _send_message(token, chat_id, caption)
    else:
        generated_path = image_path or generate_post_image(topic, posts, platform)
        caption = (
            f"{emoji} <b>{label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{post_text}"
        )
        if hashtags:
            caption += f"\n\n─── Hashtags ───\n{hashtags}"
        caption += f"\n\n🏛️ {pillar} · ⚡ {trigger} · {char_line}"
        caption += manual_note

        if generated_path and os.path.exists(generated_path):
            _send_photo(token, chat_id, generated_path, caption)
        else:
            _send_message(token, chat_id, caption)

    # Per-platform action buttons
    _api(token, "sendMessage", {
        "chat_id":    chat_id,
        "text":       f"Happy with this {label} post?",
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [
                {"text": "✅ Post This",    "callback_data": f"confirm_post_{platform}_{session_id}"},
                {"text": "✏️ Edit This",    "callback_data": f"edit_this_{platform}_{session_id}"},
            ],
            [
                {"text": "🔄 New Image",    "callback_data": f"regen_{platform}_{session_id}"},
                {"text": "⏭ Back",          "callback_data": f"back_{session_id}"},
            ]
        ]}
    })


def _send_confirm_post(token: str, chat_id: str, session_id: str, platform: str):
    """Sends confirmation message before posting. Change #3."""
    label = PLATFORM_LABELS.get(platform, ("", platform))[1] if platform != "all" else "all platforms"
    _api(token, "sendMessage", {
        "chat_id":    chat_id,
        "text":       f"Posting to <b>{label}</b> now — last chance to cancel.",
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ Go ahead",   "callback_data": f"do_post_{platform}_{session_id}"},
            {"text": "✋ Cancel",     "callback_data": f"cancel_post_{session_id}"},
        ]]}
    })


def _send_skip_reasons(token: str, chat_id: str, session_id: str):
    """Sends skip reason picker. Change #5."""
    rows = [[{"text": label, "callback_data": f"skip_{code}_{session_id}"}]
            for label, code in SKIP_REASONS]
    _api(token, "sendMessage", {
        "chat_id":    chat_id,
        "text":       "No problem. Quick question — why are you skipping?",
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": rows}
    })


# ─────────────────────────────────────────────────────────────────────────────
# INFO MENU HELPERS — called inline from the approval loop
# ─────────────────────────────────────────────────────────────────────────────

def _menu_weekly(token: str, chat_id: str):
    """Sends the weekly content calendar inline."""
    try:
        from src.weekly_calendar import get_week_plan, build_telegram_calendar_message, load_calendar
        plan = load_calendar() or get_week_plan()
        message = build_telegram_calendar_message(plan)
        _send_message(token, chat_id, message)
    except Exception as e:
        _send_message(token, chat_id, f"⚠️ Couldn't load weekly plan: {type(e).__name__}")


def _menu_performance(token: str, chat_id: str):
    """Sends the weekly performance report inline."""
    try:
        from src.performance_tracker import build_weekly_report
        report = build_weekly_report()
        _send_message(token, chat_id, report)
    except Exception as e:
        _send_message(token, chat_id, f"⚠️ Couldn't load performance data: {type(e).__name__}")


def _menu_competitors(token: str, chat_id: str):
    """
    Sends competitor intelligence report inline.
    Uses cached data if fetched today; otherwise re-fetches live (free RSS, ~10s).
    """
    import json as _json
    import glob as _glob

    _typing(token, chat_id)
    try:
        from src.competitor_spy import _fetch_headlines, _build_report, COMPETITOR_FEEDS, NICHE_FEEDS

        today_str  = datetime.now().strftime("%Y-%m-%d")
        files      = sorted(_glob.glob("logs/competitor_spy_*.json"), reverse=True)
        cache_fresh = False
        data        = {}

        if files:
            # Check if cache file was written today
            cache_mtime = os.path.getmtime(files[0])
            cache_date  = datetime.fromtimestamp(cache_mtime).strftime("%Y-%m-%d")
            if cache_date == today_str:
                with open(files[0]) as f:
                    data = _json.load(f)
                cache_fresh = True

        if not cache_fresh:
            # Cache is stale or missing — fetch live
            _send_message(token, chat_id,
                "⏳ <b>Fetching fresh competitor data</b> — one moment, this takes ~10 seconds...")
            _typing(token, chat_id)
            competitor_data = {name: _fetch_headlines(name, url) for name, url in COMPETITOR_FEEDS.items()}
            niche_data      = {name: _fetch_headlines(name, url) for name, url in NICHE_FEEDS.items()}
            # Save fresh cache
            os.makedirs("logs", exist_ok=True)
            week_key = datetime.now().strftime("%Y_W%W")
            cache_path = f"logs/competitor_spy_{week_key}.json"
            with open(cache_path, "w") as f:
                _json.dump({"competitors": competitor_data, "niche": niche_data}, f, indent=2)
            data = {"competitors": competitor_data, "niche": niche_data}

        fetched_label = "fresh as of today" if not cache_fresh else f"fetched today · {datetime.now().strftime('%H:%M UTC')}"
        report = _build_report(data.get("competitors", {}), data.get("niche", {}))
        report += f"\n\n<i>🕐 Data: {fetched_label}</i>"

        # Append viral intel summary if available
        try:
            from src.viral_spy import load_latest_viral_intel, build_telegram_viral_summary
            brief = load_latest_viral_intel()
            if brief:
                report += build_telegram_viral_summary(brief)
        except Exception:
            pass

        _send_message(token, chat_id, report)

    except Exception as e:
        _send_message(token, chat_id, f"⚠️ Couldn't load competitor intel: {type(e).__name__}")


def _menu_monthly(token: str, chat_id: str):
    """Sends the monthly report inline."""
    try:
        from src.monthly_report import build_monthly_report
        report = build_monthly_report()
        _send_message(token, chat_id, report)
    except Exception as e:
        _send_message(token, chat_id, f"⚠️ Couldn't load monthly report: {type(e).__name__}")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL LOOP — state machine
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_approval(session_id: str, timeout_hours: float = 4.0,
                       posts: dict = None, topic: dict = None,
                       image_path: str = None,
                       video_result: dict = None) -> tuple[bool, str]:
    """
    State-machine approval loop.
    States: "picker" | "viewing:{platform}" | "confirming:{platform}" | "skip_reason"
    Returns (approved: bool, feedback: str)
    """
    token            = os.getenv("TELEGRAM_BOT_TOKEN")
    expected_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not expected_chat_id:
        raise EnvironmentError("TELEGRAM credentials not set.")

    timeout_seconds  = int(timeout_hours * 3600)
    poll_interval    = 30
    elapsed          = 0
    last_update_id   = 0
    state            = "picker"
    waiting_for_text = None
    current_posts    = posts or {}
    current_topic    = topic or {}
    current_image    = image_path
    current_video    = video_result or {}
    reminder_sent    = False
    _approved_video  = False   # tracks if user tapped ✅ Use This
    REMINDER_AFTER   = 3600  # 1 hour — send follow-up if no approval action

    logger.step(f"Waiting for approval (timeout: {timeout_hours}h, session: {session_id})...")

    while elapsed < timeout_seconds:
        time.sleep(poll_interval)
        elapsed += poll_interval

        try:
            result = _api(token, "getUpdates", {
                "offset":          last_update_id + 1,
                "timeout":         25,
                "allowed_updates": ["callback_query", "message"]
            })
        except Exception as e:
            logger.warning(f"Poll error (will retry): {type(e).__name__}")
            continue

        if not result.get("ok") or not result.get("result"):
            continue

        for update in result["result"]:
            last_update_id = update["update_id"]

            # ── Callback buttons ──────────────────────────────────
            if "callback_query" in update:
                cb        = update["callback_query"]
                from_chat = str(cb.get("from", {}).get("id", ""))
                data      = cb.get("data", "")

                if not validate_telegram_chat_id(from_chat, expected_chat_id):
                    continue

                # ── Post ALL — show confirmation first ────────────
                if data == f"approve_{session_id}":
                    _answer_callback(token, cb["id"], "One moment...")
                    _send_confirm_post(token, expected_chat_id, session_id, "all")
                    state = "confirming:all"
                    continue

                # ── Confirmed — actually post ─────────────────────
                if data == f"do_post_all_{session_id}":
                    _answer_callback(token, cb["id"], "✅ Posting now...")
                    logger.success("Post CONFIRMED (all platforms).")
                    return True, ""

                for platform in PLATFORM_LABELS:
                    if data == f"do_post_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], f"✅ Posting {PLATFORM_LABELS[platform][1]}...")
                        logger.success(f"Post CONFIRMED for {platform}.")
                        return True, f"PLATFORMS:{platform}"

                # ── Cancel post ───────────────────────────────────
                if data == f"cancel_post_{session_id}":
                    _answer_callback(token, cb["id"], "↩️ Cancelled.")
                    _send_message(token, expected_chat_id, "No problem — back to the menu.")
                    _send_platform_picker(token, expected_chat_id, session_id, current_posts)
                    state = "picker"
                    continue

                # ── Skip today — show reason picker ───────────────
                if data == f"reject_{session_id}":
                    _answer_callback(token, cb["id"], "Got it.")
                    _send_skip_reasons(token, expected_chat_id, session_id)
                    state = "skip_reason"
                    continue

                # ── Skip reason selected ──────────────────────────
                for label, code in SKIP_REASONS:
                    if data == f"skip_{code}_{session_id}":
                        _answer_callback(token, cb["id"], "Got it 👍")
                        if code == "save_later":
                            _send_message(token, expected_chat_id,
                                "Saved to drafts. I'll include this topic again next week. 📁")
                        else:
                            _send_message(token, expected_chat_id,
                                "Got it — skipping today. See you tomorrow 👋")
                        logger.info(f"Post SKIPPED. Reason: {code}")
                        return False, f"Rejected: {code}"

                # ── Edit all posts ────────────────────────────────
                if data == f"edit_{session_id}":
                    _answer_callback(token, cb["id"], "✏️ Tell me what to change.")
                    _send_message(token, expected_chat_id,
                        "✏️ <b>What should I change?</b>\n\n"
                        "Type your feedback and I'll rewrite all posts.\n\n"
                        "<i>Example: \"Make the hook more direct\" or \"Different angle about FOMO\"</i>"
                    )
                    waiting_for_text = "edit"
                    state = "picker"
                    continue

                # ── My Idea ───────────────────────────────────────
                if data == f"idea_{session_id}":
                    _answer_callback(token, cb["id"], "💡 Tell me your idea.")
                    _send_message(token, expected_chat_id,
                        "💡 <b>What's your topic idea?</b>\n\n"
                        "Type it and I'll write fresh posts around it.\n\n"
                        "<i>Example: \"Why people panic sell\" or \"Power of DCA\"</i>"
                    )
                    waiting_for_text = "idea"
                    state = "picker"
                    continue

                # ── Back to picker ────────────────────────────────
                if data == f"back_{session_id}":
                    _answer_callback(token, cb["id"], "↩️ Back.")
                    state = "picker"
                    _send_platform_picker(token, expected_chat_id, session_id, current_posts)
                    continue

                # ── View TikTok — video agent preview ────────────────
                if data == f"view_tiktok_{session_id}":
                    _answer_callback(token, cb["id"], "⏳ Loading video preview...")
                    _typing(token, expected_chat_id)
                    state = "viewing:tiktok"
                    has_video = current_video.get("916") and os.path.exists(current_video["916"])
                    if has_video:
                        try:
                            from src.video_agent import skill_send_preview
                            skill_send_preview(
                                current_video,
                                current_video.get("props", {}),
                                current_topic,
                                token, expected_chat_id, session_id,
                            )
                        except Exception as e:
                            logger.warning(f"skill_send_preview failed: {type(e).__name__}")
                            _send_platform_preview(token, expected_chat_id, session_id,
                                "tiktok", current_posts, current_topic, current_image)
                    else:
                        # No video — fall back to text script preview
                        _send_platform_preview(token, expected_chat_id, session_id,
                            "tiktok", current_posts, current_topic, current_image)
                    continue

                # ── Video: New Version — full regenerate ──────────────
                if data == f"regen_video_{session_id}":
                    _answer_callback(token, cb["id"], "🔄 Generating new version...")
                    _typing(token, expected_chat_id)
                    _send_message(token, expected_chat_id,
                        "🔄 <b>Generating a new video version...</b>\n"
                        "This takes 3–8 minutes. I'll send it when ready.")
                    try:
                        from src.video_agent import run as _run_agent
                        from src.viral_spy import load_latest_viral_intel as _load_intel
                        new_result = _run_agent(current_topic, current_posts, _load_intel())
                        current_video.update(new_result)
                        from src.video_agent import skill_send_preview
                        skill_send_preview(current_video, current_video.get("props", {}),
                            current_topic, token, expected_chat_id, session_id)
                    except Exception as e:
                        _send_message(token, expected_chat_id,
                            f"⚠️ Regeneration failed: {type(e).__name__}. Try again.")
                    continue

                # ── Video: Different Hook — render next variant ────────
                if data == f"diff_hook_{session_id}":
                    _answer_callback(token, cb["id"], "🎯 Trying a different hook...")
                    _typing(token, expected_chat_id)
                    variants = current_video.get("variants", [])
                    current_props = current_video.get("props", {})
                    # Find the next variant (different from current)
                    next_props = None
                    for v in variants:
                        if v.get("hook") != current_props.get("hook"):
                            next_props = v
                            break
                    if next_props:
                        _send_message(token, expected_chat_id,
                            f"🎯 <b>Rendering with hook:</b> <i>{next_props.get('hook','')}</i>\n"
                            "This takes 3–8 minutes...")
                        try:
                            from src.video_agent import skill_render, skill_extract_thumbnail, skill_send_preview
                            ts = current_video.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M%S"))
                            new_paths = skill_render(next_props, formats=["916"], timestamp=f"{ts}_v2")
                            thumb = skill_extract_thumbnail(new_paths.get("916"), 3.0) if new_paths.get("916") else None
                            current_video.update({"916": new_paths.get("916"), "thumbnail": thumb, "props": next_props})
                            # Remove used variant so next tap picks another
                            current_video["variants"] = [v for v in variants if v.get("hook") != next_props.get("hook")]
                            skill_send_preview(current_video, next_props, current_topic, token, expected_chat_id, session_id)
                        except Exception as e:
                            _send_message(token, expected_chat_id, f"⚠️ Hook swap failed: {type(e).__name__}")
                    else:
                        _send_message(token, expected_chat_id,
                            "No more variants available — tap 🔄 New Version for a full regenerate.")
                    continue

                # ── Video: Approve — save + send copy script ──────────
                if data == f"approve_video_{session_id}":
                    _answer_callback(token, cb["id"], "✅ Video saved!")
                    _approved_video = True
                    # Save final approved video record
                    import json as _json
                    os.makedirs("drafts", exist_ok=True)
                    approved_path = f"drafts/approved_video_{datetime.now().strftime('%Y%m%d')}.json"
                    with open(approved_path, "w") as _f:
                        _json.dump({
                            "props": current_video.get("props"),
                            "916":   current_video.get("916"),
                            "11":    current_video.get("11"),
                            "topic": current_topic,
                            "approved_at": datetime.now().isoformat(),
                        }, _f, indent=2)
                    _send_message(token, expected_chat_id,
                        "🎬 <b>Video approved!</b> Sending your manual posting guide...")
                    try:
                        from src.video_agent import skill_copy_script
                        script_msg = skill_copy_script(current_video.get("props", {}), current_posts)
                        _send_message(token, expected_chat_id, script_msg)
                    except Exception as e:
                        _send_message(token, expected_chat_id, f"⚠️ Script generation failed: {type(e).__name__}")
                    continue

                # ── Video: Copy Script — manual posting guide ─────────
                if data == f"copy_script_{session_id}":
                    _answer_callback(token, cb["id"], "📋 Building posting guide...")
                    try:
                        from src.video_agent import skill_copy_script
                        script_msg = skill_copy_script(current_video.get("props", {}), current_posts)
                        _send_message(token, expected_chat_id, script_msg)
                    except Exception as e:
                        _send_message(token, expected_chat_id, f"⚠️ Script failed: {type(e).__name__}")
                    continue

                # ── View platform ─────────────────────────────────
                for platform in PLATFORM_LABELS:
                    if data == f"view_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], "⏳ Loading preview...")
                        _typing(token, expected_chat_id)
                        state = f"viewing:{platform}"
                        _send_platform_preview(
                            token, expected_chat_id, session_id,
                            platform, current_posts, current_topic, current_image
                        )
                        break

                # ── Post this platform — post immediately (no confirmation) ──
                for platform in PLATFORM_LABELS:
                    if data == f"confirm_post_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], f"✅ Posting {PLATFORM_LABELS[platform][1]}...")
                        logger.success(f"Post CONFIRMED for {platform}.")
                        return True, f"PLATFORMS:{platform}"

                # ── Edit this platform only ───────────────────────
                for platform in PLATFORM_LABELS:
                    if data == f"edit_this_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], "✏️ What should I change?")
                        _send_message(token, expected_chat_id,
                            f"✏️ <b>What should I change in the {PLATFORM_LABELS[platform][1]} post?</b>\n\n"
                            "<i>Just type your feedback.</i>"
                        )
                        waiting_for_text = f"edit_platform:{platform}"
                        break

                # ── Regen image ───────────────────────────────────
                for platform in PLATFORM_LABELS:
                    if data == f"regen_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], "🔄 Generating new image...")
                        _typing(token, expected_chat_id)
                        _send_platform_preview(
                            token, expected_chat_id, session_id,
                            platform, current_posts, current_topic, None
                        )
                        break

                # ── Info menu: Weekly Plan ────────────────────────
                if data == f"menu_weekly_{session_id}":
                    _answer_callback(token, cb["id"], "📅 Loading weekly plan...")
                    _typing(token, expected_chat_id)
                    _menu_weekly(token, expected_chat_id)
                    continue

                # ── Info menu: Performance ────────────────────────
                if data == f"menu_performance_{session_id}":
                    _answer_callback(token, cb["id"], "📊 Loading performance data...")
                    _typing(token, expected_chat_id)
                    _menu_performance(token, expected_chat_id)
                    continue

                # ── Info menu: Competitors ────────────────────────
                if data == f"menu_competitors_{session_id}":
                    _answer_callback(token, cb["id"], "🕵️ Loading competitor intel...")
                    _menu_competitors(token, expected_chat_id)
                    continue

                # ── Info menu: Monthly Report ─────────────────────
                if data == f"menu_monthly_{session_id}":
                    _answer_callback(token, cb["id"], "📈 Loading monthly report...")
                    _typing(token, expected_chat_id)
                    _menu_monthly(token, expected_chat_id)
                    continue

                # ── Noop (divider button) ─────────────────────────
                if data == "noop":
                    _answer_callback(token, cb["id"], "")
                    continue

            # ── Text messages ─────────────────────────────────────
            if "message" in update:
                msg       = update["message"]
                from_chat = str(msg.get("chat", {}).get("id", ""))
                text      = msg.get("text", "").strip()

                if not validate_telegram_chat_id(from_chat, expected_chat_id):
                    continue

                # Campaign command — detected anytime
                if text.upper().startswith("CAMPAIGN "):
                    from src.campaign_manager import parse_campaign_command
                    parsed = parse_campaign_command(text)
                    if parsed:
                        return False, f"CAMPAIGN_CREATE:{parsed['date']}:{parsed['name']}"
                    else:
                        _send_message(token, expected_chat_id,
                            "⚠️ Invalid format. Use: <code>CAMPAIGN YYYY-MM-DD Campaign name</code>"
                        )
                    continue

                if waiting_for_text == "edit" and text:
                    logger.info(f"Edit feedback: {text[:80]}")
                    return False, f"EDIT:{text}"

                if waiting_for_text == "idea" and text:
                    logger.info(f"User idea: {text[:80]}")
                    return False, f"IDEA:{text}"

                if waiting_for_text and waiting_for_text.startswith("edit_platform:") and text:
                    platform = waiting_for_text.split(":", 1)[1]
                    logger.info(f"Edit feedback for {platform}: {text[:80]}")
                    return False, f"EDIT_PLATFORM:{platform}:{text}"

                # Legacy
                if text.upper().startswith("APPROVE"):
                    return True, ""
                if text.upper().startswith("REJECT"):
                    return False, text[6:].strip()

        remaining_mins = (timeout_seconds - elapsed) // 60
        if elapsed % 300 == 0:
            logger.step(f"Still waiting... ({remaining_mins}min remaining)")

        # ── Follow-up reminder after 1 hour ──────────────────
        if not reminder_sent and elapsed >= REMINDER_AFTER:
            reminder_sent = True
            _send_message(token, expected_chat_id,
                "Hey Fayez 👋 — just checking in.\n\n"
                "Today's post is still waiting for your review. "
                "Tap a button below whenever you're ready — I'll be here."
            )
            _send_platform_picker(token, expected_chat_id, session_id, current_posts,
                                  is_monday=datetime.now().weekday() == 0,
                                  is_month_end=datetime.now().day == 1)
            logger.info("Follow-up reminder sent.")

    _send_message(
        os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"),
        "No response in 4 hours — skipping today to keep things clean. See you tomorrow 👋"
    )
    logger.warning(f"Approval timeout after {timeout_hours}h.")
    return False, "Timeout — no response received"


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def send_notification(message: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        _send_message(token, chat_id, message)
    except Exception:
        logger.warning("Telegram notification failed silently — continuing.")


def _download_telegram_photo(token: str, file_id: str) -> str:
    result    = _api(token, "getFile", {"file_id": file_id})
    file_path = result["result"]["file_path"]
    ext       = file_path.split(".")[-1] if "." in file_path else "jpg"
    url       = f"https://api.telegram.org/file/bot{token}/{file_path}"
    resp      = requests.get(url, timeout=30)
    resp.raise_for_status()
    os.makedirs("drafts/images", exist_ok=True)
    local_path = f"drafts/images/post_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    with open(local_path, "wb") as f:
        f.write(resp.content)
    return local_path
