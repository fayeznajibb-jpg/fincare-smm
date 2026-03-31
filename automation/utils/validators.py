from typing import Optional


# Platform character limits — single source of truth
LIMITS = {
    "linkedin_personal": 3000,
    "linkedin_company":  700,
    "instagram_caption": 2200,
    "tiktok_caption":    2200,
    "threads_post":      500,
}


def validate_post_lengths(posts: dict) -> tuple[bool, list[str]]:
    """
    Validates that all generated posts are within platform character limits.
    Returns (is_valid, list_of_errors).
    """
    errors = []

    checks = {
        "linkedin_personal": posts.get("linkedin_personal", ""),
        "linkedin_company":  posts.get("linkedin_company", ""),
        "instagram_caption": posts.get("instagram_caption", ""),
        "tiktok_caption":    posts.get("tiktok_caption", ""),
        "threads_post":      posts.get("threads_post", ""),
    }

    for platform, content in checks.items():
        limit = LIMITS[platform]
        length = len(content)
        if length > limit:
            errors.append(
                f"{platform}: {length} chars — exceeds limit of {limit} "
                f"(over by {length - limit})"
            )

    return len(errors) == 0, errors


def validate_env_vars(required_vars: list[str]) -> tuple[bool, list[str]]:
    """
    Checks that all required environment variables are set and non-empty.
    Returns (is_valid, list_of_missing_vars).
    """
    import os
    missing = [var for var in required_vars if not os.getenv(var)]
    return len(missing) == 0, missing


def validate_api_response(response: dict, required_keys: list[str]) -> bool:
    """Validates that an API response contains all expected keys."""
    if not isinstance(response, dict):
        return False
    return all(key in response for key in required_keys)


def sanitize_content(text: str) -> str:
    """
    Sanitizes post content before publishing.
    - Removes null bytes
    - Strips leading/trailing whitespace
    - Limits consecutive newlines to 2
    - Removes any non-printable characters
    """
    import re

    if not text:
        return ""

    # Remove null bytes
    text = text.replace('\x00', '')

    # Remove non-printable characters (except standard whitespace)
    text = re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x80-\xFF]', '', text)

    # Collapse 3+ consecutive newlines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Strip leading/trailing whitespace
    text = text.strip()

    return text


def is_safe_url(url: str) -> bool:
    """Basic URL safety check — ensures URL starts with https."""
    return isinstance(url, str) and url.startswith('https://')


def validate_telegram_chat_id(incoming_id: str, expected_id: str) -> bool:
    """
    Verifies that a Telegram message comes from the authorised chat ID.
    Prevents unauthorized users from approving posts.
    """
    return str(incoming_id).strip() == str(expected_id).strip()
