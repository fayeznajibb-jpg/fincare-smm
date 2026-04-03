"""
Fincare Video Agent
=====================
A skills-based agent that generates, renders, and delivers Remotion videos
for manual review and posting via Telegram.

Skills:
  skill_select_template  → maps pillar to visual style
  skill_generate_props   → Claude Haiku writes 3 hook variants, picks best
  skill_render           → npx remotion render (9:16 + 1:1)
  skill_extract_thumbnail → ffmpeg frame at 3s → cover.jpg
  skill_send_preview     → sends thumbnail + video + action buttons to Telegram
  skill_copy_script      → builds copy-paste manual posting guide

Entry point: run(topic, posts, viral_intel) → dict

Runs locally only (fincare-video/ is not in the repo).
All skills are non-fatal — errors are caught and logged.
"""

import os
import json
import subprocess
import anthropic
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("video_agent")

# ─── Paths ────────────────────────────────────────────────────────────────────

REMOTION_PROJECT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fincare-video"
)
OUTPUT_DIR  = os.path.join(REMOTION_PROJECT, "out")
DRAFTS_DIR  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "drafts"))

# ─── Template map ─────────────────────────────────────────────────────────────

PILLAR_TO_TEMPLATE = {
    "STORY":    "emotional",
    "DATA":     "data",
    "OPINION":  "opinion",
    "QUESTION": "question",
    "INSIGHT":  "insight",
}

PILLAR_TO_TRIGGER = {
    "STORY":    "anxiety",
    "DATA":     "fear",
    "OPINION":  "overconfidence",
    "QUESTION": "FOMO",
    "INSIGHT":  "anxiety",
}

COMPOSITION_IDS = {
    "916": "FinCare-916",
    "11":  "FinCare-11",
    "169": "FinCare-169",
}


# ─── Skill 1: Select Template ─────────────────────────────────────────────────

def skill_select_template(topic: dict) -> str:
    """
    Maps content_pillar → visual template variant name.
    Returns one of: emotional | data | opinion | question | insight
    """
    pillar = topic.get("content_pillar", "STORY").upper()
    template = PILLAR_TO_TEMPLATE.get(pillar, "emotional")
    logger.info(f"Template selected: {template} (pillar={pillar})")
    return template


# ─── Skill 2: Generate Props ──────────────────────────────────────────────────

def _hook_score(hook: str) -> int:
    """Score a hook string — higher is better."""
    score = 0
    if hook and hook[0].isdigit():
        score += 3                           # starts with a number
    words = hook.split()
    if len(words) <= 9:
        score += 2                           # concise
    if any(w in hook.lower() for w in ["you", "your"]):
        score += 2                           # personal
    if "?" in hook:
        score += 1                           # question hooks
    if hook[0].isupper():
        score += 1                           # capitalised
    return score


def skill_generate_props(
    topic: dict,
    viral_intel: dict | None = None,
    n: int = 3,
) -> list[dict]:
    """
    Calls Claude Haiku to generate N hook variants for the topic.
    Sorts them by hook strength heuristic.
    Returns list of VideoProps dicts (best first).
    Cost: ~$0.001 per call.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set.")

    pillar  = topic.get("content_pillar", "STORY")
    trigger = topic.get("emotional_trigger", PILLAR_TO_TRIGGER.get(pillar, "anxiety"))

    viral_context = ""
    if viral_intel and viral_intel.get("top_pattern"):
        best = viral_intel.get("best_performer", {})
        adaptations = viral_intel.get("top_3_fincare_adaptations", [])
        viral_context = (
            f"\nViral intel this week:\n"
            f"- Top hook style: {viral_intel['top_pattern'].replace('_', ' ')}\n"
            f"- Top trigger: {viral_intel.get('top_trigger', '')}\n"
            f"- Best performer: @{best.get('handle','—')} ({best.get('view_count',0):,} views)\n"
            f"- Fincare content idea: {adaptations[0] if adaptations else 'N/A'}\n"
        )

    prompt = f"""You are writing content for {n} different versions of a Fincare TikTok/Reels video.
Fincare is a behavioural finance app — helps investors manage emotional investing.

