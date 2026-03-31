import os
import json
import anthropic
from utils.logger import SecureLogger
from utils.validators import validate_post_lengths, sanitize_content

logger = SecureLogger("writer")


def write_posts(topic: dict) -> dict:
    """
    Takes a researched topic and writes platform-specific posts using Claude.
    Validates character limits and sanitizes all content before returning.
    Returns a dict with posts for all platforms.
    """
    logger.step(f"Writing posts for topic: {topic['topic']}")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    # Load brand voice
    voice_path = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'brand_voice.txt')
    with open(voice_path, 'r', encoding='utf-8') as f:
        brand_voice = f.read()

    system_prompt = f"""
You are the official social media copywriter for Fincare (aifincare.com).

{brand_voice}

== YOUR TASK ==
Write social media posts for ALL platforms based on the topic provided.
Return ONLY a raw JSON object — no markdown, no explanation.

== STRICT CHARACTER LIMITS (HARD RULES) ==
- linkedin_personal: MAX 3000 characters
- linkedin_company: MAX 700 characters
- instagram_caption: MAX 2200 characters
- tiktok_caption: MAX 2200 characters
- threads_post: MAX 500 characters

== JSON OUTPUT FORMAT ==
{{
  "linkedin_personal": "full post text for personal profile",
  "linkedin_company": "compressed version for company page",
  "instagram_caption": "caption with hook + body + hashtags",
  "tiktok_caption": "POV-style caption + hashtags",
  "threads_post": "short punchy post ending with a question",
  "instagram_slide_texts": [
    "Slide 1 hook (under 60 chars)",
    "Slide 2 (under 80 chars)",
    "Slide 3 (under 80 chars)",
    "Slide 4 (under 80 chars)",
    "Slide 5 (under 80 chars)",
    "Slide 6 (under 80 chars)",
    "Slide 7 CTA (under 60 chars)"
  ],
  "hashtags_linkedin": "#Tag1 #Tag2 #Tag3 #Tag4 #Tag5",
  "hashtags_instagram": "#tag1 #tag2 ... (10-15 tags)",
  "hashtags_tiktok": "#tag1 #tag2 ... (5-10 tags)"
}}
"""

    user_message = f"""
Write posts based on this researched topic:

Topic: {topic['topic']}
Angle: {topic['angle']}
Hook: {topic['hook']}
Key stat: {topic['key_stat']}
Emotional trigger: {topic['emotional_trigger']}
Why today: {topic['why_today']}

Remember:
- linkedin_personal must include: data point → hidden truth → angel voice concept → Fincare solution → question
- linkedin_company must be under 700 characters — sharp and punchy
- instagram_caption first 125 characters must serve as the hook
- tiktok_caption first 100 characters must hook immediately
- threads_post must be under 500 characters and end with a question
- NEVER give financial advice or tell people to buy/sell/hold
- Always include "Not financial advice" disclaimer where appropriate
- Use "aifincare.com" as the CTA link
- Return only raw JSON
"""

    client = anthropic.Anthropic(api_key=api_key)

    logger.step("Calling Claude API to write posts...")
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    raw_response = message.content[0].text.strip()

    # Strip markdown code block if present
    if raw_response.startswith("```"):
        raw_response = raw_response.split("```")[1]
        if raw_response.startswith("json"):
            raw_response = raw_response[4:]
        raw_response = raw_response.strip()

    posts = json.loads(raw_response)

    # Sanitize all text fields
    text_fields = [
        "linkedin_personal", "linkedin_company",
        "instagram_caption", "tiktok_caption", "threads_post"
    ]
    for field in text_fields:
        if field in posts:
            posts[field] = sanitize_content(posts[field])

    if "instagram_slide_texts" in posts:
        posts["instagram_slide_texts"] = [
            sanitize_content(slide) for slide in posts["instagram_slide_texts"]
        ]

    # Validate character limits
    is_valid, errors = validate_post_lengths(posts)
    if not is_valid:
        logger.warning(f"Character limit issues detected: {errors}")
        posts = _trim_posts(posts, errors)
        # Re-validate after trimming
        is_valid, errors = validate_post_lengths(posts)
        if not is_valid:
            raise ValueError(f"Posts still exceed limits after trimming: {errors}")

    logger.success("All posts written and validated successfully.")
    return posts


def _trim_posts(posts: dict, errors: list) -> dict:
    """
    Trims posts that exceed character limits by cutting at the last complete sentence.
    """
    from utils.validators import LIMITS
    import re

    for error in errors:
        platform = error.split(":")[0].strip()
        limit = LIMITS.get(platform, 3000)
        content = posts.get(platform, "")

        if len(content) > limit:
            # Trim to limit, then cut at last complete sentence
            trimmed = content[:limit]
            last_period = max(
                trimmed.rfind('. '),
                trimmed.rfind('.\n'),
                trimmed.rfind('! '),
                trimmed.rfind('? ')
            )
            if last_period > limit * 0.7:  # Only cut at sentence if not too aggressive
                trimmed = trimmed[:last_period + 1]
            posts[platform] = trimmed.strip()
            logger.warning(f"Trimmed {platform} from {len(content)} to {len(posts[platform])} chars")

    return posts
