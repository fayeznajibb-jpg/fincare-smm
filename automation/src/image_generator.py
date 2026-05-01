"""
Fincare Auto Image Generator
==============================
Primary:  fal.ai — Flux Pro model (~$0.005/image, fast, high quality)
Fallback: Ideogram v2 Turbo (~$0.02/image, text rendering specialist)

Set FAL_KEY in .env for primary.
Set IDEOGRAM_API_KEY in .env for fallback.

Get fal.ai key at: https://fal.ai (free $10 credit on signup)
"""

import os
import json
import requests
from datetime import datetime
from utils.logger import SecureLogger
from src.image_brief import generate_image_brief, PILLAR_STYLES, TRIGGER_COLORS, TRIGGER_VISUALS, BRAND_COLORS

logger = SecureLogger("image_generator")


# ── Platform specs ────────────────────────────────────────────────────────────
PLATFORM_SPECS = {
    "linkedin_company": {"width": 1280, "height": 720,  "aspect": "16:9",  "ideogram_res": "RESOLUTION_1280_720"},
    "instagram":        {"width": 1024, "height": 1024, "aspect": "1:1",   "ideogram_res": "RESOLUTION_1024_1024"},
    "threads":          {"width": 1024, "height": 1024, "aspect": "1:1",   "ideogram_res": "RESOLUTION_1024_1024"},
    "tiktok":           {"width": 576,  "height": 1024, "aspect": "9:16",  "ideogram_res": "RESOLUTION_576_1024"},
}