Topic: {topic.get('topic', '')}
Angle: {topic.get('angle', '')}
Key stat: {topic.get('key_stat', '')}
Hook: {topic.get('hook', '')}
Pillar: {pillar}
Trigger: {trigger}
{viral_context}

Write {n} DIFFERENT VideoProps variations. Each must have a DIFFERENT hook style.
Variation 1: shock stat hook (starts with a number)
Variation 2: question hook (starts with "Why" or "How" or a question)
Variation 3: bold claim hook (provocative statement)

Return ONLY a raw JSON array of {n} objects, each with:
{{
  "hook": "under 10 words, scroll-stopping",
  "stat": "single number/% (e.g. '67%' or '$1.7T')",
  "topic": "under 8 words",
  "insight": "core insight under 18 words — the reframe",
  "ctaText": "2-4 words, empowering (e.g. 'Stop the spiral')",
  "pillar": "{pillar}",
  "trigger": "{trigger}"
}}

Rules:
- Never give financial advice (no buy/sell/hold)
- Always behavioural — about psychology, not stock picks
- Each hook must feel genuinely different in style"""

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    variants = json.loads(raw.strip())
    if not isinstance(variants, list):
        variants = [variants]

    # Sort by hook score — best first
    variants.sort(key=lambda v: _hook_score(v.get("hook", "")), reverse=True)
    logger.success(f"Generated {len(variants)} prop variants. Best hook: '{variants[0].get('hook', '')[:50]}'")
    return variants


# ─── Skill 3: Render ──────────────────────────────────────────────────────────

def skill_render(
    props: dict,
    formats: list[str] | None = None,
    timestamp: str | None = None,
) -> dict:
    """
    Runs npx remotion render for each requested format.
    formats: list of "916", "11", "169"
    Returns: {"916": path|None, "11": path|None, "169": path|None}
    """
    if formats is None:
        formats = ["916", "11"]

    if not os.path.exists(REMOTION_PROJECT):
        logger.warning(f"Remotion project not found at {REMOTION_PROJECT} — skipping render.")
        return {f: None for f in formats}

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    props_json = json.dumps(props)
    results = {}

    for fmt in formats:
        comp_id  = COMPOSITION_IDS.get(fmt)
        if not comp_id:
            results[fmt] = None
            continue

        out_file = f"fincare-{fmt}-{ts}.mp4"
        out_path = os.path.join(OUTPUT_DIR, out_file)

        cmd = [
            "npx", "remotion", "render",
            "src/index.ts",
            comp_id,
            out_path,
            f"--props={props_json}",
            "--concurrency=2",
            "--log=error",
        ]

        logger.step(f"Rendering {comp_id} → {out_file}...")
        try:
            result = subprocess.run(
                cmd,
                cwd=REMOTION_PROJECT,
                capture_output=True,
                text=True,
                timeout=480,  # 8 min
            )
            if result.returncode == 0 and os.path.exists(out_path):
                size_mb = os.path.getsize(out_path) / (1024 * 1024)
                logger.success(f"Rendered {out_file} ({size_mb:.1f}MB)")
                results[fmt] = out_path
            else:
                logger.error(f"Render failed ({comp_id}): {result.stderr[:300]}")
                results[fmt] = None
        except subprocess.TimeoutExpired:
            logger.error(f"Render timeout for {comp_id} (>8min)")
            results[fmt] = None
        except FileNotFoundError:
            logger.error("npx not found — Node.js must be installed.")
            results[fmt] = None
        except Exception as e:
            logger.error(f"Render error: {type(e).__name__}: {str(e)[:100]}")
            results[fmt] = None

    return results


# ─── Skill 4: Extract Thumbnail ───────────────────────────────────────────────

def skill_extract_thumbnail(video_path: str, frame_sec: float = 3.0) -> str | None:
    """
    Extracts a single frame from the video using ffmpeg.
    frame_sec=3.0 → after the logo animation settles.
    Returns: path to thumbnail jpg, or None if ffmpeg unavailable.
    """
    if not video_path or not os.path.exists(video_path):
        return None

    thumb_path = video_path.replace(".mp4", "_thumb.jpg")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(frame_sec),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        thumb_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and os.path.exists(thumb_path):
            logger.success(f"Thumbnail extracted: {os.path.basename(thumb_path)}")
            return thumb_path
        else:
            logger.warning("ffmpeg thumbnail extraction failed.")
            return None
    except FileNotFoundError:
        logger.warning("ffmpeg not installed — no thumbnail. Run: brew install ffmpeg")
        return None
    except Exception as e:
        logger.warning(f"Thumbnail error: {type(e).__name__}")
        return None


# ─── Skill 5: Send Preview ────────────────────────────────────────────────────

def skill_send_preview(
    video_paths: dict,
    props: dict,
    topic: dict,
    token: str,
    chat_id: str,
    session_id: str,
) -> None:
    """
    Sends to Telegram:
    1. Thumbnail image with hook summary
    2. 9:16 video with caption
    3. Action buttons: New Version / Different Hook / Use This / Copy Script
    """
    import requests
    TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

    def _send_msg(text, reply_markup=None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            requests.post(
                TELEGRAM_API.format(token=token, method="sendMessage"),
                json=payload, timeout=30
            )
        except Exception:
            pass

    def _send_photo(path, caption):
        try:
            with open(path, "rb") as f:
                requests.post(
                    TELEGRAM_API.format(token=token, method="sendPhoto"),
                    data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=60
                )
        except Exception:
            pass

    def _send_video(path, caption):
        try:
            with open(path, "rb") as f:
                requests.post(
                    TELEGRAM_API.format(token=token, method="sendVideo"),
                    data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"video": f},
                    timeout=120
                )
        except Exception:
            pass

    hook     = props.get("hook", "")
    insight  = props.get("insight", "")
    trigger  = props.get("trigger", "")
    pillar   = props.get("pillar", "")
    cta      = props.get("ctaText", "")
    stat     = props.get("stat", "")

    # ── 1. Thumbnail (if available) ──
    thumb = video_paths.get("thumbnail")
    thumb_caption = (
        f"🎬 <b>Fincare Video Preview</b>\n\n"
        f"<b>Hook:</b> {hook}\n"
        f"<b>Stat:</b> {stat}\n"
        f"<b>Insight:</b> {insight}\n"
        f"<b>Trigger:</b> {trigger} · <b>Pillar:</b> {pillar}\n"
        f"<b>CTA:</b> {cta}"
    )
    if thumb and os.path.exists(thumb):
        _send_photo(thumb, thumb_caption)
    else:
        _send_msg(thumb_caption)

    # ── 2. 9:16 Video ──
    path_916 = video_paths.get("916")
    if path_916 and os.path.exists(path_916):
        size_mb = os.path.getsize(path_916) / (1024 * 1024)
        video_caption = (
            f"🎵 <b>9:16 · TikTok / Reels</b> · {size_mb:.1f}MB\n\n"
            f"<i>{hook}</i>"
        )
        _send_video(path_916, video_caption)

    # ── 3. 1:1 Video (square) ──
    path_11 = video_paths.get("11")
    if path_11 and os.path.exists(path_11):
        sq_caption = f"📐 <b>1:1 · Instagram Feed / LinkedIn</b>"
        _send_video(path_11, sq_caption)

    # ── 4. Action buttons ──
    _send_msg(
        "What would you like to do with this video?",
        reply_markup={"inline_keyboard": [
            [
                {"text": "🔄 New Version",     "callback_data": f"regen_video_{session_id}"},
                {"text": "🎯 Different Hook",  "callback_data": f"diff_hook_{session_id}"},
            ],
            [
                {"text": "✅ Use This",         "callback_data": f"approve_video_{session_id}"},
                {"text": "📋 Copy Script",      "callback_data": f"copy_script_{session_id}"},
            ],
            [
                {"text": "↩️ Back to Menu",    "callback_data": f"back_{session_id}"},
            ],
        ]},
    )

    logger.success("Video preview sent to Telegram.")


# ─── Skill 6: Copy Script ─────────────────────────────────────────────────────

def skill_copy_script(props: dict, posts: dict) -> str:
    """
    Builds a formatted manual posting guide ready to copy-paste.
    """
    hook    = props.get("hook", "")
    insight = props.get("insight", "")
    cta     = props.get("ctaText", "")

    tiktok_caption   = posts.get("tiktok_caption", "")
    instagram_caption = posts.get("instagram_caption", "")
    hashtags_tiktok  = posts.get("hashtags_tiktok", "")
    hashtags_ig      = posts.get("hashtags_instagram", "")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📋 <b>Manual Posting Guide</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n",

        f"🎣 <b>Hook (spoken intro):</b>",
        f"<code>{hook}</code>\n",

        f"💡 <b>Core insight (your talking point):</b>",
        f"<code>{insight}</code>\n",

        f"📣 <b>CTA to say at end:</b>",
        f"<code>{cta} — follow for more</code>\n",

        "─── 🎵 TikTok Caption ───",
        f"<code>{tiktok_caption[:800] if tiktok_caption else '(no TikTok caption written)'}</code>\n",

        "─── 📸 Instagram Caption ───",
        f"<code>{instagram_caption[:800] if instagram_caption else '(no Instagram caption written)'}</code>\n",
    ]

    if hashtags_tiktok:
        lines += ["─── TikTok Hashtags ───", f"<code>{hashtags_tiktok}</code>\n"]
    if hashtags_ig:
        lines += ["─── Instagram Hashtags ───", f"<code>{hashtags_ig}</code>\n"]

    lines += [
        "─── ⏰ Best time to post ───",
        "TikTok: 7–9pm your local time",
        "Instagram Reels: 6–9pm",
        "LinkedIn: Tue–Thu 8–10am\n",

        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    return "\n".join(lines)


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def run(
    topic: dict,
    posts: dict,
    viral_intel: dict | None = None,
) -> dict:
    """
    Main entry point. Called from main.py after write_posts().

    Orchestrates all skills:
      1. Select template
      2. Generate 3 hook variants → pick best
      3. Render 9:16 + 1:1
      4. Extract thumbnail
      5. Save session to drafts/

    Returns:
      {
        "916": path|None,
        "11":  path|None,
        "thumbnail": path|None,
        "props": best_props,
        "variants": [all 3 variants],
        "timestamp": ts,
      }

    Non-fatal — returns empty dict on full failure.
    """
    logger.step("Video agent starting...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Load viral intel if not passed
    if viral_intel is None:
        try:
            from src.viral_spy import load_latest_viral_intel
            viral_intel = load_latest_viral_intel()
        except Exception:
            pass

    # ── Step 1: Template ──────────────────────────────────────────
    try:
        _template = skill_select_template(topic)
    except Exception as e:
        logger.warning(f"Template selection failed: {type(e).__name__}")
        _template = "emotional"

    # ── Step 2: Generate props variants ───────────────────────────
    try:
        variants = skill_generate_props(topic, viral_intel, n=3)
        best_props = variants[0]
    except Exception as e:
        logger.error(f"Props generation failed: {type(e).__name__}: {str(e)}")
        return {"916": None, "11": None, "thumbnail": None, "props": None, "variants": []}

    # Save props for reference + regeneration
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    props_path = os.path.join(DRAFTS_DIR, f"video_props_{ts}.json")
    with open(props_path, "w") as f:
        json.dump({"best": best_props, "variants": variants, "topic": topic}, f, indent=2)

    # ── Step 3: Render ────────────────────────────────────────────
    try:
        render_paths = skill_render(best_props, formats=["916", "11"], timestamp=ts)
    except Exception as e:
        logger.error(f"Render failed: {type(e).__name__}: {str(e)}")
        render_paths = {"916": None, "11": None}

    # ── Step 4: Thumbnail ─────────────────────────────────────────
    thumbnail = None
    if render_paths.get("916"):
        try:
            thumbnail = skill_extract_thumbnail(render_paths["916"], frame_sec=3.0)
        except Exception as e:
            logger.warning(f"Thumbnail failed (non-critical): {type(e).__name__}")

    result = {
        "916":       render_paths.get("916"),
        "11":        render_paths.get("11"),
        "thumbnail": thumbnail,
        "props":     best_props,
        "variants":  variants,
        "timestamp": ts,
    }

    if result["916"]:
        logger.success(f"Video agent done. 9:16={os.path.basename(result['916'])}")
    else:
        logger.warning("Video agent: render skipped or failed. Props saved for later use.")

    return result
