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
from src.writer import write_posts, rewrite_posts, write_posts_from_idea
from src.telegram_bot import send_approval_request, wait_for_approval, send_notification
from src.publisher import publish_all
from src.post_scheduler import init_schedule_if_missing, get_optimal_schedule
from src.performance_tracker import record_post

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

    # ── Init skills ────────────────────────────────────
    init_schedule_if_missing()
    get_optimal_schedule()

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

    # ── Steps 3 & 4: Approval loop (supports Edit, My Idea, Image) ──
    timeout_hours = float(os.getenv("APPROVAL_TIMEOUT_HOURS", "4"))
    MAX_REWRITES = 3
    rewrites = 0
    approved = False
    image_path = None  # Set when user attaches an image

    while rewrites <= MAX_REWRITES:
        logger.info(f"STEP 3: Sending for Telegram approval (attempt {rewrites + 1})...")
        try:
            session_id = send_approval_request(topic, posts, image_path)
        except Exception as e:
            logger.error(f"Telegram send failed: {type(e).__name__}: {str(e)}")
            sys.exit(1)

        logger.info("STEP 4: Waiting for approval...")
        try:
            approved, feedback = wait_for_approval(session_id, timeout_hours)
        except Exception as e:
            logger.error(f"Approval wait failed: {type(e).__name__}: {str(e)}")
            sys.exit(1)

        # ── Approved ──────────────────────────────────
        if approved:
            break

        # ── Image attached: re-show preview with image ─
        if feedback.startswith("IMAGE:"):
            image_path = feedback[6:].strip()
            logger.info(f"Image attached: {image_path}")
            send_notification("🖼️ <b>Image attached!</b> Here's the updated preview — approve when ready.")
            continue  # Re-send approval with image shown

        # ── Edit: rewrite with feedback ───────────────
        if feedback.startswith("EDIT:"):
            user_feedback = feedback[5:].strip()
            logger.info(f"Rewriting posts with feedback: {user_feedback[:80]}")
            send_notification("✏️ <b>Rewriting posts...</b> Give me a moment.")
            try:
                posts = rewrite_posts(topic, posts, user_feedback)
                save_draft(topic, posts)
                rewrites += 1
                continue
            except Exception as e:
                logger.error(f"Rewrite failed: {type(e).__name__}: {str(e)}")
                send_notification("⚠️ <b>Rewrite failed.</b> Skipping today.")
                sys.exit(1)

        # ── My Idea: write fresh from user's topic ────
        if feedback.startswith("IDEA:"):
            user_idea = feedback[5:].strip()
            logger.info(f"Writing fresh posts from user idea: {user_idea[:80]}")
            send_notification("💡 <b>Writing posts from your idea...</b> Give me a moment.")
            try:
                topic, posts = write_posts_from_idea(user_idea)
                save_draft(topic, posts)
                rewrites += 1
                continue
            except Exception as e:
                logger.error(f"Idea write failed: {type(e).__name__}: {str(e)}")
                send_notification("⚠️ <b>Failed to write from your idea.</b> Skipping today.")
                sys.exit(1)

        # ── Rejected or timeout ───────────────────────
        logger.info(f"Posts not approved. Reason: {feedback or 'None given'}")
        send_notification(
            "📭 <b>Fincare SMM — Posts Skipped</b>\n\n"
            f"Today's posts were not approved.\n"
            f"Reason: {feedback or 'None given'}"
        )
        sys.exit(0)

    if not approved:
        send_notification("📭 <b>Fincare SMM</b> — Max rewrites reached. Skipping today.")
        sys.exit(0)

    # ── Step 5: Publish ────────────────────────────────
    logger.info("STEP 5: Publishing to all platforms...")
    try:
        results = publish_all(posts, image_path)
    except Exception as e:
        logger.error(f"Publishing failed: {type(e).__name__}: {str(e)}")
        send_notification(
            "⚠️ <b>Fincare SMM — Publishing Error</b>\n\n"
            f"Error during publishing: {type(e).__name__}\n"
            "Check logs for details."
        )
        sys.exit(1)

    # ── Record post for performance tracking ──────────
    urn = results.get("linkedin_company_urn")
    if urn and urn != "posted":
        record_post(urn, "linkedin_company", topic.get("topic", ""), topic.get("content_pillar", ""))

    # ── Step 6: Final report ───────────────────────────
    succeeded = [k for k, v in results.items() if v and k != "linkedin_company_urn"]
    failed    = [k for k, v in results.items() if not v and k != "linkedin_company_urn"]

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
