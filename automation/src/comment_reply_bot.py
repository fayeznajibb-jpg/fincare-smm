"""
Fincare Comment Reply Bot
==========================
Polls LinkedIn company page for new comments on recent posts.
Drafts replies using Claude Haiku. Sends via Telegram for approve → post.
Runs 3x daily via GitHub Actions.
"""

import os
import json
import time
import requests
import anthropic
from datetime import datetime, timedelta
from utils.logger import SecureLogger

logger = SecureLogger("comment_reply_bot")

LINKEDIN_API     = "https://api.linkedin.com/v2"
LINKEDIN_REST    = "https://api.linkedin.com/rest"
SEEN_LOG_PATH    = "logs/seen_comments.json"
PENDING_LOG_PATH = "logs/pending_replies.json"
LINKEDIN_VERSION = "202304"


# ─────────────────────────────────────────
# COMMENT FETCHING
# ─────────────────────────────────────────

def _linkedin_headers() -> dict:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    return {
        "Authorization":             f"Bearer {token}",
        "LinkedIn-Version":          LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }


def get_recent_post_urns() -> list[str]:
    """Loads URNs from the performance log for posts from the last 7 days."""
    try:
        with open("logs/performance_log.json") as f:
            log = json.load(f)
    except Exception:
        return []

    cutoff = datetime.now() - timedelta(days=7)
    return [
        e["urn"] for e in log
        if e.get("urn") and datetime.fromisoformat(e["posted_at"]) >= cutoff
    ]


def fetch_comments_for_post(post_urn: str) -> list[dict]:
    """Fetches comments for a single LinkedIn post."""
    org_id = os.getenv("LINKEDIN_ORGANIZATION_ID")
    if not org_id:
        return []

    try:
        encoded_urn = post_urn.replace(":", "%3A").replace(",", "%2C")
        resp = requests.get(
            f"{LINKEDIN_API}/socialActions/{encoded_urn}/comments",
            headers=_linkedin_headers(),
            params={"count": 20},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"Comments fetch failed for {post_urn[:40]}: {resp.status_code}")
            return []

        data = resp.json()
        comments = []
        for item in data.get("elements", []):
            author = item.get("actor", "")
            text   = item.get("message", {}).get("text", "").strip()
            cid    = item.get("$URN", item.get("id", ""))
            if text and cid:
                comments.append({"id": cid, "author": author, "text": text, "post_urn": post_urn})
        return comments

    except Exception as e:
        logger.warning(f"Comment fetch error: {type(e).__name__}")
        return []


# ─────────────────────────────────────────
# SEEN TRACKING
# ─────────────────────────────────────────

def load_seen_ids() -> set:
    try:
        with open(SEEN_LOG_PATH) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen_ids(seen: set):
    os.makedirs("logs", exist_ok=True)
    with open(SEEN_LOG_PATH, "w") as f:
        json.dump(list(seen), f)


def load_pending_replies() -> list:
    try:
        with open(PENDING_LOG_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def save_pending_replies(pending: list):
    os.makedirs("logs", exist_ok=True)
    with open(PENDING_LOG_PATH, "w") as f:
        json.dump(pending, f, indent=2)


# ─────────────────────────────────────────
# REPLY DRAFTING
# ─────────────────────────────────────────

def draft_reply(comment_text: str) -> str:
    """Uses Claude Haiku to draft a reply. Falls back to template on error."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _template_reply(comment_text)

    prompt = f"""You are the Fincare social media manager replying to a LinkedIn comment.
Fincare is an AI investing co-pilot that helps investors master their emotions.

Comment: "{comment_text}"

Write a reply that:
- Is warm, genuine, and conversational (never corporate)
- Under 50 words
- Acknowledges what they said specifically
- Adds one brief insight or follow-up thought if relevant
- Ends with a question OR a light invite to try Fincare (don't do both)
- NEVER uses: "delve", "crucial", "leverage", "comprehensive", "absolutely", "certainly"
- Sounds like a real person, not a bot

Return only the reply text — no quotes, no labels."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Reply draft failed: {type(e).__name__} — using template.")
        return _template_reply(comment_text)


def _template_reply(comment_text: str) -> str:
    """Zero-cost fallback reply templates."""
    text_lower = comment_text.lower()
    if any(w in text_lower for w in ["agree", "exactly", "true", "yes", "right"]):
        return "Glad this resonated! Most investors go through this — the key is just knowing the pattern before it plays out. What's been your biggest emotional investing moment?"
    if any(w in text_lower for w in ["how", "what", "where", "when", "tell me"]):
        return "Great question! It really comes down to building awareness of your own patterns first. Happy to share more — what's your current investing setup?"
    if any(w in text_lower for w in ["thanks", "thank you", "helpful", "great post"]):
        return "Really appreciate that! We post this kind of content weekly — the more investors understand their own psychology, the better decisions they make."
    return "Love hearing this perspective. What's your take on how emotions affect your own investing decisions?"


# ─────────────────────────────────────────
# POSTING REPLIES
# ─────────────────────────────────────────

def post_reply_to_linkedin(comment_id: str, reply_text: str) -> bool:
    """Posts an approved reply to a LinkedIn comment."""
    org_id = os.getenv("LINKEDIN_ORGANIZATION_ID")
    if not org_id:
        return False

    try:
        encoded_id = comment_id.replace(":", "%3A").replace(",", "%2C")
        payload = {
            "actor":   f"urn:li:organization:{org_id}",
            "message": {"text": reply_text}
        }
        resp = requests.post(
            f"{LINKEDIN_API}/socialActions/{encoded_id}/comments",
            headers={**_linkedin_headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.success(f"Reply posted to comment {comment_id[:40]}")
            return True
        else:
            logger.error(f"Reply post failed: {resp.status_code}")
            return False
    except Exception as e:
        logger.error(f"Reply post error: {type(e).__name__}")
        return False


# ─────────────────────────────────────────
# TELEGRAM APPROVAL FLOW
# ─────────────────────────────────────────

def send_reply_for_approval(comment: dict, draft: str, session_id: str):
    """Sends a comment + draft reply to Telegram with Post/Skip buttons."""
    from src.telegram_bot import _api
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    msg = (
        f"<b>💬 NEW LINKEDIN COMMENT</b>\n"
        f"{'─'*30}\n"
        f"<b>Comment:</b>\n<i>{comment['text'][:300]}</i>\n\n"
        f"<b>Draft reply:</b>\n{draft}\n\n"
        f"{'─'*30}\n"
        f"<i>Tap Post to reply or Skip to ignore.</i>"
    )

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Post Reply",    "callback_data": f"reply_post_{session_id}"},
            {"text": "✏️ Edit Reply",   "callback_data": f"reply_edit_{session_id}"},
            {"text": "⏭️ Skip",         "callback_data": f"reply_skip_{session_id}"},
        ]]
    }

    try:
        _api(token, "sendMessage", {
            "chat_id":      chat_id,
            "text":         msg,
            "parse_mode":   "HTML",
            "reply_markup": keyboard,
        })
    except Exception as e:
        logger.warning(f"Failed to send reply approval: {type(e).__name__}")


