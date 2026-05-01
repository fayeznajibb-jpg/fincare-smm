"""
Fincare Quality Gate
====================
Acts as the strict final gatekeeper before any content reaches Telegram.
Scores every post against professional SMM standards and auto-improves weak ones.

Scoring dimensions (1–5 each, max 25):
  1. Hook strength     — does the first line stop the scroll in <3 seconds?
  2. Platform fit      — does it match the platform's native voice and format?
  3. CTA quality       — is the CTA saves/shares/comments focused? Specific?
  4. Freshness         — is it tied to today's specific news, not generic advice?
  5. Saves potential   — is there a genuine reason to save/screenshot/reshare this?

Auto-rewrites any post scoring < 3 on any dimension.
Sends Telegram approval summary with what was improved.
Non-blocking — failures return original posts.
"""

import os
import json
import anthropic
from utils.logger import SecureLogger

logger = SecureLogger("quality_gate")

SCORE_THRESHOLD = 4   # rewrite if any dimension falls below this (4 = "good", was 3 = "decent")
AUTO_REWRITE    = True # set False to only score, no rewrite
MAX_REWRITE_ATTEMPTS = 3  # max rewrites per post before accepting best version


SCORING_GUIDE = """
You are The Curator — the quality control and evolution agent for Fincare's social media content.
Persona: A sophisticated, slightly picky art critic with a memory like an elephant.
Mantra: "Excellence is the only standard; evolution is the only path."

You are the Burn Down agent. Your job is not to pass content — it is to enforce excellence.
If something is 2% off, you send it back. No exceptions.

Score each dimension 1–5. Be harsh — "good enough" is not acceptable. Only give 5 to genuinely excellent work.

HOOK STRENGTH (1–5)
5 = First line is a scroll-stopper. Creates instant curiosity, emotion, or surprise. Under 12 words. Specific fact or bold claim.
4 = Good hook. Engaging but could be sharper or more specific.
3 = Decent. Opens well but not scroll-stopping.
2 = Weak. Generic opener. "Investing is hard" / "The market is volatile" level.
1 = No hook. Starts with brand name, "We", or a boring statement.

PLATFORM FIT (1–5)
5 = Could only exist on this platform. Feels completely native. Correct format, length, tone.
4 = Good fit. Very minor awkwardness.
3 = Works but could be more native — wrong length or slightly off-tone.
2 = Feels like it was written for a different platform and adapted poorly.
1 = Copy-paste from another platform with zero adaptation.

CTA QUALITY (1–5)
5 = Specific action tied to the content. "Save this for the next time markets move." / "Drop ANGEL below." / "Comment your worst sell decision."
4 = Good CTA. Slightly generic but still clear and specific.
3 = Has a CTA but vague. "Follow us for more" or "Share this."
2 = Weak CTA. "Visit our website" or "Check our bio."
1 = No CTA, or just "Like this post."

FRESHNESS (1–5)
5 = Tied directly to a specific event happening TODAY. Could not have been posted last week.
4 = References current context but could work any recent week.
3 = Has some currency but mostly evergreen — misses the specific news moment.
2 = Could have been posted any time in the last year.
1 = Completely generic. No current context whatsoever.

SAVES POTENTIAL (1–5)
5 = Reader will screenshot/save/share immediately. Contains a framework, stat, checklist, or actionable system.
4 = Valuable enough to save. Has a clear takeaway worth keeping.
3 = Some save value. Reader might save but probably won't.
2 = Low save value. Mostly emotional reaction, no actionable insight.
1 = No reason to save. Pure scroll-past content.

PATTERN INTERRUPT CHECK (1–5) — The Architect standard:
5 = First 1-2 lines are a genuine Pattern Interrupt. No finance content starts this way. Scroll-stopping.
4 = Strong hook, slightly expected but well executed.
3 = Decent hook but could come from any finance account. Not distinctly Fincare.
2 = Weak hook. Sounds like a financial newsletter opener. "Markets are volatile today..."
1 = No hook. Starts with a statement, a brand mention, or a generic fact.

JARGON-FREE CHECK (1–5) — The Zen Guru standard:
5 = Zero unexplained jargon. Every financial term is immediately translated into plain human language.
4 = Mostly jargon-free. One term left unexplained but not critical.
3 = Some jargon present that a retail investor might not understand.
2 = Multiple unexplained terms. Feels like a financial report, not a friend.
1 = Full jargon. Unreadable to anyone without a finance background.

FINVYON VOICE ALIGNMENT (1–5) — The Curator standard:
5 = Reads exactly as FINVYON would speak: calm, wise, slightly amused by human behavior, Old Money patience.
4 = Close to FINVYON's voice. Minor drift toward generic brand tone.
3 = Has moments of FINVYON's voice but inconsistent throughout.
2 = Generic brand voice. Could be any fintech company.
1 = Sounds nothing like FINVYON. Corporate, salesy, or preachy.
"""


