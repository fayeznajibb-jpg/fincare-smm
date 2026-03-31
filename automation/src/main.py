"""
Fincare SMM Automation — Main Orchestrator
==========================================
Pipeline: Research → Write → Telegram Approval → Publish
"""

import os
import sys
import json
from datetime import datetime

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from utils.logger import SecureLogger
from utils.validators import validate_env_vars
from src.researcher import research_topic
from src.writer import write_posts
from src.telegram_bot import send_approval_request, wait_for_approval, send_notification
from src.publisher import publish_all

logger = SecureLogger("main")

REQUIRED_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def save_draft(topic: dict, posts: dict):
    """Saves current run's content to drafts folder as a backup."""
    os.makedirs("drafts", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = f"drafts/posts_{timestamp}.json"

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({"topic": topic, "posts": posts}, f, indent=2, ensure_ascii=False)

    logger.info(f"Draft saved: {filepath}")


def run():
    """
    Main automation pipeline.
    Runs once per day via GitHub Actions.
    """
    logger.info("=" * 50)
    logger.info("FINCARE SMM AUTOMATION — STARTING RUN")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 50)

    # ── Step 0: Validate environment ──────────────────
    is_valid, missing = validate_env_vars(REQUIRED_ENV_VARS)
    if not is_valid:
        logger.error(f"Missing required environment variables: {missing}")
        send_notification(
            "⚠️ <b>Fincare SMM Error</b>\n\n"
            f"Missing environment variables: {', '.join(missing)}\n"
            "Please check GitHub Secrets."
        )
        sys.exit(1)

    # ── Step 1: Research trending topic ───────────────
    logger.info("STEP 1: Researching topic...")
    try:
        topic = research_topic()
    except Exception as e:
        logger.error(f"Research failed: {type(e).__name__}: {str(e)}")
        send_notification(
            "⚠️ <b>Fincare SMM — Research Failed</b>\n\n"
            f"Error: {type(e).__name__}\nCheck logs for details."
        )
        sys.exit(1)

    # ── Step 2: Write posts ────────────────────────────
    logger.info("STEP 2: Writing posts...")
    try:
        posts = write_posts(topic)
    except Exception as e:
        logger.error(f"Writing failed: {type(e).__name__}: {str(e)}")
        send_notification(
            "⚠️ <b>Fincare SMM — Writing Failed</b>\n\n"
            f"Error: {type(e).__name__}\nCheck logs for details."
        )
        sys.exit(1)

    # Save draft as backup
    save_draft(topic, posts)

    # ── Step 3: Send for approval ──────────────────────
    logger.info("STEP 3: Sending for Telegram approval...")
    try:
        timeout_hours = float(os.getenv("APPROVAL_TIMEOUT_HOURS", "4"))
        session_id = send_approval_request(topic, posts)
    except Exception as e:
        logger.error(f"Telegram send failed: {type(e).__name__}: {str(e)}")
        sys.exit(1)

    # ── Step 4: Wait for approval ──────────────────────
    logger.info("STEP 4: Waiting for approval...")
    try:
        approved, feedback = wait_for_approval(session_id, timeout_hours)
    except Exception as e:
        logger.error(f"Approval wait failed: {type(e).__name__}: {str(e)}")
        sys.exit(1)

    if not approved:
        logger.info(f"Posts not approved. Reason: {feedback or 'None given'}")
        send_notification(
            "📭 <b>Fincare SMM — Posts Skipped</b>\n\n"
            f"Today's posts were not approved.\n"
            f"Reason: {feedback or 'None given'}"
        )
        sys.exit(0)

    # ── Step 5: Publish ────────────────────────────────
    logger.info("STEP 5: Publishing to all platforms...")
    try:
        results = publish_all(posts)
    except Exception as e:
        logger.error(f"Publishing failed: {type(e).__name__}: {str(e)}")
        send_notification(
            "⚠️ <b>Fincare SMM — Publishing Error</b>\n\n"
            f"Error during publishing: {type(e).__name__}\n"
            "Check logs for details."
        )
        sys.exit(1)

    # ── Step 6: Final report ───────────────────────────
    succeeded = [k for k, v in results.items() if v]
    failed    = [k for k, v in results.items() if not v]

    report = (
        "📊 <b>Fincare SMM — Daily Report</b>\n\n"
        f"📌 Topic: {topic['topic'][:80]}...\n\n"
        f"✅ Posted to: {', '.join(succeeded) if succeeded else 'none'}\n"
    )
    if failed:
        report += f"⚠️ Skipped: {', '.join(failed)}\n"
    report += "\n<i>TikTok draft saved — post manually.</i>"

    send_notification(report)
    logger.success("Run complete.")


if __name__ == "__main__":
    run()
