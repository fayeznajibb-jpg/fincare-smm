"""
Fincare Auto Image Generator
==============================
Calls Ideogram v2 API to auto-generate branded infographic images
for each platform post. Image is shown in Telegram as a preview
(photo + caption) before the user approves.

Cost: ~$0.02 per image (Ideogram V2 Turbo)
Requires: IDEOGRAM_API_KEY in GitHub Secrets / .env
"""

import os
import requests
from datetime import datetime
from utils.logger import SecureLogger
from src.image_brief import generate_image_brief, PILLAR_STYLES, TRIGGER_COLORS, TRIGGER_VISUALS, BRAND_COLORS

logger = SecureLogger("image_generator")

IDEOGRAM_API = "https://api.ideogram.ai/generate"

# Platform → Ideogram resolution + aspect
PLATFORM_SPECS = {
    "linkedin_company": {"resolution": "RESOLUTION_1280_720",  "aspect_ratio": "ASPECT_16_9"},
    "instagram":        {"resolution": "RESOLUTION_1024_1024", "aspect_ratio": "ASPECT_1_1"},
    "threads":          {"resolution": "RESOLUTION_1024_1024", "aspect_ratio": "ASPECT_1_1"},
    "tiktok":           {"resolution": "RESOLUTION_576_1024",  "aspect_ratio": "ASPECT_9_16"},
}


def generate_post_image(topic: dict, posts: dict, platform: str) -> str | None:
    """
    Generates a branded infographic image for the given platform post.
    Returns local file path on success, None if API key missing or call fails.
    """
    api_key = os.getenv("IDEOGRAM_API_KEY")
    if not api_key:
        logger.warning("IDEOGRAM_API_KEY not set — skipping image generation.")
        return None

    pillar  = topic.get("content_pillar", "STORY")
    trigger = topic.get("emotional_trigger", "anxiety")
    hook    = topic.get("hook", topic.get("topic", ""))[:60]

    style_data  = PILLAR_STYLES.get(pillar, PILLAR_STYLES["STORY"])
    color_data  = TRIGGER_COLORS.get(trigger, TRIGGER_COLORS["anxiety"])
    visual_hint = TRIGGER_VISUALS.get(trigger, "")

    prompt = (
        f"Social media infographic for Fincare AI investing app. "
        f"Style: {style_data['style']}. "
        f"Composition: {style_data['composition']}. "
        f"Mood: {style_data['mood']}. "
        f"Color palette: deep navy {BRAND_COLORS['primary']}, teal accent {BRAND_COLORS['accent']}, white text. "
        f"Color gradient: {color_data['gradient']} at {color_data['opacity']} opacity. "
        f"Visual: {visual_hint}. "
        f"Bold text overlay: '{hook}' — clean sans-serif, white on dark background. "
        f"Small 'fincare' wordmark in bottom right, teal. "
        f"Premium fintech aesthetic. Dark background. Modern minimal design."
    )

    negative_prompt = (
        "no cheesy stock photos, no clipart, no cartoon, no bright white background, "
        "no generic business imagery, no rainbow colors, no clutter, no watermarks, "
        "no people's faces, no text errors"
    )

    specs = PLATFORM_SPECS.get(platform, PLATFORM_SPECS["linkedin_company"])

    payload = {
        "image_request": {
            "prompt":          prompt,
            "negative_prompt": negative_prompt,
            "model":           "V_2_TURBO",
            "magic_prompt_option": "OFF",
            "style_type":      "DESIGN",
            "resolution":      specs["resolution"],
        }
    }

    headers = {
        "Api-Key":      api_key,
        "Content-Type": "application/json",
    }

    logger.step(f"Generating image for {platform} (pillar={pillar}, trigger={trigger})...")

    try:
        resp = requests.post(IDEOGRAM_API, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        image_url = data["data"][0]["url"]
        return _download_image(image_url, platform)

    except requests.RequestException as e:
        logger.error(f"Ideogram API error: {type(e).__name__} — {str(e)[:100]}")
        return None
    except (KeyError, IndexError) as e:
        logger.error(f"Unexpected Ideogram response format: {type(e).__name__}")
        return None


def _download_image(url: str, platform: str) -> str | None:
    """Downloads generated image to local drafts/images/ folder."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        os.makedirs("drafts/images", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"drafts/images/generated_{platform}_{timestamp}.jpg"

        with open(path, "wb") as f:
            f.write(resp.content)

        logger.success(f"Image saved: {path}")
        return path

    except Exception as e:
        logger.error(f"Image download failed: {type(e).__name__}")
        return None