PLATFORM_REWRITE_RULES = {
    "linkedin_company": """
LinkedIn Company Post (HARD LIMIT: 700 characters):
- Professional but human — write like a thoughtful person, NOT a brand
- NEVER start with "We", the company name, or "At Fincare"
- Opens with a data point, question, or relatable scenario — NOT corporate speak
- Structure: 1 line hook → insight → data point → Fincare angle → question CTA
- End with a specific question that invites replies from professionals
- Must include brief "Not financial advice" or "This is not financial advice"
- 3-5 relevant hashtags maximum — NO keyword stuffing
- COUNT every character — you MUST stay under 700""",

    "instagram_caption": """
Instagram Caption (HARD LIMIT: 2200 characters):
- First 125 characters = the hook (visible before "more" cutoff) — make every word count
- Visual storytelling — paint a specific scene the reader can see
- Carousel-friendly structure: content should map to 7-10 slides
- End with: "Save this 👆" + "Drop [SPECIFIC WORD] in comments"
- 3-5 hashtags in a block at the END — NEVER inline, NEVER more than 5
- The hook must work as a standalone statement before the cutoff
- Use line breaks for readability — no walls of text""",

    "tiktok_caption": """
TikTok Caption (HARD LIMIT: 2200 characters, optimal: 150-300 chars):
- STARTS with "POV:" or a strong punchy statement — hook hits in FIRST 5 WORDS
- Conversational, urgent, authentic — NOT polished marketing
- Sound like a real person sharing something that shocked them
- "Comment your [experience/reaction]" CTA that invites participation
- 3-5 hashtags only — highly relevant to current moment
- Must make the viewer want to comment OR share with someone specific
- Include a [SCRIPT NOTE: ...] at the end for video creation""",

    "threads_post": """
Threads Post (HARD LIMIT: 500 characters — COUNT THIS CAREFULLY):
- Raw, conversational, no hashtags needed (max 1-2 if truly relevant)
- Choose ONE format: hot take / micro-story / data drop / genuine question
- Ends with a question OR a bold statement that demands a response
- Must feel like something a real, smart person posts — NOT a brand account
- ABSOLUTELY NO corporate language, no "excited to share", no "reach out"
- RECOUNT characters — if over 500, cut ruthlessly until you're under""",
}


def score_post(post_text: str, platform: str, topic: dict) -> dict:
    """Score a single post. Returns dict with scores and reasoning."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {}

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""You are a strict quality control manager for Fincare's social media.
Your job is to enforce high standards — only excellent content gets through.

{SCORING_GUIDE}

Platform: {platform}
Topic: {topic.get('topic', '')}
Today's news context: {topic.get('why_today', '')}
Emotional trigger: {topic.get('emotional_trigger', '')}

POST TO SCORE:
---
{post_text[:1500]}
---

Be strict. A score of 3+ means it passed that dimension. Below 3 means it needs work.
Check character limits too:
- linkedin_company: 700 chars max
- threads_post: 500 chars max

Return ONLY raw JSON:
{{
  "hook_strength": <1-5>,
  "platform_fit": <1-5>,
  "cta_quality": <1-5>,
  "freshness": <1-5>,
  "saves_potential": <1-5>,
  "pattern_interrupt": <1-5>,
  "jargon_free": <1-5>,
  "finvyon_voice": <1-5>,
  "total": <sum of all 8 scores>,
  "weakest_dimension": "<name of lowest scoring dimension>",
  "specific_improvement": "<one concrete actionable sentence: exactly what to change>",
  "char_count": <actual character count of the post>,
  "over_limit": <true if over platform limit, false otherwise>
}}"""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.warning(f"Scoring failed for {platform}: {type(e).__name__}")
        return {}


