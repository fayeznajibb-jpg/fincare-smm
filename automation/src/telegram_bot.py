import os
import time
import uuid
import requests
from utils.logger import SecureLogger
from utils.validators import validate_telegram_chat_id

logger = SecureLogger("telegram_bot")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


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


def send_approval_request(topic: dict, posts: dict, image_path: str = None) -> str:
    """
    Sends a formatted post preview to Telegram with action buttons.
    Returns a unique session_id used to match the callback response.
    Security: each session has a unique ID to prevent replay attacks.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")

    session_id = str(uuid.uuid4())[:8]  # Short unique ID for this run

    message = _build_preview_message(topic, posts, image_path)

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve — Post All", "callback_data": f"approve_{session_id}"},
                {"text": "❌ Reject — Skip Today", "callback_data": f"reject_{session_id}"}
            ],
            [
                {"text": "✏️ Edit — Give Feedback", "callback_data": f"edit_{session_id}"},
                {"text": "💡 My Idea — I'll Set the Topic", "callback_data": f"idea_{session_id}"}
            ],
            [
                {"text": "🖼️ Add Image", "callback_data": f"image_{session_id}"}
            ]
        ]
    }

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "reply_markup": keyboard
    }

    result = _api(token, "sendMessage", payload)

    if not result.get("ok"):
        raise RuntimeError(f"Failed to send Telegram message: {result}")

    logger.success(f"Approval request sent to Telegram (session: {session_id})")
    return session_id


def wait_for_approval(session_id: str, timeout_hours: float = 4.0) -> tuple[bool, str]:
    """
    Polls Telegram for the user's approval or rejection response.
    Security: only accepts callbacks matching the current session_id
    and from the authorised chat ID.

    Returns (approved: bool, feedback: str)
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    expected_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not expected_chat_id:
        raise EnvironmentError("TELEGRAM credentials not set.")

    timeout_seconds = int(timeout_hours * 3600)
    poll_interval = 30  # seconds between polls
    elapsed = 0
    last_update_id = 0
    waiting_for_text = None  # "edit" or "idea" after user taps those buttons

    logger.step(f"Waiting for approval (timeout: {timeout_hours}h, session: {session_id})...")

    while elapsed < timeout_seconds:
        time.sleep(poll_interval)
        elapsed += poll_interval

        try:
            result = _api(token, "getUpdates", {
                "offset": last_update_id + 1,
                "timeout": 25,
                "allowed_updates": ["callback_query", "message"]
            })
        except Exception as e:
            logger.warning(f"Poll error (will retry): {type(e).__name__}")
            continue

        if not result.get("ok") or not result.get("result"):
            continue

        for update in result["result"]:
            last_update_id = update["update_id"]

            # Handle inline keyboard callback
            if "callback_query" in update:
                callback = update["callback_query"]
                from_chat = str(callback.get("from", {}).get("id", ""))
                data = callback.get("data", "")

                # Security check — only accept from authorised chat ID
                if not validate_telegram_chat_id(from_chat, expected_chat_id):
                    logger.warning(f"Rejected callback from unauthorised user: {from_chat}")
                    continue

                # Security check — only accept callbacks matching this session
                if data == f"approve_{session_id}":
                    _answer_callback(token, callback["id"], "✅ Approved! Posting now...")
                    logger.success("Post APPROVED by user.")
                    return True, ""

                elif data == f"reject_{session_id}":
                    _answer_callback(token, callback["id"], "❌ Rejected. Skipping today.")
                    logger.info("Post REJECTED by user.")
                    return False, "Rejected by user"

                elif data == f"edit_{session_id}":
                    _answer_callback(token, callback["id"], "✏️ Got it! Send me your feedback.")
                    send_notification("✏️ <b>What should I change?</b>\n\nJust type your feedback and I'll rewrite the posts.\n\n<i>Example: \"Make the tone more casual\" or \"Use a different hook about FOMO\"</i>")
                    waiting_for_text = "edit"
                    logger.info("User requested EDIT — waiting for feedback text...")

                elif data == f"idea_{session_id}":
                    _answer_callback(token, callback["id"], "💡 Love it! Tell me your idea.")
                    send_notification("💡 <b>What's your idea or topic?</b>\n\nJust type it and I'll write fresh posts around it.\n\n<i>Example: \"Why young people are scared to start investing\" or \"The power of compound interest\"</i>")
                    waiting_for_text = "idea"
                    logger.info("User requested own IDEA — waiting for topic text...")

                elif data == f"image_{session_id}":
                    _answer_callback(token, callback["id"], "🖼️ Send me the image!")
                    send_notification("🖼️ <b>Send me the image</b> you want to attach to this post.\n\nJust send it as a photo in this chat.")
                    waiting_for_text = "image"
                    logger.info("User requested image — waiting for photo...")

            # Handle text and photo replies
            if "message" in update:
                msg = update["message"]
                from_chat = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()

                if not validate_telegram_chat_id(from_chat, expected_chat_id):
                    continue

                # Handle photo upload when waiting for image
                if waiting_for_text == "image" and "photo" in msg:
                    photo = msg["photo"][-1]  # Largest available size
                    file_id = photo["file_id"]
                    try:
                        image_path = _download_telegram_photo(token, file_id)
                        logger.success(f"Image downloaded: {image_path}")
                        return False, f"IMAGE: {image_path}"
                    except Exception as e:
                        logger.error(f"Image download failed: {type(e).__name__}")
                        send_notification("⚠️ Couldn't download image. Please try again or skip.")
                    continue

                # Campaign creation — detected anytime, not just when waiting
                if text.upper().startswith("CAMPAIGN "):
                    from src.campaign_manager import parse_campaign_command, create_campaign
                    parsed = parse_campaign_command(text)
                    if parsed:
                        logger.info(f"Campaign command received: {parsed['name']} on {parsed['date']}")
                        return False, f"CAMPAIGN_CREATE:{parsed['date']}:{parsed['name']}"
                    else:
                        send_notification(
                            "⚠️ Invalid campaign format.\n\n"
                            "Use: <code>CAMPAIGN YYYY-MM-DD Campaign name</code>\n"
                            "Example: <code>CAMPAIGN 2026-05-01 Fincare hits 1000 users</code>"
                        )

                # If we're waiting for edit feedback or idea text
                if waiting_for_text == "edit" and text:
                    logger.info(f"Edit feedback received: {text[:80]}")
                    return False, f"EDIT: {text}"

                if waiting_for_text == "idea" and text:
                    logger.info(f"User idea received: {text[:80]}")
                    return False, f"IDEA: {text}"

                # Legacy text commands still supported
                if text.upper().startswith("APPROVE"):
                    logger.success("Post APPROVED via text reply.")
                    return True, ""

                if text.upper().startswith("REJECT"):
                    feedback = text[6:].strip() if len(text) > 6 else ""
                    logger.info(f"Post REJECTED via text reply. Feedback: {feedback}")
                    return False, feedback

        remaining_mins = (timeout_seconds - elapsed) // 60
        if elapsed % 300 == 0:  # Log every 5 minutes
            logger.step(f"Still waiting for approval... ({remaining_mins}min remaining)")

    logger.warning(f"Approval timeout reached after {timeout_hours}h. Skipping today's post.")
    return False, "Timeout — no response received"


