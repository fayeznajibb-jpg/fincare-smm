"""
Fincare Video Generator
=========================
Generates data-driven Remotion videos for each daily post.
Uses viral intel to pick the best hook style, then calls Claude Haiku
to write VideoProps (hook, stat, insight, ctaText) for the Remotion render.

Called from main.py after write_posts(), before Telegram approval.
Renders two formats: 9:16 (TikTok/Reels) and 1:1 (Instagram/LinkedIn).

Cost: ~$0.001/day (Claude Haiku props generation)
Remotion render: free (Node.js subprocess, ~2–4 min on GitHub Actions)
"""

import os
import json
import subprocess
import anthropic
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("video_generator")

# Path to Remotion project (relative to automation/ working dir)
REMOTION_PROJECT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fincare-video"
)

OUTPUT_DIR = os.path.join(REMOTION_PROJECT, "out")

PILLAR_TO_TRIGGER_COLORS = {
    "STORY":    "anxiety",
    "DATA":     "fear",
    "OPINION":  "overconfidence",
    "QUESTION": "FOMO",
    "INSIGHT":  "anxiety",
}


def _build_video_props(topic: dict, viral_intel: dict | None) -> dict:
    """
    Calls Claude Haiku to write Remotion VideoProps from the daily topic.
    Uses viral intel to inform hook style.
    Returns a dict with: hook, stat, topic, insight, ctaText, pillar, trigger.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set.")

    pillar  = topic.get("content_pillar", "STORY")
    trigger = topic.get("emotional_trigger", PILLAR_TO_TRIGGER_COLORS.get(pillar, "anxiety"))

    viral_context = ""
    if viral_intel and viral_intel.get("top_pattern"):
        best = viral_intel.get("best_performer", {})
        adaptations = viral_intel.get("top_3_fincare_adaptations", [])
        viral_context = f"""
Viral intel from this week's top finance creators:
- Top hook style working right now: {viral_intel['top_pattern'].replace('_', ' ')}
- Top emotional trigger: {viral_intel['top_trigger']}
- Best performing creator: @{best.get('handle','—')} with {best.get('view_count',0):,} views
- Top content idea for Fincare: {adaptations[0] if adaptations else 'N/A'}

Use this viral pattern as inspiration for the hook style — but make it specific to Fincare's topic and brand.
"""

    prompt = f"""You are writing content for a Fincare TikTok/Reels video.
Fincare is a behavioural finance app that helps investors manage emotional investing.

Topic: {topic.get('topic', '')}
Angle: {topic.get('angle', '')}
Key stat: {topic.get('key_stat', '')}
Hook: {topic.get('hook', '')}
Content pillar: {pillar}
Emotional trigger: {trigger}
{viral_context}

Write VideoProps for a 68-second Remotion video. Return ONLY raw JSON:
{{
  "hook": "opening headline — under 10 words, punchy, scroll-stopping",
  "stat": "single number or % from the key stat (e.g. '67%' or '$1.7T')",
  "topic": "topic title — under 8 words",
  "insight": "core insight — under 18 words, the reframe or truth",
  "ctaText": "CTA — 2-4 words, action-oriented (e.g. 'Stop the spiral')",
  "pillar": "{pillar}",
  "trigger": "{trigger}"
}}

Rules:
- hook must start with a number, "Why", "How", or a bold claim
- Never give financial advice or say buy/sell/hold
- Always behavioural — about the investor's psychology, not stock tips
- ctaText should feel empowering, not alarming"""

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    props = json.loads(raw.strip())
    logger.success(f"VideoProps generated: hook='{props.get('hook', '')[:50]}'")
    return props


def _render_video(props: dict, composition: str, output_filename: str) -> str | None:
    """
    Runs Remotion render via subprocess.
    Returns output file path on success, None on failure.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    props_json  = json.dumps(props)

    cmd = [
        "npx", "remotion", "render",
        "src/index.ts",
        composition,
        output_path,
        f"--props={props_json}",
        "--concurrency=2",
        "--log=error",
    ]

    logger.step(f"Rendering {composition} → {output_filename}...")

    try:
        result = subprocess.run(
            cmd,
            cwd=REMOTION_PROJECT,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
        )

        if result.returncode == 0 and os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.success(f"Rendered: {output_filename} ({size_mb:.1f}MB)")
            return output_path
        else:
            logger.error(f"Render failed for {composition}: {result.stderr[:300]}")
            return None

    except subprocess.TimeoutExpired:
        logger.error(f"Render timeout for {composition} (>10min)")
        return None
    except FileNotFoundError:
        logger.error("npx not found. Node.js must be installed in the GitHub Actions runner.")
        return None
    except Exception as e:
        logger.error(f"Render error: {type(e).__name__}: {str(e)[:100]}")
        return None


def generate_video(topic: dict, posts: dict, viral_intel: dict | None = None) -> dict:
    """
    Main entry point. Called from main.py after write_posts().

    1. Generates VideoProps from topic + viral intel
    2. Renders 9:16 (TikTok/Reels) and 1:1 (Instagram/LinkedIn) formats
    3. Returns paths dict: {"916": path, "11": path, "props": props}

    Returns empty paths if Remotion is not set up (non-fatal).
    """
    logger.step("Starting video generation...")

    # Load viral intel if not passed
    if viral_intel is None:
        try:
            from src.viral_spy import load_latest_viral_intel
            viral_intel = load_latest_viral_intel()
            if viral_intel:
                logger.info(f"Loaded viral intel: top_pattern={viral_intel.get('top_pattern')}")
        except Exception:
            pass

    # Build props
    try:
        props = _build_video_props(topic, viral_intel)
    except Exception as e:
        logger.error(f"VideoProps generation failed: {type(e).__name__}: {str(e)}")
        return {"916": None, "11": None, "props": None}

    # Save props for reference
    os.makedirs("drafts", exist_ok=True)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    props_path = f"drafts/video_props_{timestamp}.json"
    with open(props_path, "w") as f:
        json.dump(props, f, indent=2)

    # Check if Remotion project exists
    if not os.path.exists(REMOTION_PROJECT):
        logger.warning(f"Remotion project not found at {REMOTION_PROJECT} — skipping render.")
        return {"916": None, "11": None, "props": props}

    # Render both formats
    path_916 = _render_video(props, "FinCare-916", f"fincare-daily-916-{timestamp}.mp4")
    path_11  = _render_video(props, "FinCare-11",  f"fincare-daily-11-{timestamp}.mp4")

    result = {"916": path_916, "11": path_11, "props": props}

    if path_916:
        logger.success(f"Video generation complete: 9:16={path_916}")
    else:
        logger.warning("Video render failed — posts will show as text-only.")

    return result