def rewrite_weak_post(post_text: str, platform: str, topic: dict, score: dict, posts: dict) -> str:
    """Rewrites a post that scored below threshold. Returns improved version."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return post_text

    client = anthropic.Anthropic(api_key=api_key)

    rules = PLATFORM_REWRITE_RULES.get(platform, "")
    weak  = score.get("weakest_dimension", "unknown")
    fix   = score.get("specific_improvement", "improve overall quality")
    over_limit = score.get("over_limit", False)
    char_count = score.get("char_count", 0)

    # Load brand voice for context
    voice_path = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'brand_voice.txt')
    try:
        with open(voice_path, 'r', encoding='utf-8') as f:
            brand_voice = f.read()
    except Exception:
        brand_voice = "Fincare: behavioural finance AI. Calm. Data-backed. Human."

    limit_warning = ""
    if over_limit and platform == "threads_post":
        limit_warning = f"\n⚠️ CRITICAL: Current post is {char_count} chars. Threads hard limit is 500. CUT to under 500 chars."
    elif over_limit and platform == "linkedin_company":
        limit_warning = f"\n⚠️ CRITICAL: Current post is {char_count} chars. LinkedIn hard limit is 700. CUT to under 700 chars."

    prompt = f"""You are the senior social media copywriter for Fincare.
Your job is to rewrite this post to excellent standards — not "good enough", but genuinely excellent.

{brand_voice}

PLATFORM RULES (follow every single one):
{rules}
{limit_warning}

TODAY'S TOPIC: {topic.get('topic', '')}
TODAY'S HOOK: {topic.get('hook', '')}
TODAY'S KEY STAT: {topic.get('key_stat', '')}
TODAY'S NEWS CONTEXT: {topic.get('why_today', '')}
EMOTIONAL TRIGGER: {topic.get('emotional_trigger', '')}

CURRENT POST (failed on: {weak}):
---
{post_text[:1500]}
---

WHAT NEEDS TO CHANGE: {fix}

REWRITE REQUIREMENTS:
1. Fix "{weak}" first — this is what failed
2. Hook must hit within first 3 words / 5 seconds
3. CTA must be saves or specific engagement — never "visit our website"
4. Must reference today's specific news context — not generic advice
5. Stay strictly within character limits
6. Keep what's already working — only improve what's weak
7. Sound like a real expert, not a brand account

