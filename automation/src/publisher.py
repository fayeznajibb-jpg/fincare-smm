import os
import requests
from utils.logger import SecureLogger
from utils.validators import is_safe_url

logger = SecureLogger("publisher")

LINKEDIN_API  = "https://api.linkedin.com/v2"
TIKTOK_API    = "https://open.tiktokapis.com/v2"
THREADS_API   = "https://graph.threads.net/v1.0"


# ─────────────────────────────────────────
# LINKEDIN
# ─────────────────────────────────────────

def _linkedin_upload_image(token: str, author_urn: str, image_path: str) -> str | None:
    """
    Uploads an image to LinkedIn and returns the asset URN.
    Two-step: register upload → PUT binary.
    Returns asset URN string or None on failure.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0"
    }

    # Step 1: Register upload
    register_payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": author_urn,
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent"
            }]
        }
    }

    try:
        reg_resp = requests.post(
            f"{LINKEDIN_API}/assets?action=registerUpload",
            headers=headers,
            json=register_payload,
            timeout=15
        )
        if reg_resp.status_code != 200:
            logger.error(f"LinkedIn image register failed. Status: {reg_resp.status_code}")
            return None

        reg_data = reg_resp.json()
        upload_url = reg_data["value"]["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
        asset_urn  = reg_data["value"]["asset"]

        # Step 2: Upload binary
        with open(image_path, "rb") as img_file:
            upload_resp = requests.put(
                upload_url,
                data=img_file,
                headers={"Authorization": f"Bearer {token}"},
                timeout=60
            )

        if upload_resp.status_code in (200, 201):
            logger.success(f"LinkedIn image uploaded: {asset_urn}")
            return asset_urn
        else:
            logger.error(f"LinkedIn image upload failed. Status: {upload_resp.status_code}")
            return None

    except Exception as e:
        logger.error(f"LinkedIn image upload error: {type(e).__name__}")
        return None


def post_linkedin_personal(content: str, hashtags: str, image_path: str = None) -> bool:
    """Posts to LinkedIn personal profile, optionally with an image."""
    token     = os.getenv("LINKEDIN_ACCESS_TOKEN")
    person_id = os.getenv("LINKEDIN_PERSON_ID")

    if not token or not person_id:
        logger.warning("LinkedIn personal credentials missing — skipping.")
        return False

    full_text  = f"{content}\n\n{hashtags}".strip()
    author_urn = f"urn:li:person:{person_id}"

    asset_urn = _linkedin_upload_image(token, author_urn, image_path) if image_path else None

    if asset_urn:
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": full_text},
                    "shareMediaCategory": "IMAGE",
                    "media": [{
                        "status": "READY",
                        "media": asset_urn,
                        "title": {"text": ""}
                    }]
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
        }
    else:
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": full_text},
                    "shareMediaCategory": "NONE"
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
        }

    return _linkedin_post(token, payload, "personal profile")


def post_linkedin_company(content: str, hashtags: str, image_path: str = None) -> bool:
    """Posts to LinkedIn company page, optionally with an image."""
    token   = os.getenv("LINKEDIN_ACCESS_TOKEN")
    org_id  = os.getenv("LINKEDIN_ORGANIZATION_ID")

    if not token or not org_id:
        logger.warning("LinkedIn company credentials missing — skipping.")
        return False

    full_text  = f"{content}\n\n{hashtags}".strip()
    author_urn = f"urn:li:organization:{org_id}"

    asset_urn = _linkedin_upload_image(token, author_urn, image_path) if image_path else None

    if asset_urn:
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": full_text},
                    "shareMediaCategory": "IMAGE",
                    "media": [{
                        "status": "READY",
                        "media": asset_urn,
                        "title": {"text": ""}
                    }]
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
        }
    else:
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": full_text},
                    "shareMediaCategory": "NONE"
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
        }

    return _linkedin_post(token, payload, "company page")  # returns URN or None


def _linkedin_post(token: str, payload: dict, label: str) -> str | None:
    """
    Shared LinkedIn posting logic. Returns the post URN on success, None on failure.
    URN is captured from the x-restli-id response header for performance tracking.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0"
    }

    try:
        resp = requests.post(
            f"{LINKEDIN_API}/ugcPosts",
            headers=headers,
            json=payload,
            timeout=15
        )

        if resp.status_code in (200, 201):
            post_urn = resp.headers.get("x-restli-id", "")
            logger.success(f"LinkedIn {label} posted successfully. URN: {post_urn[:40]}")
            return post_urn or "posted"
        else:
            logger.error(f"LinkedIn {label} failed. Status: {resp.status_code}")
            return None

    except requests.RequestException as e:
        logger.error(f"LinkedIn {label} request error: {type(e).__name__}")
        return None