_VISUAL_DIRECTOR_SYSTEM = """You are the visual director for FinCare — an AI-powered emotional intelligence platform for retail investors. Your only job is to write a single, masterfully crafted image generation prompt that:
1. Visually translates the emotional core of a social media post
2. Stays locked to FinCare's brand identity
3. Will produce a premium, editorial, scroll-stopping image from a text-to-image model

You do NOT generate the image. You generate the PROMPT that an image model will use. The quality of your prompt is the difference between a generic stock photo and a cinematic, emotionally resonant brand asset.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FINCARE BRAND DNA — LOCKED, NON-NEGOTIABLE

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every image prompt you write must encode these visual rules. They are brand law.

COLOR PALETTE (always specify these):
- Dominant background: deep navy #1A2B4A — specify as "deep navy blue #1A2B4A"
- Accent: teal #00C9A7 — use sparingly (one glowing element, light reflection, or subtle highlight only)
- Shadow/depth: midnight #0D1F35
- Highlights: near-white or soft off-white for skin tones, light sources, and focal points
- Forbidden colors: bright red backgrounds, yellow, orange, neon green, hot pink, pure white backgrounds, flat gray

LIGHTING (always specify):
- Cinematic: single-source dramatic lighting, usually from screen glow, city window, or distant light
- Mood: cool-toned with one warm teal accent — never warm-dominant or golden hour
- Shadows: deep, purposeful, not murky — shadows create emotion, not confusion

COMPOSITION (always specify):
- Rule of thirds — subject offset, never centered unless intentional tension
- Depth: foreground blur or bokeh when using human subjects
- Negative space: generous — this image will have text overlaid by the designer
- Aspect ratio: always specify based on platform

HUMAN SUBJECTS (when used):
- Diverse, real-feeling humans — not stock photo perfection, not overly posed
- Emotional authenticity: micro-expressions of stress, focus, quiet contemplation, or cautious hope
- Avoid: big smiles, thumbs up, handshakes, pointing at screens with enthusiasm
- Avoid: visible logos, branded clothing, or identifiable real people
- Hands and body language carry emotion — a clenched jaw, a hand over a mouth, leaning forward at a screen
- Age range: 28–45, mixed backgrounds, urban professional but human

WHAT FINCARE IMAGES NEVER SHOW:
- Charts with visible numbers or labels
- Text, words, or numbers of any kind in the image
- Cryptocurrency coins, dollar signs, or currency symbols as main subjects
- Stock market tickers or trading terminals with readable content
- Cartoon, illustration, or flat design style (photorealistic or cinematic only)
- Extreme wealth signaling: superyachts, private jets, Lamborghinis
- Poverty signaling: broken homes, empty wallets, extreme distress imagery

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PLATFORM ASPECT RATIO RULES

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
linkedin: landscape 16:9 — horizontal composition, professional framing, more negative space on right side for text overlay
instagram: square 1:1 or portrait 4:5 — bolder composition, subject can be more centered, stronger emotional close-up acceptable
tiktok: portrait 9:16 — vertical composition, strong top-third focal point, lower two-thirds as breathing room
threads: square 1:1 — graphic and bold, works as a standalone visual with no text needed

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EMOTIONAL TRIGGER → VISUAL VOCABULARY

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
fear_of_loss:
  → Subject: person alone in a dark apartment, faint blue light from a laptop or phone
  → Environment: late night, city lights out of focus through window, stillness
  → Body language: staring at screen, hand on chin or forehead, slightly hunched
  → Mood: quiet dread with a sliver of teal light cutting through — not panic, not peace

fomo:
  → Subject: person watching a crowd move in one direction while they stand still, or face pressed to glass looking in
  → Environment: urban street at dusk, blurred movement suggesting speed, warmth outside vs cool stillness inside
  → Body language: yearning stillness — watching, not participating
  → Mood: the tension between standing firm and the pull of momentum

shame:
  → Subject: person with head slightly bowed, hands loose in lap, soft focus on face
  → Environment: minimal — empty desk, dim natural light from a single window
  → Body language: closed posture, quiet — not dramatic, private
  → Mood: the weight of a private mistake, not performance of emotion

anxiety:
  → Subject: person mid-motion — standing up from a desk at night, phone in hand, mid-decision
  → Environment: layers of half-lit screens, stacked notifications implied by light patterns
  → Body language: tense, coiled, on the edge of action
  → Mood: the three-second window before a bad financial decision

overconfidence:
  → Subject: person leaning back in chair, arms loosely open, looking at screens with premature ease
  → Environment: multiple monitors showing upward-trending data (unreadable), warm light from screens
  → Body language: relaxed when they should not be — this is irony, not aspiration
  → Mood: the calm before a fall — use composition to imply fragility

regret:
  → Subject: person looking out a rain-streaked window, reflection visible in glass
  → Environment: rainy exterior, interior warmth muted by navy tones
  → Body language: turned away from camera, contemplative, still
  → Mood: the quiet aftermath of a decision that cannot be undone

confusion:
  → Subject: person surrounded by overlapping screens or papers, slight motion blur on hands
  → Environment: information-dense but unreadable — screens, charts, headlines, all abstracted
  → Body language: leaning in, squinting slightly, trying to find signal in noise
  → Mood: the modern investor's cognitive overload — exhausting, not chaotic

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONTENT PILLAR → VISUAL METAPHOR

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STORY: human subject at the center, environmental storytelling, single character's journey implied
DATA: abstract data environment — particles, light grids, or blurred screens that suggest information without showing it
OPINION: bold compositional choice — strong diagonal, high contrast, single focal point that commands attention
QUESTION: visual ambiguity — two paths, a figure at a threshold, open door with unknown beyond
INSIGHT: revelation lighting — a shaft of teal or cool white light breaking through darkness, clarity emerging

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW TO WRITE THE PROMPT

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Your prompt must be a single paragraph of 60–120 words. No bullet points, no headers.
Write it in the style of a film director's visual note to a cinematographer.

Structure in this order:
1. Subject description (who or what is the focal point)
2. Setting and environment (where, time of day, atmosphere)
3. Lighting (source, quality, color temperature)
4. Color palette (dominant, accent)
5. Mood/emotion (the feeling the image must produce)
6. Technical quality tags (photorealistic, cinematic, editorial, 8K, no text, no watermarks)
7. Negative prompt anchor (always end here)

FORBIDDEN IN THE PROMPT TEXT:
- Never write "a person feeling [emotion]" — show it through visual choices
- Never reference FinCare, brand names, or logos
- Never write dollar amounts, percentages, or financial figures
- Never describe text that should appear in the image

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

THE STYLIST LAYER — FINVYON & OLD MONEY AESTHETICS

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are also The Stylist: an obsessive Vogue editor for illustrated characters.
Mantra: "The thickness of a line can communicate a billion dollars."

FINVYON CHARACTER RULES — when the character appears in any scene:
FINVYON is the CFA Stickman Sensei. His appearance NEVER varies. If you include him:
- Head: clean white circle, medium-weight black stroke
- Hair: salt-and-pepper, brushed-back, neat behind ears
- Glasses: thick tortoiseshell / dark espresso, rounded rectangular — intellectual and cool
- Blazer: tailored navy blue, clean sharp shoulders, simple lapel
- Shirt: light blue Oxford, open collar — no tie
- Trousers: slim khaki / beige chinos
- Shoes: dark chocolate brown penny loafers, no visible socks
- Watch: small gold circle on wrist
- Body: clean 2D illustration with simplified human proportions — NOT photorealistic, NOT a thin stickman
- Arms + legs: clean simplified illustration lines, slightly thicker than body outline, flat illustration style
- Posture: upright, confident — one hand in pocket or behind back
If FINVYON appears, his EYES must point toward the most important element in the scene
(the key financial prop, a chart, a door, a stat — never looking at the camera unless it is a direct address moment).

ENVIRONMENTAL STORYTELLING RULES:
The environment is not decoration — it is a second layer of meaning.
- Market volatility → subtle rainstorm visible through a window, streaks on glass
- Recovery or clarity → soft golden light breaking through clouds or curtains (filtered, not warm)
- Panic / bad decision → multiple screens, overlapping light sources, slight visual chaos
- Patience / long-term thinking → single clean desk lamp, one focused light source, stillness
- FOMO → warm light pouring in from outside while the interior stays cool navy
- Shame → minimal empty environment, single dim window, nothing on the desk
- Overconfidence → too many screens lit up, everything glowing — abundance before the fall
Always add at least one environmental storytelling element relevant to the emotional trigger.

OLD MONEY PROP LIBRARY (use when contextually appropriate):
- Leather-bound book on desk → wisdom, patience
- Magnifying glass → deep analysis, seeing what others miss
- Vintage globe → long-term thinking, big picture
- Teacup mid-sip → calm, unhurried, FINVYON's signature prop
- Pocket watch or gold wristwatch → time as the ultimate asset
- Chess piece (knight or king) → strategic thinking
- Rolled financial scroll → old world meets new
Never use: cryptocurrency coins, dollar bills as main subject, flashy tech gadgets, luxury cars

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATIVE EXPANSION RULES

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Before writing your prompt, ask yourself:

ABSTRACTION TEST: Can I convey this emotion with an abstract or environmental image instead of a human?
METAPHOR TEST: What physical metaphor matches this emotional trigger?
UNEXPECTED ANGLE TEST: What is the least-expected camera angle for this emotion?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SELF-EVALUATION BEFORE OUTPUT

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BRAND CHECK: Does the prompt specify deep navy, teal accent, and no text?
EMOTION CHECK: Is the emotion shown through visual choices, not stated?
CREATIVE CHECK: Would this image look different from a Getty search result?
PLATFORM CHECK: Does the composition fit the platform's aspect ratio?
NEGATIVE PROMPT CHECK: Have you specified what NOT to generate?

Return ONLY raw JSON. No preamble, no markdown. Start with { and end with }."""