def send_notification(message: str):
    """Sends a plain notification message to Telegram (no approval needed)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return

    try:
        _api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        })
    except Exception:
        # Retry without HTML parse mode in case of formatting error
        try:
            _api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
            })
        except Exception:
            logger.warning("Telegram notification failed silently — continuing.")


def _download_telegram_photo(token: str, file_id: str) -> str:
    """Downloads a photo from Telegram and saves it to a temp file. Returns the file path."""
    import tempfile

    result = _api(token, "getFile", {"file_id": file_id})
    file_path = result["result"]["file_path"]
    ext = file_path.split(".")[-1] if "." in file_path else "jpg"

    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    os.makedirs("drafts/images", exist_ok=True)
    from datetime import datetime
    local_path = f"drafts/images/post_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    with open(local_path, "wb") as f:
        f.write(resp.content)

    return local_path


def _answer_callback(token: str, callback_id: str, text: str):
    """Sends a response to a Telegram callback query (dismisses the loading state)."""
    try:
        _api(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text
        })
    except Exception:
        pass  # Non-critical


def _build_preview_message(topic: dict, posts: dict, image_path: str = None) -> str:
    """Builds a formatted Telegram preview of all posts for approval."""

    linkedin_p = posts.get("linkedin_personal", "")
    linkedin_c = posts.get("linkedin_company", "")
    instagram  = posts.get("instagram_caption", "")
    tiktok     = posts.get("tiktok_caption", "")
    threads    = posts.get("threads_post", "")

    def preview(text: str, limit: int = 300) -> str:
        return text[:limit] + "..." if len(text) > limit else text

    image_line = f"\n<b>🖼️ IMAGE:</b> ✅ Attached ({os.path.basename(image_path)})\n" if image_path else ""

    msg = (
        f"<b>🤖 FINCARE — DAILY POST APPROVAL</b>\n"
        f"{'─' * 35}\n\n"
        f"<b>📌 TOPIC:</b> {topic.get('topic', '')}\n"
        f"<b>🎯 ANGLE:</b> {topic.get('angle', '')}\n"
        f"<b>📊 KEY STAT:</b> {topic.get('key_stat', '')}\n"
        f"<b>🏛️ PILLAR:</b> {topic.get('content_pillar', '—')} | <b>⚡ TRIGGER:</b> {topic.get('emotional_trigger', '—')}\n"
        f"{image_line}\n"
        f"{'─' * 35}\n\n"

        f"<b>💼 LINKEDIN (Personal — {len(linkedin_p)} chars):</b>\n"
        f"{preview(linkedin_p)}\n\n"

        f"<b>🏢 LINKEDIN (Company — {len(linkedin_c)} chars):</b>\n"
        f"{preview(linkedin_c, 200)}\n\n"

        f"<b>📸 INSTAGRAM ({len(instagram)} chars):</b>\n"
        f"{preview(instagram, 200)}\n\n"

        f"<b>🎵 TIKTOK ({len(tiktok)} chars):</b>\n"
        f"{preview(tiktok, 150)}\n\n"

        f"<b>🧵 THREADS ({len(threads)} chars):</b>\n"
        f"{preview(threads, 200)}\n\n"

        f"{'─' * 35}\n"
        f"<i>Tap a button below.</i>"
    )

    return msg
