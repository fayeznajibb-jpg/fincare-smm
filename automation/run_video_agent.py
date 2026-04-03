"""
Fincare Video Agent — Standalone CLI Runner
============================================
Generate a Fincare video for any topic and send it to Telegram for review.

Usage:
  cd "/Users/fayeznajib/Downloads/Fincare SMM /automation"

  # Generate video for a custom topic:
  python run_video_agent.py --topic "Why investors panic sell at the bottom"

  # Specify pillar and trigger:
  python run_video_agent.py --topic "The FOMO trap" --pillar STORY --trigger FOMO

  # Render all 3 formats (9:16, 1:1, 16:9):
  python run_video_agent.py --topic "Loss aversion explained" --formats 916 11 169

  # Props only — no render (just generate + print VideoProps):
  python run_video_agent.py --topic "FOMO panic" --props-only
"""

import os
import sys
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from utils.logger import SecureLogger
from src.video_agent import (
    skill_select_template,
    skill_generate_props,
    skill_render,
    skill_extract_thumbnail,
    skill_send_preview,
    skill_copy_script,
    run as run_agent,
)
from src.viral_spy import load_latest_viral_intel

logger = SecureLogger("run_video_agent")


def main():
    parser = argparse.ArgumentParser(description="Fincare Video Agent CLI")
    parser.add_argument("--topic",      required=True,  help="Topic title or description")
    parser.add_argument("--angle",      default="",     help="Unique behavioural angle")
    parser.add_argument("--stat",       default="",     help="Key stat (e.g. '67%%' or '$1.7T')")
    parser.add_argument("--hook",       default="",     help="Opening hook line")
    parser.add_argument("--pillar",     default="STORY",
                        choices=["STORY", "DATA", "OPINION", "QUESTION", "INSIGHT"],
                        help="Content pillar")
    parser.add_argument("--trigger",    default="",
                        help="Emotional trigger: anxiety|fear|FOMO|overconfidence|shame")
    parser.add_argument("--formats",    nargs="+", default=["916", "11"],
                        help="Formats to render: 916 11 169")
    parser.add_argument("--props-only", action="store_true",
                        help="Generate props only — no render")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram preview send")
    parser.add_argument("--variants",   type=int, default=3,
                        help="Number of hook variants to generate (default 3)")
    args = parser.parse_args()

    logger.step(f"Video agent starting for topic: '{args.topic}'")

    # ── Build topic dict ────────────────────────────────────────────────────
    topic = {
        "topic":           args.topic,
        "angle":           args.angle or f"Behavioural finance angle on: {args.topic}",
        "key_stat":        args.stat,
        "hook":            args.hook,
        "content_pillar":  args.pillar,
        "emotional_trigger": args.trigger or {
            "STORY":    "anxiety",
            "DATA":     "fear",
            "OPINION":  "overconfidence",
            "QUESTION": "FOMO",
            "INSIGHT":  "anxiety",
        }.get(args.pillar, "anxiety"),
    }

    # ── Load viral intel ────────────────────────────────────────────────────
    viral_intel = load_latest_viral_intel()
    if viral_intel:
        logger.info(f"Viral intel loaded: top_pattern={viral_intel.get('top_pattern')}")
    else:
        logger.info("No viral intel found — generating props without it.")

    # ── Step 1: Template ────────────────────────────────────────────────────
    template = skill_select_template(topic)
    print(f"\n📐 Template: {template}")

    # ── Step 2: Props variants ──────────────────────────────────────────────
    print(f"\n🧠 Generating {args.variants} hook variants...")
    variants = skill_generate_props(topic, viral_intel, n=args.variants)
    best     = variants[0]

    print(f"\n✅ Best hook: {best['hook']}")
    print(f"   Stat: {best['stat']}")
    print(f"   Insight: {best['insight']}")
    print(f"   CTA: {best['ctaText']}")
    print(f"   Trigger: {best['trigger']}\n")

    if len(variants) > 1:
        print("Other variants:")
        for i, v in enumerate(variants[1:], 2):
            print(f"  {i}. {v['hook']}")

    # Save props
    os.makedirs("drafts", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    props_path = f"drafts/video_props_{ts}.json"
    with open(props_path, "w") as f:
        json.dump({"best": best, "variants": variants, "topic": topic}, f, indent=2)
    print(f"\n💾 Props saved: {props_path}")

    if args.props_only:
        print("\n✅ Props-only mode — done. No render.")
        return

    # ── Step 3: Render ──────────────────────────────────────────────────────
    print(f"\n🎬 Rendering formats: {args.formats}...")
    print("   (This takes 3–8 minutes per format)\n")
    render_paths = skill_render(best, formats=args.formats, timestamp=ts)

    for fmt, path in render_paths.items():
        if path:
            size = os.path.getsize(path) / (1024 * 1024)
            print(f"   ✅ {fmt}: {os.path.basename(path)} ({size:.1f}MB)")
        else:
            print(f"   ❌ {fmt}: render failed or skipped")

    # ── Step 4: Thumbnail ───────────────────────────────────────────────────
    thumbnail = None
    if render_paths.get("916"):
        print("\n🖼️  Extracting thumbnail...")
        thumbnail = skill_extract_thumbnail(render_paths["916"], frame_sec=3.0)
        if thumbnail:
            print(f"   ✅ {os.path.basename(thumbnail)}")

    render_paths["thumbnail"] = thumbnail

    # ── Step 5: Send to Telegram ────────────────────────────────────────────
    if args.no_telegram:
        print("\n📵 Telegram skipped (--no-telegram flag).")
        return

    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("\n⚠️  TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping Telegram send.")
        return

    session_id = f"cli_{ts}"
    print(f"\n📱 Sending preview to Telegram (session: {session_id})...")
    skill_send_preview(render_paths, best, topic, token, chat_id, session_id)

    # Also send copy script
    posts = {}  # No posts dict in CLI mode — just props
    script = skill_copy_script(best, posts)
    import requests
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": script, "parse_mode": "HTML"},
            timeout=30,
        )
    except Exception:
        pass

    print("\n✅ Done! Check Telegram for the video preview.")
    print(f"   Session ID: {session_id} (use this for callbacks)\n")


if __name__ == "__main__":
    main()