def _build_prompt(topic: dict, platform: str, posts: dict = None) -> dict | None:
    """
    Uses Claude (Visual Director) to generate a cinematic image prompt.
    Returns a dict with image_prompt, negative_prompt, and api_parameters.
    Falls back to template-based prompt if Claude is unavailable.
    """
    import anthropic

    pillar   = topic.get("content_pillar", "STORY")
    trigger  = topic.get("emotional_trigger", "anxiety")

    # Map pipeline trigger names to Visual Director vocabulary
    trigger_map = {
        "fear":           "fear_of_loss",
        "FOMO":           "fomo",
        "fomo":           "fomo",
        "shame":          "shame",
        "anxiety":        "anxiety",
        "overconfidence": "overconfidence",
        "regret":         "regret",
        "confusion":      "confusion",
        "fear_of_loss":   "fear_of_loss",
    }
    trigger_vd = trigger_map.get(trigger, "anxiety")

    # Pick the most relevant post text for context
    post_text = ""
    if posts:
        for key in ["linkedin_company", "instagram_caption", "threads_post", "tiktok_caption"]:
            if posts.get(key):
                post_text = posts[key][:600]
                break

    # Platform normalization
    platform_vd = {
        "linkedin_company": "linkedin",
        "instagram_caption": "instagram",
        "tiktok_caption": "tiktok",
        "threads_post": "threads",
    }.get(platform, platform)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key and post_text:
        try:
            client = anthropic.Anthropic(api_key=api_key)
            # Resolve platform-specific values upfront so Claude doesn't pick from pipe-separated options
            fal_size_for_platform = {
                "linkedin": "landscape_16_9",
                "instagram": "square_hd",
                "tiktok": "portrait_16_9",
                "threads": "square_hd",
            }.get(platform_vd, "square_hd")

            ideogram_ratio_for_platform = {
                "linkedin": "ASPECT_16_9",
                "instagram": "ASPECT_1_1",
                "tiktok": "ASPECT_9_16",
                "threads": "ASPECT_1_1",
            }.get(platform_vd, "ASPECT_1_1")

            platform_ratio_for_platform = {
                "linkedin": "16:9",
                "instagram": "1:1",
                "tiktok": "9:16",
                "threads": "1:1",
            }.get(platform_vd, "1:1")

            user_message = (
                f"post_text: {post_text}\n"
                f"topic: {topic.get('topic', '')}\n"
                f"emotional_trigger: {trigger_vd}\n"
                f"platform: {platform_vd}\n"
                f"content_pillar: {pillar}\n\n"
                "Return ONLY raw JSON. No explanation, no markdown, no preamble. Start with {{ and end with }}:\n"
                '{{\n'
                '  "image_prompt": "60-120 word cinematic prompt as a single paragraph",\n'
                '  "negative_prompt": "comma-separated visual elements to suppress",\n'
                '  "visual_concept": "one sentence — core visual metaphor",\n'
                f'  "platform_ratio": "{platform_ratio_for_platform}",\n'
                '  "api_parameters": {{\n'
                '    "fal_ai": {{\n'
                f'      "image_size": "{fal_size_for_platform}",\n'
                '      "num_inference_steps": 28,\n'
                '      "guidance_scale": 3.5,\n'
                '      "num_images": 1,\n'
                '      "enable_safety_checker": true\n'
                '    }},\n'
                '    "ideogram": {{\n'
                f'      "aspect_ratio": "{ideogram_ratio_for_platform}",\n'
                '      "model": "V_2_TURBO",\n'
                '      "style_type": "REALISTIC",\n'
                '      "negative_prompt": "same as negative_prompt above"\n'
                '    }}\n'
                '  }},\n'
                '  "creative_direction_note": "one sentence for the content team"\n'
                '}}'
            )
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=_VISUAL_DIRECTOR_SYSTEM,
                messages=[{"role": "user", "content": user_message}]
            )
            raw = resp.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            # Ensure it ends with closing brace (truncation guard)
            if not raw.endswith("}"):
                last_brace = raw.rfind("}")
                if last_brace != -1:
                    raw = raw[:last_brace + 1]
            result = json.loads(raw)
            logger.info(f"Visual Director prompt built ({len(result.get('image_prompt', ''))} chars)")
            if result.get("creative_direction_note"):
                logger.info(f"Visual concept: {result['creative_direction_note']}")
            return result

        except Exception as e:
            logger.warning(f"Visual Director failed ({type(e).__name__}) — using template fallback.")

    # ── Template fallback ─────────────────────────────────────────────────
    style_data  = PILLAR_STYLES.get(pillar, PILLAR_STYLES["STORY"])
    visual_hint = TRIGGER_VISUALS.get(trigger, "abstract financial")

    platform_ratio_map = {
        "linkedin": "16:9", "linkedin_company": "16:9",
        "instagram": "1:1", "instagram_caption": "1:1",
        "tiktok": "9:16",   "tiktok_caption": "9:16",
        "threads": "1:1",   "threads_post": "1:1",
    }
    fal_size_map = {
        "16:9": "landscape_16_9", "1:1": "square_hd",
        "4:5": "portrait_4_5",   "9:16": "portrait_16_9",
    }
    ideogram_ratio_map = {
        "16:9": "ASPECT_16_9", "1:1": "ASPECT_1_1",
        "4:5": "ASPECT_4_5",  "9:16": "ASPECT_9_16",
    }

    ratio = platform_ratio_map.get(platform, "1:1")
    prompt = (
        f"Professional editorial photograph for a fintech brand. "
        f"Style: {style_data['style']}. Composition: {style_data['composition']}. "
        f"Mood: {style_data['mood']}. Visual theme: {visual_hint}. "
        f"Deep navy blue #1A2B4A background, subtle teal #00C9A7 accent light. "
        f"Cinematic single-source lighting. No text, no words, no logos. "
        f"Photorealistic, 8K, editorial quality."
    )
    negative = (
        "text, words, letters, logos, watermarks, readable charts, bright red background, "
        "yellow, orange, neon, cartoon, illustration, flat design, stock photo aesthetic, "
        "plastic skin, perfect smile, thumbs up, currency symbols, overexposed"
    )
    return {
        "image_prompt": prompt,
        "negative_prompt": negative,
        "visual_concept": f"Template fallback: {visual_hint} for {trigger} trigger.",
        "platform_ratio": ratio,
        "api_parameters": {
            "fal_ai": {
                "image_size": fal_size_map.get(ratio, "square_hd"),
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "num_images": 1,
                "enable_safety_checker": True,
            },
            "ideogram": {
                "aspect_ratio": ideogram_ratio_map.get(ratio, "ASPECT_1_1"),
                "model": "V_2_TURBO",
                "style_type": "REALISTIC",
                "negative_prompt": negative,
            },
        },
    }


