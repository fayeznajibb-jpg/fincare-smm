"""
Fincare Auto Image Brief
=========================
Generates detailed, branded image prompts for Ideogram/DALL-E/Midjourney.
Zero API cost — pure template logic based on topic, pillar, and emotional trigger.
Output is sent to Telegram so you can paste into any free image tool.
"""

import os
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("image_brief")

# Fincare visual identity
BRAND_COLORS = {
    "primary":    "#1A2B4A",   # Deep navy
    "accent":     "#00C9A7",   # Teal/mint
    "text":       "#FFFFFF",   # White
    "background": "#0D1B2A",   # Dark navy
    "warning":    "#FF6B6B",   # Soft red (for fear/panic posts)
    "calm":       "#4ECDC4",   # Calm teal (for insight/solution posts)
}

# Visual styles per content pillar
PILLAR_STYLES = {
    "STORY": {
        "style":       "cinematic close-up, warm bokeh background, moody lighting",
        "composition": "person looking at phone/laptop with subtle red glow on face, dark room",
        "mood":        "intimate, relatable, slightly anxious but hopeful",
        "text_layout": "large emotional hook at top, smaller explanatory text below",
    },
    "DATA": {
        "style":       "clean minimal infographic, data visualization aesthetic",
        "composition": "bold percentage or number as hero element, clean dark background with subtle grid",
        "mood":        "authoritative, clear, trustworthy",
        "text_layout": "stat dominates center, source and context as smaller text",
    },
    "OPINION": {
        "style":       "bold typographic design, high contrast",
        "composition": "statement text as the main visual, minimal imagery, strong contrast",
        "mood":        "confident, opinionated, slightly provocative",
        "text_layout": "opinion statement in large bold text, supporting context below",
    },
    "QUESTION": {
        "style":       "clean conversational design, approachable",
        "composition": "question mark as design element, open space, inviting layout",
        "mood":        "curious, open, thought-provoking",
        "text_layout": "question in center, small context text below",
    },
    "INSIGHT": {
        "style":       "premium minimal design, sophisticated",
        "composition": "clean split layout — insight text on one side, abstract brain/graph visual on other",
        "mood":        "wise, calm, empowering",
        "text_layout": "mental model name as header, insight as subtext",
    },
}

# Emotional trigger color overlays
TRIGGER_COLORS = {
    "fear":           {"overlay": "#FF6B6B", "opacity": "15%", "gradient": "dark red to navy"},
    "FOMO":           {"overlay": "#FFD93D", "opacity": "15%", "gradient": "amber to dark navy"},
    "anxiety":        {"overlay": "#6C63FF", "opacity": "12%", "gradient": "purple to dark navy"},
    "overconfidence": {"overlay": "#00C9A7", "opacity": "12%", "gradient": "teal to dark navy"},
    "shame":          {"overlay": "#4ECDC4", "opacity": "10%", "gradient": "soft teal to dark navy"},
}

# Visual elements per trigger
TRIGGER_VISUALS = {
    "fear":           "subtle downward chart line in background, red tint, person checking phone nervously",
    "FOMO":           "crowd/group visual in background, one person separated, amber tones",
    "anxiety":        "abstract tangled lines or labyrinth, purple-tinted dark background",
    "overconfidence": "upward chart that then drops, person looking away confidently",
    "shame":          "single person in spotlight, everyone else blurred, soft teal tones",
}


def generate_image_brief(topic: dict, posts: dict) -> dict:
    """
    Generates a complete image brief for a post.
    Returns dict with prompt, negative_prompt, platform_specs, and telegram_message.
    Zero API cost.
    """
    pillar   = topic.get("content_pillar", "STORY")
    trigger  = topic.get("emotional_trigger", "anxiety")
    hook     = topic.get("hook", "")
    key_stat = topic.get("key_stat", "")

    style_data   = PILLAR_STYLES.get(pillar, PILLAR_STYLES["STORY"])
    color_data   = TRIGGER_COLORS.get(trigger, TRIGGER_COLORS["anxiety"])
    visual_hint  = TRIGGER_VISUALS.get(trigger, "")
    brand_colors = f"Deep navy {BRAND_COLORS['primary']}, teal accent {BRAND_COLORS['accent']}, white text"

    # Build the main image prompt
    prompt = (
        f"Social media post image for Fincare (AI investing app). "
        f"Style: {style_data['style']}. "
        f"Composition: {style_data['composition']}. "
        f"Mood: {style_data['mood']}. "
        f"Color palette: {brand_colors}. "
        f"Color overlay: {color_data['gradient']} gradient at {color_data['opacity']} opacity. "
        f"Visual element: {visual_hint}. "
        f"Text on image: '{hook[:60]}' — clean sans-serif font, white on dark background. "
        f"Text layout: {style_data['text_layout']}. "
        f"Brand logo: small 'fincare' wordmark in bottom right corner, teal color. "
        f"Overall: premium fintech aesthetic, not stock-photo-generic, dark background, modern."
    )

    negative_prompt = (
        "no cheesy stock photos, no clipart, no cartoon style, no bright white background, "
        "no generic business imagery, no rainbow colors, no clutter, no watermarks"
    )

    # Platform-specific size notes
    platform_specs = {
        "Instagram (carousel/feed)": "1080 x 1080px (square) or 1080 x 1350px (portrait 4:5)",
        "LinkedIn":                  "1200 x 627px (landscape) or 1080 x 1080px (square)",
        "Threads":                   "1080 x 1080px (square)",
        "TikTok thumbnail":          "1080 x 1920px (9:16 vertical)",
    }

    logger.success(f"Image brief generated for pillar={pillar}, trigger={trigger}")

    return {
        "prompt":          prompt,
        "negative_prompt": negative_prompt,
        "pillar":          pillar,
        "trigger":         trigger,
        "platform_specs":  platform_specs,
    }


def build_telegram_image_message(brief: dict, topic: dict) -> str:
    """Formats the image brief as a Telegram message."""
    specs_text = "\n".join(f"  • {k}: {v}" for k, v in brief["platform_specs"].items())

    return (
        f"<b>🎨 IMAGE BRIEF — {brief['pillar']} | {brief['trigger'].upper()}</b>\n"
        f"{'─'*35}\n\n"
        f"<b>📌 Topic:</b> {topic.get('topic','')[:80]}\n\n"
        f"<b>✏️ PROMPT</b> (paste into Ideogram / DALL-E / Midjourney):\n"
        f"<i>{brief['prompt'][:600]}</i>\n\n"
        f"<b>🚫 NEGATIVE PROMPT:</b>\n"
        f"<i>{brief['negative_prompt']}</i>\n\n"
        f"<b>📐 PLATFORM SIZES:</b>\n{specs_text}\n\n"
        f"{'─'*35}\n"
        f"<i>Free tools: ideogram.ai | Bing Image Creator | Adobe Firefly</i>"
    )


def send_image_brief(topic: dict, posts: dict):
    """Generates brief and sends it to Telegram. Called from main.py after approval."""
    from src.telegram_bot import send_notification
    brief   = generate_image_brief(topic, posts)
    message = build_telegram_image_message(brief, topic)
    send_notification(message)
    logger.success("Image brief sent to Telegram.")
    return brief
