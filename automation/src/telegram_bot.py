import os
import re
import time
import uuid
import requests
from utils.logger import SecureLogger
from utils.validators import validate_telegram_chat_id

logger = SecureLogger("telegram_bot")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# Platform display names for messages
PLATFORM_LABELS = {
    "linkedin_company": ("🏢", "LinkedIn Company"),
    "instagram":        ("📸", "Instagram"),
    "tiktok":           ("🎵", "TikTok"),
    "threads":          ("🧵", "Threads"),
}

# Maps platform key → posts dict key
PLATFORM_POST_KEY = {
    "linkedin_company": "linkedin_company",
    "instagram":        "instagram_caption",
    "tiktok":           "tiktok_caption",
    "threads":          "threads_post",
}


def _api(token: str, method: str, payload: dict = None) -> dict:
    """Makes a secure request to the Telegram Bot API."""
    url = TELEGRAM_API.format(token=token, method=method)
    try:
        resp = requests.post(url, json=payload or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"Telegram API error on {method}: {type(e).__name__}")
        raise


def _send_message(token: str, chat_id: str, text: str):
    """Sends an HTML message, splitting at 4096-char Telegram limit."""
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
    """Sends a photo with caption to Telegram. Caption limited to 1024 chars."""
    url = TELEGRAM_API.format(token=token, method="sendPhoto")
    caption = caption[:1024]
    try:
        with open(image_path, "rb") as img:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": img},
                timeout=30
            )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Photo send failed ({type(e).__name__}) — sending text only.")
        _send_message(token, chat_id, caption)


def _answer_callback(token: str, callback_id: str, text: str):
    """Answers a callback query to dismiss the Telegram loading spinner."""
    try:
        _api(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text
        })
    except Exception:
        pass