Return ONLY the rewritten post text. No explanation. No JSON. No preamble."""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        improved = resp.content[0].text.strip()
        logger.success(f"{platform}: rewritten (fixed: {weak})")
        return improved
    except Exception as e:
        logger.warning(f"Rewrite failed for {platform}: {type(e).__name__}")
        return post_text


def run(topic: dict, posts: dict) -> tuple[dict, str]:
    """
    Main quality gate. Scores all posts and auto-rewrites weak ones.
    Returns (improved_posts, approval_summary).
    Non-blocking — always returns something.
    """
    if not AUTO_REWRITE:
        logger.info("Quality gate: scoring only (AUTO_REWRITE=False).")

    PLATFORM_KEY_MAP = {
        "linkedin_company":  "linkedin_company",
        "instagram_caption": "instagram_caption",
        "tiktok_caption":    "tiktok_caption",
        "threads_post":      "threads_post",
    }

    improved_posts = dict(posts)
    total_rewrites = 0
    score_log      = {}
    improvements   = []  # for Telegram summary

    for post_key, platform in PLATFORM_KEY_MAP.items():
        text = posts.get(post_key, "")
        if not text:
            continue

        score = score_post(text, platform, topic)
        if not score:
            continue

        total    = score.get("total", 25)
        weakest  = score.get("weakest_dimension", "—")
        weak_val = min(
            score.get("hook_strength", 5),
            score.get("platform_fit", 5),
            score.get("cta_quality", 5),
            score.get("freshness", 5),
            score.get("saves_potential", 5),
            score.get("pattern_interrupt", 5),
            score.get("jargon_free", 5),
            score.get("finvyon_voice", 5),
        )
        over_limit = score.get("over_limit", False)
        score_log[post_key] = {
            "total":      total,
            "weakest":    weakest,
            "weak_score": weak_val,
            "over_limit": over_limit,
        }

        logger.info(
            f"{post_key}: {total}/25 | weakest={weakest}({weak_val}) | "
            f"hook={score.get('hook_strength')} fit={score.get('platform_fit')} "
            f"cta={score.get('cta_quality')} fresh={score.get('freshness')} "
            f"saves={score.get('saves_potential')}"
            + (" ⚠️ OVER LIMIT" if over_limit else "")
        )

        needs_rewrite = (AUTO_REWRITE and weak_val < SCORE_THRESHOLD) or over_limit

        if needs_rewrite:
            reason = f"score {weak_val}/5 on {weakest}" if weak_val < SCORE_THRESHOLD else "over character limit"
            logger.step(f"{post_key}: {reason} — rewriting...")

            best_text  = text
            best_score = score

            for attempt in range(MAX_REWRITE_ATTEMPTS):
                improved = rewrite_weak_post(best_text, platform, topic, best_score, posts)
                new_score = score_post(improved, platform, topic)

                if new_score:
                    new_weak = min(
                        new_score.get("hook_strength", 5),
                        new_score.get("platform_fit", 5),
                        new_score.get("cta_quality", 5),
                        new_score.get("freshness", 5),
                        new_score.get("saves_potential", 5),
                        new_score.get("pattern_interrupt", 5),
                        new_score.get("jargon_free", 5),
                        new_score.get("finvyon_voice", 5),
                    )
                    new_total = new_score.get("total", 0)
                    new_over  = new_score.get("over_limit", False)

                    logger.info(f"  Attempt {attempt+1}: {new_total}/25 (min={new_weak})")

                    if new_weak >= SCORE_THRESHOLD and not new_over:
                        best_text  = improved
                        best_score = new_score
                        break  # passed — stop rewriting
                    elif new_total > best_score.get("total", 0):
                        best_text  = improved  # better but still not perfect
                        best_score = new_score
                else:
                    best_text = improved
                    break

            improved_posts[post_key] = best_text
            total_rewrites += 1

            fix_desc = score.get("specific_improvement", "")[:80]
            improvements.append(
                f"• <b>{post_key}</b>: fixed {weakest} "
                f"({weak_val}→{best_score.get('total', '?')}/25)"
                + (f" — {fix_desc}" if fix_desc else "")
            )

    # Build Telegram approval summary
    if total_rewrites > 0:
        logger.success(f"Quality gate: {total_rewrites} post(s) improved before briefing.")
        summary = (
            f"🔍 <b>Quality Gate</b> — {total_rewrites} post(s) improved:\n"
            + "\n".join(improvements)
        )
    else:
        logger.success("Quality gate: all posts passed — no rewrites needed.")
        summary = "✅ <b>Quality Gate</b> — all posts passed with no rewrites needed."

    return improved_posts, summary


# ─── Video Timing Check ────────────────────────────────────────────────────────

def check_video_timing(vo_lines: list, audio_path: str) -> dict:
    """
    Checks if the voiceover audio duration is close to the 60s video target.
    Uses ffprobe to get exact audio duration.
    Returns dict: { audio_duration_s, video_duration_s, gap_s, status, message }
    """
    import subprocess
    VIDEO_DURATION = 60.0  # FINVYON 10-scene = 60s fixed

    result = {
        "audio_duration_s": None,
        "video_duration_s": VIDEO_DURATION,
        "gap_s":            None,
        "status":           "unknown",
        "message":          "",
    }

    if not audio_path or not os.path.exists(audio_path):
        result["status"]  = "skipped"
        result["message"] = "No audio file to check."
        return result

    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        out = subprocess.check_output(cmd, timeout=10).decode().strip()
        audio_dur = float(out)
        gap = VIDEO_DURATION - audio_dur
        result["audio_duration_s"] = round(audio_dur, 1)
        result["gap_s"]            = round(gap, 1)

        if audio_dur > VIDEO_DURATION + 3:
            # Speech overruns the video — last words will be cut off by mixer
            result["status"]  = "fail"
            result["message"] = f"❌ Speech: {audio_dur:.1f}s / {VIDEO_DURATION:.0f}s — {audio_dur - VIDEO_DURATION:.1f}s TOO LONG (words will be cut)"
        elif gap <= 5:
            result["status"]  = "pass"
            result["message"] = f"🎙 Speech: {audio_dur:.1f}s / {VIDEO_DURATION:.0f}s ✅"
        elif gap <= 12:
            result["status"]  = "warn"
            result["message"] = f"⚠️ Speech: {audio_dur:.1f}s / {VIDEO_DURATION:.0f}s — {gap:.1f}s gap (slight silence at end)"
        else:
            result["status"]  = "fail"
            result["message"] = f"❌ Speech: {audio_dur:.1f}s / {VIDEO_DURATION:.0f}s — {gap:.1f}s gap (too short)"

        logger.info(f"Video timing: {result['message']}")
    except Exception as e:
        result["status"]  = "error"
        result["message"] = f"Timing check error: {e}"
        logger.warning(f"Timing check failed: {e}")

    return result
