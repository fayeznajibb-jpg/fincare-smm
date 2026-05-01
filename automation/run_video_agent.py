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
    skill_select_format,
    skill_generate_props,
    skill_generate_scene_images,
    skill_generate_carousel,
    skill_render,
    skill_extract_thumbnail,
    skill_send_preview,
    skill_copy_script,
    skill_generate_voiceover_script,
    skill_synthesise_voice_elevenlabs,
    skill_synthesise_voice_edge,
    skill_mix_audio,
    run as run_agent,
)
from src.viral_spy import load_latest_viral_intel

logger = SecureLogger("run_video_agent")


def _generate_cli_captions(props: dict, topic: dict, ts: str) -> dict:
    """
    Generates TikTok + Instagram captions using Claude Haiku so the manual
    posting guide in Telegram includes ready-to-paste copy.
    Returns a posts dict compatible with skill_copy_script.
    """
    import anthropic as _ant
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {}

    hook    = props.get("hook", "")
    insight = props.get("insight", "")
    stat    = props.get("stat", "")
    cta     = props.get("ctaText", "Save this.")
    pillar  = topic.get("content_pillar", "FUNDAMENTALS")

    prompt = f"""Write social media captions for a FINVYON finance video.

Topic: {topic.get('topic', '')}
Hook: {hook}
Key stat: {stat}
Insight: {insight}
CTA: {cta}
Pillar: {pillar}

Write TWO captions:

1. TIKTOK CAPTION (max 150 chars): punchy, curiosity-driven, ends with a hook question or statement. No hashtags here.
2. INSTAGRAM CAPTION (3–5 lines): hook line, 2-3 insight lines, CTA line. Conversational. No hashtags here.
3. HASHTAGS TIKTOK (8–10 tags): mix of broad (#investing #finance) and niche (#personalfinance #stockmarket)
4. HASHTAGS INSTAGRAM (15–20 tags): broader mix including Arabic finance tags like #استثمار #تمويل

Return ONLY raw JSON, no markdown:
{{"tiktok_caption": "...", "instagram_caption": "...", "hashtags_tiktok": "...", "hashtags_instagram": "..."}}"""

    try:
        client = _ant.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)
    except Exception as e:
        print(f"   ⚠️  Caption generation failed: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser(description="Fincare Video Agent CLI")
    parser.add_argument("--topic",      required=True,  help="Topic title or description")
    parser.add_argument("--angle",      default="",     help="Unique behavioural angle")
    parser.add_argument("--stat",       default="",     help="Key stat (e.g. '67%%' or '$1.7T')")
    parser.add_argument("--hook",       default="",     help="Opening hook line")
    parser.add_argument("--pillar",     default="FUNDAMENTALS",
                        choices=["FUNDAMENTALS", "NEWS", "COMPANY", "PORTFOLIO", "MINDSET",
                                 "STORY", "DATA", "OPINION", "QUESTION", "INSIGHT"],
                        help="Content pillar")
    parser.add_argument("--trigger",    default="",
                        help="Emotional trigger: anxiety|fear|FOMO|overconfidence|shame")
    parser.add_argument("--formats",    nargs="+", default=None,
                        help="Formats to render: 916 11 daily_916 news_916 (default: auto from --video-format)")
    parser.add_argument("--video-format", default=None,
                        choices=["A", "B", "D", "E"],
                        help="Video format: A=DailyBrief B=AIReveal D=Explainer E=NewsReaction (default: auto from pillar)")
    parser.add_argument("--breaking",   action="store_true",
                        help="Flag topic as breaking news → forces Format E (News Reaction)")
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
        "is_breaking":     args.breaking,
        "emotional_trigger": args.trigger or {
            "FUNDAMENTALS": "curiosity",
            "NEWS":         "anxiety",
            "COMPANY":      "curiosity",
            "PORTFOLIO":    "anxiety",
            "MINDSET":      "fear",
            "STORY":        "anxiety",
            "DATA":         "fear",
            "OPINION":      "overconfidence",
            "QUESTION":     "FOMO",
            "INSIGHT":      "anxiety",
        }.get(args.pillar, "anxiety"),
    }

    # ── Load viral intel ────────────────────────────────────────────────────
    viral_intel = load_latest_viral_intel()
    if viral_intel:
        logger.info(f"Viral intel loaded: top_pattern={viral_intel.get('top_pattern')}")
    else:
        logger.info("No viral intel found — generating props without it.")

    # ── Step 1: Format + Template ───────────────────────────────────────────
    video_format = args.video_format or skill_select_format(topic)
    topic["suggested_video_format"] = video_format
    template = skill_select_template(topic)
    print(f"\n📐 Format: {video_format} | Template: {template}")

    # ── Step 2: Props variants ──────────────────────────────────────────────
    print(f"\n🧠 Generating {args.variants} hook variants...")
    variants = skill_generate_props(topic, viral_intel, n=args.variants, video_format=video_format)
    best     = variants[0]

    print(f"\n✅ Best hook: {best['hook']}")
    print(f"   Stat: {best.get('stat', '')}")
    print(f"   Insight: {best.get('insight', '')}")
    if best.get("bullets"):
        print(f"   Bullets: {best['bullets']}")
    if best.get("aiSignal"):
        print(f"   AI Signal: {best['aiSignal']}")
    if best.get("headline"):
        print(f"   Headline: {best['headline']}")
    if best.get("actionStep"):
        print(f"   Action Step: {best['actionStep']}")
    print(f"   CTA: {best.get('ctaText', '')}")
    print(f"   Trigger: {best.get('trigger', '')}\n")

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

    # ── Step 3: Generate AI scene images ────────────────────────────────────
    print(f"\n🎨 Generating AI scene images (10 scenes)...")
    print("   (This takes ~2–5 minutes)\n")
    scene_images = skill_generate_scene_images(topic, {}, best, ts, video_format=video_format)
    if scene_images:
        print(f"   ✅ {sum(1 for k in scene_images if k.startswith('img'))} scene images generated")
    else:
        print("   ⚠️  Image generation failed — video will use placeholder backgrounds")

    # ── Step 4: Render ───────────────────────────────────────────────────────
    print(f"\n🎬 Rendering (format={video_format})...")
    print("   (This takes 2–4 minutes)\n")

    finvyon_scenes = []  # populated below for Format D; empty for all other formats
    if video_format == "D" and scene_images:
        # Build 10-scene FINVYON props
        for i in range(1, 11):
            scene = {
                "img":       scene_images.get(f"img{i}", "ai_bg/placeholder.jpg"),
                "voiceover": scene_images.get(f"vo{i}", ""),
                "type":      scene_images.get(f"type{i}", "standard"),
            }
            if scene_images.get(f"vid{i}"):
                scene["vid"] = scene_images[f"vid{i}"]
            if scene_images.get(f"fq{i}"):
                scene["fincareQuestion"] = scene_images[f"fq{i}"]
            finvyon_scenes.append(scene)
        render_props = {"scenes": finvyon_scenes}
        render_paths = skill_render(render_props, formats=["finvyon_10"], timestamp=ts)
    else:
        render_paths = skill_render(best, formats=args.formats, video_format=video_format, timestamp=ts)

    for fmt, path in render_paths.items():
        if path:
            size = os.path.getsize(path) / (1024 * 1024)
            print(f"   ✅ {fmt}: {os.path.basename(path)} ({size:.1f}MB)")
        else:
            print(f"   ❌ {fmt}: render failed or skipped")

    # ── Step 4b: Carousel Slides ─────────────────────────────────────────────
    # Carousel slides are normally generated by the full pipeline (main.py) via the Writer.
    # In run_video_agent.py (standalone CLI), carousel_slides come from --posts-file if provided,
    # otherwise this step is skipped gracefully.
    carousel_916: list[str] = []
    carousel_11:  list[str] = []
    _cli_posts = {}
    posts_file = getattr(args, "posts_file", None)
    if posts_file and os.path.exists(posts_file):
        try:
            with open(posts_file) as _f:
                _cli_posts = json.load(_f)
        except Exception:
            pass
    if _cli_posts.get("carousel_slides"):
        print(f"\n🃏  Generating carousel slides...")
        carousel_916 = skill_generate_carousel(topic, _cli_posts, ts, aspect="916")
        carousel_11  = skill_generate_carousel(topic, _cli_posts, ts, aspect="11")
        print(f"   ✅ {len(carousel_916)} slides (9:16) + {len(carousel_11)} slides (1:1)")
    else:
        print("\n🃏  Carousel: no carousel_slides in posts — skipping. "
              "(Run full pipeline via main.py or pass --posts-file to generate carousel.)")

    # ── Step 5: Voiceover ───────────────────────────────────────────────────
    from src.quality_gate import check_video_timing
    final_916 = render_paths.get("finvyon_10") or render_paths.get("916")
    timing = {"message": "", "status": "skipped"}
    vo_path = None
    if final_916:
        print("\n🎙️  Synthesising voiceover...")
        # Single source of truth: voiceovers come from the Writer via finvyon_scenes.
        # finvyon_scenes[i]["voiceover"] = Writer's TikTok scene lines (or placeholders).
        # Fall back to standalone script generator only if scenes are unavailable.
        script = None
        vo_lines = []
        if video_format == "D" and finvyon_scenes:
            vo_lines = [s.get("voiceover", "").strip() for s in finvyon_scenes
                        if s.get("voiceover", "").strip()]
            if vo_lines:
                script = " ... ".join(vo_lines)  # "..." = ~600ms natural pause between scenes
                print(f"   Using {len(vo_lines)} scene voiceovers ({len(script.split())} words)")
        if not script:
            script = skill_generate_voiceover_script(best, {})  # standalone fallback
            vo_lines = script.split(" ... ") if script else []
            if script:
                print(f"   Using fallback voiceover script ({len(script.split())} words)")
        if not script:
            print("   ⚠️  No voiceover script — skipping TTS synthesis.")
        else:
            pass  # synthesis runs below
        vo_path = skill_synthesise_voice_elevenlabs(script, timestamp=ts) if script else None
        if not vo_path and script:
            vo_path = skill_synthesise_voice_edge(script, timestamp=ts)
        if vo_path:
            # Timing quality check
            timing = check_video_timing(vo_lines, vo_path)
            print(f"   ✅ Voiceover: {os.path.basename(vo_path)}")
            print(f"   ⏱  {timing['message']}")
            mixed = skill_mix_audio(final_916, vo_path, timestamp=ts)
            if mixed:
                if render_paths.get("finvyon_10"):
                    render_paths["finvyon_10"] = mixed
                else:
                    render_paths["916"] = mixed
                final_916 = mixed
                size = os.path.getsize(mixed) / (1024 * 1024)
                print(f"   ✅ Mixed: {os.path.basename(mixed)} ({size:.1f}MB)")
        else:
            print("   ⚠️  Voiceover synthesis failed — sending silent video.")

    # ── Step 5: Thumbnail ───────────────────────────────────────────────────
    thumbnail = None
    if final_916:
        print("\n🖼️  Extracting thumbnail...")
        thumbnail = skill_extract_thumbnail(final_916, frame_sec=3.0)
        if thumbnail:
            print(f"   ✅ {os.path.basename(thumbnail)}")

    render_paths["thumbnail"] = thumbnail

    # ── Step 6: Send to Telegram ────────────────────────────────────────────
    if args.no_telegram:
        print("\n📵 Telegram skipped (--no-telegram flag).")
        return

    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("\n⚠️  TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping Telegram send.")
        return

    # skill_send_preview expects key "916" for the main video — map finvyon_10 → 916
    if final_916:
        render_paths["916"] = final_916

    # ── Generate captions for the posting guide ─────────────────────────────
    print("\n✍️  Generating post captions...")
    posts = _generate_cli_captions(best, topic, ts)

    session_id = f"cli_{ts}"
    # Include carousel paths in render_paths so skill_send_preview picks them up automatically
    if carousel_916:
        render_paths["carousel_916"] = carousel_916
    if carousel_11:
        render_paths["carousel_11"] = carousel_11

    print(f"\n📱 Sending preview to Telegram (session: {session_id})...")
    skill_send_preview(render_paths, best, topic, token, chat_id, session_id)

    # Send copy script with captions + timing report
    import requests
    copy_script = skill_copy_script(best, posts)
    timing_line = f"\n\n🎙 <b>Timing check:</b> {timing['message']}" if timing.get("message") else ""
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": copy_script + timing_line, "parse_mode": "HTML"},
            timeout=30,
        )
    except Exception:
        pass

    print("\n✅ Done! Check Telegram for the video preview.")
    print(f"   Session ID: {session_id} (use this for callbacks)\n")


if __name__ == "__main__":
    main()