def send_approval_request(topic: dict, posts: dict, image_path: str = None) -> str:
    """
    Sends the approval flow to Telegram:
      1. Topic header card
      2. Platform picker buttons (tap to preview each post)

    Returns session_id used to match callbacks.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")

    session_id = str(uuid.uuid4())[:8]

    image_line = f"\n<b>🖼️ IMAGE:</b> ✅ Attached — <i>{os.path.basename(image_path)}</i>" if image_path else ""

    # ── Message 1: Topic header ────────────────────────────────
    _send_message(token, chat_id,
        f"<b>🤖 FINCARE — DAILY POST REVIEW</b>\n"
        f"{'─' * 35}\n"
        f"<b>📌 TOPIC:</b> {topic.get('topic', '')}\n"
        f"<b>🎯 ANGLE:</b> {topic.get('angle', '')}\n"
        f"<b>📊 KEY STAT:</b> {topic.get('key_stat', '')}\n"
        f"<b>🏛️ PILLAR:</b> {topic.get('content_pillar', '—')}  "
        f"<b>⚡ TRIGGER:</b> {topic.get('emotional_trigger', '—')}"
        f"{image_line}\n"
        f"{'─' * 35}\n"
        f"<i>Tap a platform to preview its post and image. Or post all at once.</i>"
    )

    # ── Message 2: Platform picker ──────────────────────────────
    _send_platform_picker(token, chat_id, session_id, posts)

    logger.success(f"Approval request sent (session: {session_id})")
    return session_id


def _send_platform_picker(token: str, chat_id: str, session_id: str, posts: dict):
    """Sends the platform picker keyboard."""
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

    keyboard_rows = [
        [
            {"text": "✅ Post ALL",    "callback_data": f"approve_{session_id}"},
            {"text": "❌ Skip Today", "callback_data": f"reject_{session_id}"}
        ],
    ]
    if view_row:
        keyboard_rows.append(view_row)
    if view_row2:
        keyboard_rows.append(view_row2)
    keyboard_rows += [
        [
            {"text": "✏️ Edit All",  "callback_data": f"edit_{session_id}"},
            {"text": "💡 My Idea",   "callback_data": f"idea_{session_id}"}
        ],
    ]

    _api(token, "sendMessage", {
        "chat_id":      chat_id,
        "text":         "👆 <b>Choose a platform to preview, or post all at once:</b>",
        "parse_mode":   "HTML",
        "reply_markup": {"inline_keyboard": keyboard_rows},
    })


def _send_platform_preview(token: str, chat_id: str, session_id: str,
                            platform: str, posts: dict, topic: dict, image_path: str | None):
    """
    Generates an image (if possible) and sends the full post as photo+caption.
    Then sends per-platform action buttons below it.
    """
    from src.image_generator import generate_post_image

    emoji, label = PLATFORM_LABELS.get(platform, ("📌", platform))
    post_key     = PLATFORM_POST_KEY.get(platform, platform)
    post_text    = posts.get(post_key) or ""

    if not post_text:
        _send_message(token, chat_id, f"<i>No {label} post available.</i>")
        return

    # Try to generate image (skips silently if no API key)
    if platform == "tiktok":
        # TikTok is draft-only — no image, just show script
        _send_message(token, chat_id,
            f"{emoji} <b>{label} SCRIPT</b>\n"
            f"{'─' * 35}\n"
            f"{post_text}\n\n"
            f"<i>ℹ️ TikTok posts saved as draft — publish manually until video API is live.</i>"
        )
    else:
        # Generate or use existing image
        generated_path = image_path or generate_post_image(topic, posts, platform)
        caption = f"{emoji} <b>{label}</b>\n{'─' * 35}\n{post_text}"

        if generated_path and os.path.exists(generated_path):
            _send_photo(token, chat_id, generated_path, caption)
        else:
            _send_message(token, chat_id, caption)

    # Per-platform action buttons
    _api(token, "sendMessage", {
        "chat_id":    chat_id,
        "text":       f"What would you like to do with the {label} post?",
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [
                {"text": f"✅ Post This",      "callback_data": f"post_this_{platform}_{session_id}"},
                {"text": f"✏️ Edit This",      "callback_data": f"edit_this_{platform}_{session_id}"},
            ],
            [
                {"text": f"🔄 New Image",      "callback_data": f"regen_{platform}_{session_id}"},
                {"text": f"⏭ Back to Menu",   "callback_data": f"back_{session_id}"},
            ]
        ]}
    })


def wait_for_approval(session_id: str, timeout_hours: float = 4.0,
                       posts: dict = None, topic: dict = None,
                       image_path: str = None) -> tuple[bool, str]:
    """
    State-machine approval loop.
    States: "picker" | "viewing:{platform}"
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

            # ── Callback buttons ───────────────────────────────────
            if "callback_query" in update:
                cb        = update["callback_query"]
                from_chat = str(cb.get("from", {}).get("id", ""))
                data      = cb.get("data", "")

                if not validate_telegram_chat_id(from_chat, expected_chat_id):
                    logger.warning(f"Rejected callback from unauthorised user: {from_chat}")
                    continue

                # ── Global actions (available from picker) ────────
                if data == f"approve_{session_id}":
                    _answer_callback(token, cb["id"], "✅ Posting to all platforms...")
                    logger.success("Post APPROVED (all platforms).")
                    return True, ""

                if data == f"reject_{session_id}":
                    _answer_callback(token, cb["id"], "❌ Skipping today.")
                    logger.info("Post REJECTED by user.")
                    return False, "Rejected by user"

                if data == f"edit_{session_id}":
                    _answer_callback(token, cb["id"], "✏️ Send me your feedback.")
                    send_notification(
                        "✏️ <b>What should I change?</b>\n\n"
                        "Type your feedback and I'll rewrite all posts.\n\n"
                        "<i>Example: \"Make the tone more casual\" or \"Different hook about FOMO\"</i>"
                    )
                    waiting_for_text = "edit"
                    state = "picker"
                    continue

                if data == f"idea_{session_id}":
                    _answer_callback(token, cb["id"], "💡 Tell me your idea.")
                    send_notification(
                        "💡 <b>What's your topic idea?</b>\n\n"
                        "Type it and I'll write fresh posts around it.\n\n"
                        "<i>Example: \"Why people panic sell\" or \"Power of DCA\"</i>"
                    )
                    waiting_for_text = "idea"
                    state = "picker"
                    continue

                if data == f"back_{session_id}":
                    _answer_callback(token, cb["id"], "↩️ Back to menu.")
                    state = "picker"
                    if current_posts is not None:
                        _send_platform_picker(token, expected_chat_id, session_id, current_posts)
                    continue

                # ── View platform (from picker) ───────────────────
                for platform in PLATFORM_LABELS:
                    if data == f"view_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], "⏳ Generating preview...")
                        state = f"viewing:{platform}"
                        if current_posts is not None:
                            _send_platform_preview(
                                token, expected_chat_id, session_id,
                                platform, current_posts, current_topic or {}, current_image
                            )
                        break

                # ── Post this platform only ───────────────────────
                for platform in PLATFORM_LABELS:
                    if data == f"post_this_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], f"✅ Posting {PLATFORM_LABELS[platform][1]}...")
                        logger.success(f"Post APPROVED for {platform} only.")
                        return True, f"PLATFORMS:{platform}"

                # ── Edit this platform only ───────────────────────
                for platform in PLATFORM_LABELS:
                    if data == f"edit_this_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], "✏️ What should I change?")
                        send_notification(
                            f"✏️ <b>What should I change in the {PLATFORM_LABELS[platform][1]} post?</b>\n\n"
                            "<i>Just type your feedback.</i>"
                        )
                        waiting_for_text = f"edit_platform:{platform}"
                        break

                # ── Regenerate image ──────────────────────────────
                for platform in PLATFORM_LABELS:
                    if data == f"regen_{platform}_{session_id}":
                        _answer_callback(token, cb["id"], "🔄 Generating new image...")
                        send_notification("🔄 <b>Generating a new image...</b> Give me a moment.")
                        if current_posts is not None:
                            # Clear cached image path so a fresh one is generated
                            _send_platform_preview(
                                token, expected_chat_id, session_id,
                                platform, current_posts, current_topic or {}, None
                            )
                        break

            # ── Text / Photo messages ──────────────────────────────
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
                        logger.info(f"Campaign command: {parsed['name']} on {parsed['date']}")
                        return False, f"CAMPAIGN_CREATE:{parsed['date']}:{parsed['name']}"
                    else:
                        send_notification(
                            "⚠️ Invalid campaign format.\n\n"
                            "Use: <code>CAMPAIGN YYYY-MM-DD Campaign name</code>"
                        )
                    continue

                # Global edit feedback
                if waiting_for_text == "edit" and text:
                    logger.info(f"Edit feedback: {text[:80]}")
                    return False, f"EDIT:{text}"

                # Global idea
                if waiting_for_text == "idea" and text:
                    logger.info(f"User idea: {text[:80]}")
                    return False, f"IDEA:{text}"

                # Per-platform edit feedback
                if waiting_for_text and waiting_for_text.startswith("edit_platform:") and text:
                    platform = waiting_for_text.split(":", 1)[1]
                    logger.info(f"Edit feedback for {platform}: {text[:80]}")
                    return False, f"EDIT_PLATFORM:{platform}:{text}"

                # Legacy text commands
                if text.upper().startswith("APPROVE"):
                    return True, ""
                if text.upper().startswith("REJECT"):
                    feedback = text[6:].strip() if len(text) > 6 else ""
                    return False, feedback

        remaining_mins = (timeout_seconds - elapsed) // 60
        if elapsed % 300 == 0:
            logger.step(f"Still waiting... ({remaining_mins}min remaining)")

    logger.warning(f"Approval timeout after {timeout_hours}h.")
    return False, "Timeout — no response received"


def send_notification(message: str):
    """Sends a plain notification to Telegram."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        _send_message(token, chat_id, message)
    except Exception:
        logger.warning("Telegram notification failed silently — continuing.")


def _download_telegram_photo(token: str, file_id: str) -> str:
    """Downloads a user-uploaded photo from Telegram. Returns local file path."""
    result    = _api(token, "getFile", {"file_id": file_id})
    file_path = result["result"]["file_path"]
    ext       = file_path.split(".")[-1] if "." in file_path else "jpg"

    url  = f"https://api.telegram.org/file/bot{token}/{file_path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    os.makedirs("drafts/images", exist_ok=True)
    from datetime import datetime
    local_path = f"drafts/images/post_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    with open(local_path, "wb") as f:
        f.write(resp.content)
    return local_path