# ── fal.ai (primary) ──────────────────────────────────────────────────────────

def _generate_fal(prompt_data: dict, platform: str) -> str | None:
    """Generate image via fal.ai Flux Pro using prompt_data from Visual Director.
    Returns local file path or None."""
    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        return None

    try:
        import fal_client
        os.environ["FAL_KEY"] = fal_key

        fal_params = prompt_data.get("api_parameters", {}).get("fal_ai", {})
        image_size = fal_params.get("image_size", "square_hd")

        logger.step(f"fal.ai Flux Pro — generating {platform} image ({image_size})...")

        result = fal_client.run(
            "fal-ai/flux-pro/v1.1",
            arguments={
                "prompt":                 prompt_data["image_prompt"],
                "image_size":             image_size,
                "num_inference_steps":    fal_params.get("num_inference_steps", 28),
                "guidance_scale":         fal_params.get("guidance_scale", 3.5),
                "num_images":             1,
                "output_format":          "jpeg",
                "enable_safety_checker":  fal_params.get("enable_safety_checker", True),
            }
        )

        image_url = result["images"][0]["url"]
        return _download_image(image_url, platform, source="fal")

    except Exception as e:
        logger.warning(f"fal.ai failed ({type(e).__name__}: {str(e)[:80]}) — trying Ideogram fallback.")
        return None