# ─────────────────────────────────────────
# THREADS
# ─────────────────────────────────────────

def post_threads(content: str) -> bool:
    """
    Posts a text post to Threads.
    Two-step process: create container → publish.
    """
    token   = os.getenv("THREADS_ACCESS_TOKEN")
    user_id = os.getenv("THREADS_USER_ID")

    if not token or not user_id:
        logger.warning("Threads credentials missing — skipping.")
        return False

    try:
        # Step 1: Create media container
        container_resp = requests.post(
            f"{THREADS_API}/{user_id}/threads",
            params={
                "media_type": "TEXT",
                "text": content,
                "access_token": token
            },
            timeout=15
        )

        if container_resp.status_code != 200:
            logger.error(f"Threads container creation failed. Status: {container_resp.status_code}")
            return False

        container_data = container_resp.json()
        container_id = container_data.get("id")

        if not container_id:
            logger.error("Threads container ID not returned.")
            return False

        # Brief pause before publishing (Meta recommendation)
        import time
        time.sleep(3)

        # Step 2: Publish the container
        publish_resp = requests.post(
            f"{THREADS_API}/{user_id}/threads_publish",
            params={
                "creation_id": container_id,
                "access_token": token
            },
            timeout=15
        )

        if publish_resp.status_code == 200:
            logger.success("Threads post published successfully.")
            return True
        else:
            logger.error(f"Threads publish failed. Status: {publish_resp.status_code}")
            return False

    except requests.RequestException as e:
        logger.error(f"Threads request error: {type(e).__name__}")
        return False


# ─────────────────────────────────────────
# TIKTOK
# ─────────────────────────────────────────

def post_tiktok_text(content: str) -> bool:
    """
    TikTok does not support text-only posts via API.
    This function logs a reminder to post manually until
    Remotion video generation is integrated in Phase 2.
    """
    logger.warning(
        "TikTok requires video content via API. "
        "Text-only posting is not supported. "
        "Please post manually until Remotion integration is complete (Phase 2)."
    )

    # Save TikTok content to drafts for manual posting
    _save_tiktok_draft(content)
    return False


def _save_tiktok_draft(content: str):
    """Saves TikTok content to drafts folder for manual review."""
    import json
    from datetime import datetime

    os.makedirs("drafts", exist_ok=True)
    filename = f"drafts/tiktok_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"platform": "tiktok", "content": content}, f, indent=2)

    logger.info(f"TikTok draft saved to {filename} for manual posting.")


# ─────────────────────────────────────────
# PUBLISH ALL
# ─────────────────────────────────────────

def publish_all(posts: dict, image_path: str = None) -> dict:
    """
    Publishes approved posts to all platforms.
    Optionally attaches an image to LinkedIn posts.
    Returns a results dict showing success/failure per platform.
    """
    logger.step("Starting publishing to all platforms...")
    if image_path:
        logger.info(f"Image attached: {image_path}")

    results = {
        "linkedin_company":     None,   # URN string on success, None on failure
        "linkedin_company_urn": None,   # captured separately for performance tracking
        "threads":              False,
        "tiktok":               False,
    }

    # LinkedIn Company page only — no personal profile posting
    urn = post_linkedin_company(
        posts.get("linkedin_company", ""),
        posts.get("hashtags_linkedin", ""),
        image_path
    )
    results["linkedin_company"]     = bool(urn)
    results["linkedin_company_urn"] = urn

    # Threads
    threads_content = (
        posts.get("threads_post", "") + "\n\n" +
        posts.get("hashtags_tiktok", "")
    ).strip()
    results["threads"] = post_threads(threads_content)

    # TikTok (manual for now — Phase 2 will automate with Remotion)
    results["tiktok"] = post_tiktok_text(posts.get("tiktok_caption", ""))

    # Summary
    succeeded = [k for k, v in results.items() if v]
    failed    = [k for k, v in results.items() if not v]

    logger.success(f"Published to: {', '.join(succeeded) if succeeded else 'none'}")
    if failed:
        logger.warning(f"Failed or skipped: {', '.join(failed)}")

    return results