def wait_for_reply_decision(session_id: str, timeout_minutes: int = 30) -> tuple[str, str]:
    """
    Polls Telegram for post/edit/skip decision on a reply.
    Returns (action, edited_text) where action is 'post', 'edit', or 'skip'.
    """
    from src.telegram_bot import _api
    from utils.validators import validate_telegram_chat_id

    token             = os.getenv("TELEGRAM_BOT_TOKEN")
    expected_chat_id  = os.getenv("TELEGRAM_CHAT_ID")
    timeout_seconds   = timeout_minutes * 60
    elapsed           = 0
    poll_interval     = 20
    last_update_id    = 0
    waiting_for_edit  = False

    while elapsed < timeout_seconds:
        time.sleep(poll_interval)
        elapsed += poll_interval

        try:
            result = _api(token, "getUpdates", {
                "offset":          last_update_id + 1,
                "timeout":         15,
                "allowed_updates": ["callback_query", "message"],
            })
        except Exception:
            continue

        for update in result.get("result", []):
            last_update_id = update["update_id"]

            if "callback_query" in update:
                cb      = update["callback_query"]
                from_id = str(cb.get("from", {}).get("id", ""))
                data    = cb.get("data", "")
                if not validate_telegram_chat_id(from_id, expected_chat_id):
                    continue

                if data == f"reply_post_{session_id}":
                    return "post", ""
                elif data == f"reply_skip_{session_id}":
                    return "skip", ""
                elif data == f"reply_edit_{session_id}":
                    from src.telegram_bot import send_notification
                    send_notification("✏️ Type your edited reply and send it here.")
                    waiting_for_edit = True

            if "message" in update and waiting_for_edit:
                msg     = update["message"]
                from_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "").strip()
                if validate_telegram_chat_id(from_id, expected_chat_id) and text:
                    return "edit", text

    return "skip", ""


# ─────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────

def run_comment_reply_bot():
    """
    Main function called by GitHub Actions 3x daily.
    Finds new comments → drafts replies → sends to Telegram for approval → posts.
    """
    logger.step("Starting comment reply bot...")

    post_urns = get_recent_post_urns()
    if not post_urns:
        logger.info("No recent post URNs found — skipping.")
        return

    seen_ids = load_seen_ids()
    new_count = 0

    for post_urn in post_urns:
        comments = fetch_comments_for_post(post_urn)

        for comment in comments:
            cid = comment["id"]
            if cid in seen_ids:
                continue

            seen_ids.add(cid)
            new_count += 1

            # Draft reply
            draft = draft_reply(comment["text"])

            # Send to Telegram for approval
            import uuid
            session_id = str(uuid.uuid4())[:8]
            send_reply_for_approval(comment, draft, session_id)

            # Wait for decision (30 min window per comment)
            action, edited = wait_for_reply_decision(session_id, timeout_minutes=30)

            if action == "post":
                post_reply_to_linkedin(cid, draft)
            elif action == "edit" and edited:
                post_reply_to_linkedin(cid, edited)
            else:
                logger.info(f"Comment skipped: {cid[:40]}")

    save_seen_ids(seen_ids)
    logger.success(f"Comment bot run complete. New comments processed: {new_count}")