# ── Ideogram (fallback) ───────────────────────────────────────────────────────

def _generate_ideogram(prompt_data: dict, platform: str) -> str | None:
    """Generate image via Ideogram v2 Turbo using prompt_data from Visual Director.
    Returns local file path or None."""
    api_key = os.getenv("IDEOGRAM_API_KEY")
    if not api_key:
        return None

    ideogram_params = prompt_data.get("api_parameters", {}).get("ideogram", {})

    payload = {
        "image_request": {
            "prompt":               prompt_data["image_prompt"],
            "negative_prompt":      prompt_data.get("negative_prompt", ""),
            "model":                ideogram_params.get("model", "V_2_TURBO"),
            "magic_prompt_option":  "OFF",
            "style_type":           ideogram_params.get("style_type", "REALISTIC"),
            "aspect_ratio":         ideogram_params.get("aspect_ratio", "ASPECT_1_1"),
        }
    }

    logger.step(f"Ideogram v2 — generating {platform} image...")

    try:
        resp = requests.post(
            "https://api.ideogram.ai/generate",
            json=payload,
            headers={"Api-Key": api_key, "Content-Type": "application/json"},
            timeout=60
        )
        resp.raise_for_status()
        image_url = resp.json()["data"][0]["url"]
        return _download_image(image_url, platform, source="ideogram")
    except Exception as e:
        logger.error(f"Ideogram failed: {type(e).__name__} — {str(e)[:80]}")
        return None


# ── Public entry point ────────────────────────────────────────────────────────

def generate_post_image(topic: dict, posts: dict, platform: str) -> str | None:
    """
    Generates a branded image for the given platform based on actual post content.
    Calls Visual Director (Claude Haiku) once to build the cinematic prompt,
    then tries fal.ai first, falls back to Ideogram.
    Returns local file path or None.
    """
    prompt_data = _build_prompt(topic, platform, posts)
    if not prompt_data:
        logger.warning("Image prompt generation returned nothing — skipping.")
        return None

    # Try fal.ai first
    path = _generate_fal(prompt_data, platform)
    if path:
        return path

    # Fallback to Ideogram
    path = _generate_ideogram(prompt_data, platform)
    if path:
        return path

    logger.warning("No image API key set (FAL_KEY or IDEOGRAM_API_KEY) — skipping image generation.")
    return None


def _download_image(url: str, platform: str, source: str = "api") -> str | None:
    """Downloads generated image to drafts/images/."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        os.makedirs("drafts/images", exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"drafts/images/generated_{platform}_{source}_{ts}.jpg"
        with open(path, "wb") as f:
            f.write(resp.content)

        size_kb = os.path.getsize(path) // 1024
        logger.success(f"Image saved: {path} ({size_kb}KB)")
        return path

    except Exception as e:
        logger.error(f"Image download failed: {type(e).__name__}")
        return None
