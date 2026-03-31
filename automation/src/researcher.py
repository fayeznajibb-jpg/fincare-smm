import os
import json
import anthropic
from utils.logger import SecureLogger

logger = SecureLogger("researcher")


def research_topic() -> dict:
    """
    Uses Claude to identify a trending, high-potential topic for today's post.
    Returns a structured topic dict with angle, hook, stat, and emotional trigger.
    """
    logger.step("Starting topic research...")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    # Load research prompt
    prompt_path = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'research_prompt.txt')
    with open(prompt_path, 'r', encoding='utf-8') as f:
        system_prompt = f.read()

    # Inject today's date for context
    from datetime import datetime
    import pytz
    tz = pytz.timezone(os.getenv("TIMEZONE", "UTC"))
    today = datetime.now(tz).strftime("%A, %B %d, %Y")

    user_message = (
        f"Today is {today}. "
        "Based on what retail investors are experiencing and discussing right now, "
        "identify the best topic for Fincare to post about today. "
        "Return only the JSON object — no explanation, no markdown, just raw JSON."
    )

    client = anthropic.Anthropic(api_key=api_key)

    logger.step("Calling Claude API for topic research...")
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    raw_response = message.content[0].text.strip()

    # Strip markdown code block if Claude wraps it
    if raw_response.startswith("```"):
        raw_response = raw_response.split("```")[1]
        if raw_response.startswith("json"):
            raw_response = raw_response[4:]
        raw_response = raw_response.strip()

    topic = json.loads(raw_response)

    # Validate required keys
    required_keys = ["topic", "angle", "hook", "key_stat", "emotional_trigger", "why_today"]
    missing = [k for k in required_keys if k not in topic]
    if missing:
        raise ValueError(f"Research response missing keys: {missing}")

    logger.success(f"Topic identified: {topic['topic']}")
    return topic
