"""
Fincare Video Agent
=====================
A skills-based agent that generates, renders, and delivers Remotion videos
for manual review and posting via Telegram.

Skills:
  skill_select_template          → maps pillar to visual style
  skill_generate_props           → Claude Haiku writes 3 hook variants, picks best
  skill_render                   → npx remotion render (9:16 + 1:1)
  skill_extract_thumbnail        → ffmpeg frame at 3s → cover.jpg
  skill_send_preview             → sends thumbnail + video + action buttons to Telegram
  skill_copy_script              → builds copy-paste manual posting guide
  skill_clone_voice              → ONE-TIME: extracts voice reference WAV (no API needed)
  skill_generate_voiceover_script → Claude Haiku writes 60s timed spoken script
  skill_synthesise_voice         → Coqui XTTS v2 (local, free) → WAV in cloned voice
  skill_mix_audio                → ffmpeg: voiceover (100%) + beat (20%) + SFX

Entry point: run(topic, posts, viral_intel) → dict

Runs locally only (fincare-video/ is not in the repo).
All skills are non-fatal — errors are caught and logged.
"""

import os
import json
import subprocess
from datetime import datetime
from utils.llm import call_llm
from utils.logger import SecureLogger

logger = SecureLogger("video_agent")

# ─── Paths ────────────────────────────────────────────────────────────────────

REMOTION_PROJECT = os.path.expanduser("~/Downloads/fincare-video")
OUTPUT_DIR  = os.path.join(REMOTION_PROJECT, "out")
DRAFTS_DIR  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "drafts"))

# ─── Template map ─────────────────────────────────────────────────────────────

PILLAR_TO_TEMPLATE = {
    # New pillars
    "FUNDAMENTALS": "insight",
    "NEWS":         "data",
    "COMPANY":      "data",
    "PORTFOLIO":    "insight",
    "MINDSET":      "emotional",
    # Legacy fallbacks
    "STORY":    "emotional",
    "DATA":     "data",
    "OPINION":  "opinion",
    "QUESTION": "question",
    "INSIGHT":  "insight",
}

PILLAR_TO_TRIGGER = {
    # New pillars
    "FUNDAMENTALS": "curiosity",
    "NEWS":         "anxiety",
    "COMPANY":      "curiosity",
    "PORTFOLIO":    "anxiety",
    "MINDSET":      "fear",
    # Legacy fallbacks
    "STORY":    "anxiety",
    "DATA":     "fear",
    "OPINION":  "overconfidence",
    "QUESTION": "FOMO",
    "INSIGHT":  "anxiety",
}

COMPOSITION_IDS = {
    # Format B — AI Reveal (68.5s) — hero format (always available)
    "916":            "FinCare-916",
    "11":             "FinCare-11",
    "169":            "FinCare-169",
    # Format A — Daily Brief (~42s) — falls back to FinCare-916 until dedicated composition built
    "daily_916":      "FinCare-916",
    "daily_11":       "FinCare-11",
    # Format E — News Reaction (~20s) — falls back to FinCare-916
    "news_916":       "FinCare-916",
    # Slideshow — cinematic AI image-driven, 45s
    "slideshow_916":  "FinCare-Slideshow-916",
    "slideshow_11":   "FinCare-Slideshow-11",
    # FINVYON Explainer — illustrated character, 4 scenes, 24s
    "finvyon_916":    "FinCare-Finvyon-Story",
    # FINVYON 10-scene — illustrated character, 10 scenes, 60s (WORKFLOW.md)
    "finvyon_10":          "FinCare-Finvyon-Story-10",
    # Carousel Video Story — carousel slides animated, dynamic duration
    "carousel_video_916":  "FinCare-Carousel-Video-916",
}

# Format → default render keys
FORMAT_RENDER_KEYS = {
    "A":         ["daily_916", "daily_11"],
    "B":         ["916", "11"],
    "D":         ["finvyon_10"],    # Explainer → FINVYON 10-scene 60s (WORKFLOW.md)
    "E":         ["news_916"],
    "SLIDESHOW": ["slideshow_916"],
    "FINVYON":   ["finvyon_10"],
}

# Content pillar → auto video format
PILLAR_TO_FORMAT = {
    # New pillars — all FINVYON 10-scene explainer (Format D)
    "FUNDAMENTALS": "D",   # Mon — CFA concept lesson
    "NEWS":         "D",   # Tue — FINVYON news reaction
    "COMPANY":      "D",   # Wed — Company metric teardown
    "PORTFOLIO":    "D",   # Thu — Portfolio construction lesson
    "MINDSET":      "D",   # Fri — Behavioural finance / mindset
    # Legacy fallbacks — all redirect to Format D
    "STORY":    "D",
    "DATA":     "D",
    "OPINION":  "D",
    "QUESTION": "D",
    "INSIGHT":  "D",
}

def _compute_scene_durations(vo_lines: list, fps: int = 30) -> list:
    """
    Distribute video frames proportionally across 10 scenes by voiceover word count.
    Scene 0 (intro chat UI) and scene 9 (CTA logo zoom) get fixed durations.
    Scenes 1–8 split remaining frames proportionally by word count.
    """
    intro_frames = 150    # 5s fixed — logo + hook typing
    cta_frames   = 150    # 5s fixed — logo zoom
    min_slide    = 120    # 4s minimum per slide

    # Pad / trim to exactly 10 lines
    lines = list(vo_lines)
    while len(lines) < 10:
        lines.append("")
    lines = lines[:10]

    slide_words = [max(1, len(l.split())) for l in lines[1:9]]  # 8 middle lines
    total_words = sum(slide_words)
    target      = 1500   # ~50s for 8 slides at 30fps

    slide_frames = [max(min_slide, round(w / total_words * target)) for w in slide_words]
    diff = target - sum(slide_frames)
    slide_frames[-1] = max(min_slide, slide_frames[-1] + diff)

    return [intro_frames] + slide_frames + [cta_frames]


def _copy_slides_to_public(slide_paths: list, ts: str) -> list:
    """
    Copies rendered carousel JPGs into public/carousel_temp_{ts}/ so Remotion can load them.
    Returns list of relative paths (relative to public/).
    """
    import shutil as _shutil
    temp_dir = os.path.join(REMOTION_PROJECT, "public", f"carousel_temp_{ts}")
    os.makedirs(temp_dir, exist_ok=True)
    rel_paths = []
    for i, src in enumerate(slide_paths):
        if not os.path.exists(src):
            logger.warning(f"Slide {i+1} not found at {src} — skipping.")
            continue
        dst = os.path.join(temp_dir, f"slide_{i+1:02d}.jpg")
        _shutil.copy2(src, dst)
        rel_paths.append(f"carousel_temp_{ts}/slide_{i+1:02d}.jpg")
    return rel_paths


BRAND_RULE = """
BRAND RULES (apply to every video):
- FINVYON is a CFA charterholder and investment advisor — CFA-level knowledge in plain English
- Teach real finance: P/E ratios, balance sheets, portfolio theory, real companies, real numbers
- Psychology is a layer on top of CFA content — not the foundation (except MINDSET pillar)
- Scene 6 is ALWAYS the Fincare Moment: FINVYON pauses, Fincare chat types the question
- Scene 10 is ALWAYS the golden key CTA: one of the 4 rotating AIFinCare CTAs
- Never give financial advice (no buy/sell/hold recommendations on specific securities)
- Always apply concepts to REAL companies (Apple, Nvidia, Tesla, Saudi Aramco, Amazon, etc.)
"""

# ─── Budget Strategy ──────────────────────────────────────────────────────────
# Per-scene strategy selector (WORKFLOW.md): Kling/Pika/Luma/Static based on motion keywords.
# Budget: 10 images ($0.40) + ~2 Kling clips ($0.34) = ~$0.74 per 60s video.
SCENE_STRATEGY = {1: "kling", 2: "static", 3: "kling", 4: "static"}  # legacy 4-scene fallback
BUDGET_MAX  = 1.50   # auto-downgrade if total cost exceeds this
COST_KLING  = 0.168  # fal-ai/kling-video/v1.6 per 5s clip
COST_PIKA   = 0.36   # fal-ai/pika/v2.2 per 6s clip
COST_LUMA   = 0.24   # fal-ai/luma-dream-machine/ray-2 per 5s clip
COST_IMAGE  = 0.04   # fal-ai/flux-pro/v1.1 per image

STRATEGY_COST = {"kling": COST_KLING, "pika": COST_PIKA, "luma": COST_LUMA, "static": 0.0}

CHARACTER_BASE_FINVYON = (
    "Refined black stickman, brushed-back grey hair, thick tortoiseshell glasses, navy blue blazer, "
    "light blue open-collar shirt, khaki chinos, brown loafers. Full-body or close-up composition "
    "optimized for a 9:16 vertical frame. Minimalist flat illustration, clean black outlines, "
    "vast pure white background. No text, no labels, no numbers."
)


def _select_strategy(motion_instruction: str) -> str:
    """
    WORKFLOW.md per-scene strategy selector.
    Parses motion_instruction keywords → returns "kling" | "pika" | "luma" | "static".
    Only unambiguous human-motion verbs trigger Kling (keeps cost down).
    Priority: Kling > Pika > Luma > Static.
    """
    m = motion_instruction.lower()
    # Strict Kling list — only clear single-person motion verbs
    KLING_KEYWORDS = ["taps", "raises", "nods", "leans", "walks", "turns", "waves", "waving"]
    strat = "static"  # default
    for k in KLING_KEYWORDS:
        if k in m:
            strat = "kling"
            break
    if strat == "static":
        if any(k in m for k in ["creative", "effect", "morph", "transition", "zoom burst", "shatter", "glow"]):
            strat = "pika"
        elif any(k in m for k in ["fast", "prototype", "quick", "draft"]):
            strat = "luma"
    # Respect USE_KLING flag — downgrade to static if Kling is disabled
    if strat == "kling" and not _use_kling:
        strat = "static"
    return strat


# ─── Skill 1: Select Format ───────────────────────────────────────────────────

def skill_select_format(topic: dict) -> str:
    """
    Picks the video format based on content pillar and breaking news flag.

    Returns: "A" | "B" | "D" | "E"
      A = Daily Brief (30–45s) — data-driven market recap
      B = AI Reveal (60–68s)   — hero format, emotional narrative
      D = Explainer (50–60s)   — educational, reuses Format B template
      E = News Reaction (15–25s) — breaking news, reactive
    """
    # Breaking news is now handled by the NEWS pillar (Tuesday) in Format D (FINVYON 10-scene)
    # Format E (15s news reaction) is retired — FINVYON CFA analysis is the better response
    pillar = topic.get("content_pillar", "FUNDAMENTALS").upper()
    fmt = PILLAR_TO_FORMAT.get(pillar, "D")  # default D — all pillars produce FINVYON explainer
    logger.info(f"Format selected: {fmt} (pillar={pillar})")
    return fmt


def skill_select_template(topic: dict) -> str:
    """
    Maps content_pillar → visual template variant name.
    Returns one of: emotional | data | opinion | question | insight
    """
    pillar = topic.get("content_pillar", "FUNDAMENTALS").upper()
    template = PILLAR_TO_TEMPLATE.get(pillar, "insight")
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
    video_format: str = "B",
) -> list[dict]:
    """
    Generates N prop variants, enforces the Researcher's brief flows through,
    and uses Claude to rank variants (not a heuristic).

    For Format D (the main FINVYON Explainer): prop generation is STRICTLY
    constrained by the Researcher's brief. The stat, real companies, CFA concept,
    and teaching example cannot be re-invented by the prop generator.
    """
    pillar  = topic.get("content_pillar", "FUNDAMENTALS")
    trigger = topic.get("emotional_trigger", PILLAR_TO_TRIGGER.get(pillar, "curiosity"))

    # ── Pull every field the Researcher produced ─────────────────────────────
    topic_str         = topic.get("topic", "")
    angle_str         = topic.get("angle", "")
    stat_str          = topic.get("key_stat", "")
    hook_str          = topic.get("hook", "")
    real_companies    = topic.get("real_companies", [])
    cfa_concept       = topic.get("cfa_concept", "")
    teaching_example  = topic.get("teaching_example", "")
    the_misconception = topic.get("the_misconception", "")
    the_correct_view  = topic.get("the_correct_view", "")
    jargon_glossary   = topic.get("jargon_glossary", {})
    source_headline   = topic.get("source_headline", "")

    companies_str = ", ".join(real_companies) if real_companies else "(none specified)"
    glossary_str = (
        "\n".join(f"  - {term}: {defn}" for term, defn in jargon_glossary.items())
        if jargon_glossary else "(none)"
    )

    viral_context = ""
    if viral_intel and viral_intel.get("top_pattern"):
        best_vi = viral_intel.get("best_performer", {})
        viral_context = (
            f"\nViral intel this week (style guide, NOT content source):\n"
            f"- Top hook style: {viral_intel['top_pattern'].replace('_', ' ')}\n"
            f"- Best performer: @{best_vi.get('handle','—')} ({best_vi.get('view_count',0):,} views)\n"
        )

    # ── Format D: FINVYON Explainer ──────────────────────────────────────────
    if video_format == "D":
        prompt = f"""You are generating video props for a FINVYON 60-second CFA Explainer.
FINVYON is a CFA charterholder teaching real finance to retail investors.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE RESEARCHER'S BRIEF — USE EXACTLY, DO NOT INVENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pillar: {pillar}
Topic: {topic_str}
CFA Concept: {cfa_concept}
Key Stat (use this EXACT number): {stat_str}
Real Companies (name at least one verbatim): {companies_str}
Teaching Example: {teaching_example}
The Misconception: {the_misconception}
The Correct View: {the_correct_view}
Source Headline (NEWS pillar only): {source_headline}

Jargon Glossary — if you use the term, define it in-line the first time:
{glossary_str}

{viral_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. The "stat" field MUST contain the exact key_stat above. Do not round, reword, or replace it.
2. The "hook" field MUST name at least one real company from the list above.
3. The "insight" field MUST state the_correct_view in FINVYON's voice.
4. Any finance term used must be followed by an in-line definition in brackets on first mention.
   Example: "P/E ratio [price divided by earnings]"
5. Do NOT use: "navigate", "volatile", "dive into", "unlock", "leverage" (verb), "game-changer",
   "empower", or any FOMO/panic framing. This is education, not fear-bait.
6. Do NOT start the hook with "Your brain..." or "You're about to..." — these are tired.
7. Specificity test: hook must contain at least ONE number AND one proper noun (company/index).
8. BANNED hook openings: "Did you know", "Most people", "Here's why", "What nobody tells you",
   "This is why", "The secret to", "You won't believe", "Everyone knows"
9. The hook must work as a standalone tweet — if it only makes sense with more context, it is too weak.
10. GOLD STANDARD hook examples for Format D:
    - "Nvidia trades at 35x P/E. Intel trades at 12x. One number explains why."
    - "Apple's gross margin hit 46%. Most investors celebrate. CFA analysts ask what comes next."
    - "S&P 500 returned 26% last year. The average investor captured 15%. Beta explains the gap."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GENERATE 3 VARIANTS — EACH A DIFFERENT HOOK ANGLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Variant 1: "The number that changes the view" — lead with the stat and the company.
  Example shape: "[Company]'s [metric] is [number]. Here's what that actually means."
Variant 2: "The misconception flip" — name what most investors think, then correct it.
  Example shape: "Most investors think [misconception]. The [cfa_concept] says something different."
Variant 3: "The analyst's one-liner" — the observation a CFA would drop in a meeting.
  Example shape: "[Specific observation using the stat and company] — and that changes how you value it."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AFTER GENERATING, RANK THEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Score each variant 1-10 on:
- Specificity (real number + real company present?)
- Educational payoff (would the viewer learn something concrete?)
- CFA-rigor (would a charterholder nod or cringe?)
Put the HIGHEST-SCORING variant first in the output array.

Return ONLY a raw JSON array of 3 objects in ranked order (best first):
[{{
  "hook": "under 15 words. Contains a number AND a company name.",
  "stat": "{stat_str}",
  "topic": "under 8 words — the concept being taught",
  "insight": "the correct_view in one sentence, FINVYON's voice, max 20 words",
  "cfa_concept": "{cfa_concept}",
  "real_companies": {json.dumps(real_companies)},
  "the_misconception": "{the_misconception}",
  "the_correct_view": "{the_correct_view}",
  "jargon_glossary": {json.dumps(jargon_glossary)},
  "ctaText": "Save this.",
  "pillar": "{pillar}",
  "format": "D",
  "rank_score": 8.5,
  "rank_reasoning": "one sentence on why this variant ranked where it did"
}}]"""

    # ── Format A: Daily Brief ────────────────────────────────────────────────
    elif video_format == "A":
        prompt = f"""You write content for a Fincare "Daily Brief" TikTok video (30–45s).
Fast, data-driven, Bloomberg Terminal energy. For retail investors who want smart daily updates.
{BRAND_RULE}
Topic: {topic_str}
Angle: {angle_str}
Key stat: {stat_str}
Pillar: DATA | Trigger: {trigger}
{viral_context}

Write {n} DIFFERENT variations. Each must have a different stat-led hook style.

Return ONLY a raw JSON array of {n} objects:
[{{
  "hook": "starts with a number/stat, under 10 words — the daily insight",
  "stat": "single number/% (e.g. '67%' or '$1.2T')",
  "topic": "under 8 words — the market event",
  "bullets": ["fact 1 under 12 words", "fact 2 under 12 words", "fact 3 under 12 words"],
  "aiSignal": "what AIFinCare's AI flagged about this — 1 punchy sentence",
  "insight": "core behavioural insight under 18 words",
  "ctaText": "2-4 words e.g. 'Get the signal'",
  "pillar": "DATA",
  "trigger": "{trigger}",
  "format": "A"
}}]"""

    # ── Format E: News Reaction ──────────────────────────────────────────────
    elif video_format == "E":
        prompt = f"""You write content for a Fincare "News Reaction" TikTok video (15–25s).
Urgent, reactive, red/orange energy. Posted within 2–4 hours of breaking news.
{BRAND_RULE}
Breaking topic: {topic_str}
Key stat: {stat_str}
{viral_context}

Write {n} DIFFERENT variations. Each reacts to the news with urgency and the AIFinCare angle.

Return ONLY a raw JSON array of {n} objects:
[{{
  "hook": "under 8 words — urgent, grabs attention immediately",
  "headline": "the news headline in plain English, under 15 words",
  "stat": "single number/% related to this news",
  "bullets": ["what this means — 10 words", "who it affects — 10 words", "the risk/opportunity — 10 words"],
  "actionStep": "what smart money is doing right now — 1 sentence",
  "insight": "the behavioural trap to avoid — under 18 words",
  "ctaText": "2-4 words e.g. 'Stay ahead'",
  "pillar": "DATA",
  "trigger": "fear",
  "format": "E"
}}]"""

    # ── Format B: AI Reveal (default) ────────────────────────────────────────
    else:
        prompt = f"""You write content for a Fincare "AI Reveal" TikTok video (60–68s).
The hero format — high-engagement, narrative, emotional. Behavioural finance meets AI.
{BRAND_RULE}
Topic: {topic_str}
Angle: {angle_str}
Key stat: {stat_str}
Hook: {hook_str}
Pillar: {pillar} | Trigger: {trigger}
{viral_context}

Write {n} DIFFERENT VideoProps variations. Each must have a DIFFERENT hook style.
Variation 1: shock stat hook (starts with a number)
Variation 2: question hook (starts with "Why" or "How" or a question)
Variation 3: bold claim hook (provocative statement)

Return ONLY a raw JSON array of {n} objects:
[{{
  "hook": "under 10 words, scroll-stopping",
  "stat": "single number/% (e.g. '67%' or '$1.7T')",
  "topic": "under 8 words",
  "insight": "core insight under 18 words — the behavioural reframe",
  "ctaText": "2-4 words, empowering (e.g. 'Stop the spiral')",
  "pillar": "{pillar}",
  "trigger": "{trigger}",
  "format": "B"
}}]"""

    # Format D has large embedded JSON (jargon_glossary, real_companies, rank_reasoning ×3)
    # — needs significantly more tokens than the other formats.
    max_tokens = 3000 if video_format == "D" else 1200

    raw = call_llm("", prompt, tier="haiku", max_tokens=max_tokens).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    raw = raw.strip()

    # Robust JSON extraction: if raw is truncated mid-array, try to recover the
    # partial objects we did receive before the truncation point.
    try:
        variants = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt to extract complete JSON objects from a truncated array.
        import re as _re
        # Find all complete {...} blocks inside the array
        objects = _re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}', raw, _re.DOTALL)
        recovered = []
        for obj_str in objects:
            try:
                recovered.append(json.loads(obj_str))
            except json.JSONDecodeError:
                pass
        if recovered:
            logger.warning(f"JSON truncated — recovered {len(recovered)} of {n} variants via regex.")
            variants = recovered
        else:
            # Last resort: ask Haiku to repair
            logger.warning("JSON repair fallback: re-requesting with simplified Format D prompt.")
            simple_prompt = f"""Return ONLY a valid JSON array with 1 object for a FINVYON finance video.
Topic: {topic_str}
Stat: {stat_str}
CFA Concept: {cfa_concept}

[{{
  "hook": "one punchy sentence with a number and company name, under 15 words",
  "stat": "{stat_str}",
  "topic": "under 8 words",
  "insight": "the correct view, FINVYON voice, max 20 words",
  "cfa_concept": "{cfa_concept}",
  "real_companies": {json.dumps(real_companies[:2] if real_companies else [])},
  "the_misconception": "{the_misconception}",
  "the_correct_view": "{the_correct_view}",
  "jargon_glossary": {{}},
  "ctaText": "Save this.",
  "pillar": "{pillar}",
  "format": "D",
  "rank_score": 8.0,
  "rank_reasoning": "fallback single variant"
}}]"""
            repair_raw = call_llm("", simple_prompt, tier="haiku", max_tokens=1000).strip()
            if repair_raw.startswith("```"):
                repair_raw = repair_raw.split("```")[1]
                if repair_raw.startswith("json"):
                    repair_raw = repair_raw[4:]
            variants = json.loads(repair_raw.strip())
    if not isinstance(variants, list):
        variants = [variants]

    # Format D: Claude ranked them in the prompt — trust the order, validate top variant.
    # Other formats: use heuristic hook scoring.
    if video_format == "D":
        best = variants[0]
        hook = best.get("hook", "")
        has_number = any(c.isdigit() for c in hook)
        has_company = any(c.lower() in hook.lower() for c in real_companies) if real_companies else True
        if not (has_number and has_company):
            logger.warning(
                f"Top variant failed specificity check (number={has_number}, company={has_company}). "
                f"Hook: '{hook[:60]}'. Consider regenerating."
            )
    else:
        variants.sort(key=lambda v: _hook_score(v.get("hook", "")), reverse=True)

    logger.success(f"Generated {len(variants)} prop variants (format={video_format}). Best hook: '{variants[0].get('hook', '')[:60]}'")
    return variants


# ─── Skill 3: Render ──────────────────────────────────────────────────────────

def skill_render(
    props: dict,
    formats: list[str] | None = None,
    video_format: str = "B",
    timestamp: str | None = None,
) -> dict:
    """
    Runs npx remotion render for each requested format.

    If formats is None, picks defaults based on video_format:
      A → ["daily_916", "daily_11"]   (Daily Brief)
      B → ["916", "11"]               (AI Reveal — default)
      D → ["916"]                     (Explainer — single 9:16)
      E → ["news_916"]                (News Reaction — 9:16 only, fastest)

    Returns dict keyed by format code, e.g. {"916": path|None, "daily_916": path|None}
    """
    if formats is None:
        formats = FORMAT_RENDER_KEYS.get(video_format, ["916", "11"])

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
            "--jpeg-quality=80",   # reduces file size ~40% vs default, still crisp
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


# ─── Skill 3b: Generate Carousel Slides ──────────────────────────────────────

def skill_generate_carousel(
    topic: dict,
    posts: dict,
    ts: str,
    aspect: str = "916",  # "916" = 9:16 TikTok/Stories | "11" = 1:1 Instagram Feed
) -> list[str]:
    """
    Renders 8 carousel slide images using `npx remotion still`.
    Each slide is rendered from the FinCare-Carousel-{aspect} composition.
    Returns list of absolute paths to the rendered JPG files.
    Falls back gracefully — empty list if Remotion unavailable or slides missing.
    """
    carousel_slides = posts.get("carousel_slides", [])
    if not isinstance(carousel_slides, list) or len(carousel_slides) != 8:
        logger.warning(f"skill_generate_carousel: expected 8 slides, got {len(carousel_slides) if isinstance(carousel_slides, list) else 'non-list'} — skipping.")
        return []

    if not os.path.exists(REMOTION_PROJECT):
        logger.warning(f"Remotion project not found at {REMOTION_PROJECT} — skipping carousel.")
        return []

    comp_id    = f"FinCare-Carousel-{aspect}"
    pillar     = topic.get("content_pillar", "FUNDAMENTALS")
    topic_name = topic.get("topic", "")[:40]  # keep label short for top bar

    out_dir = os.path.join(OUTPUT_DIR, f"carousel_{aspect}_{ts}")
    os.makedirs(out_dir, exist_ok=True)

    rendered = []
    for i, slide in enumerate(carousel_slides):
        slide_num = i + 1
        # Build full props for this slide
        props = {
            "slideType":   slide.get("type", "cover"),
            "title":       slide.get("title", ""),
            "body":        slide.get("body", ""),
            "stat":        slide.get("stat"),
            "company":     slide.get("company"),
            "bullets":     slide.get("bullets", []),
            "slideIndex":  slide_num,
            "totalSlides": 8,
            "pillar":      pillar,
            "topic":       topic_name,
        }
        # Number slide: pass dynamic chart label + benchmark from writer output
        if slide.get("type") == "number":
            cfa = topic.get("cfa_concept", "")
            comp = slide.get("comparison") or slide.get("company", "")
            # Build a chart label like "P/E vs Intel" from cfa_concept + comparison
            if cfa and comp:
                props["chartLabel"] = f"{cfa} vs {comp}"
            elif cfa:
                props["chartLabel"] = f"{cfa} vs Sector"
            if slide.get("comparison"):
                props["benchmark"] = slide["comparison"]
            if slide.get("comparison_val"):
                props["benchmarkVal"] = slide["comparison_val"]
        props_json = json.dumps(props)
        out_file   = f"slide_{slide_num:02d}_{ts}.jpg"
        out_path   = os.path.join(out_dir, out_file)

        cmd = [
            "npx", "remotion", "still",
            "src/index.ts",
            comp_id,
            out_path,
            f"--props={props_json}",
            "--log=error",
        ]

        logger.step(f"Rendering carousel slide {slide_num}/8 ({slide.get('type', '?')})...")
        try:
            result = subprocess.run(
                cmd,
                cwd=REMOTION_PROJECT,
                capture_output=True,
                text=True,
                timeout=120,  # 2 min per slide
            )
            if result.returncode == 0 and os.path.exists(out_path):
                size_kb = os.path.getsize(out_path) / 1024
                logger.success(f"Slide {slide_num}: {out_file} ({size_kb:.0f}KB)")
                rendered.append(out_path)
            else:
                logger.warning(f"Slide {slide_num} render failed: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.warning(f"Slide {slide_num} render timeout (>2min)")
        except FileNotFoundError:
            logger.error("npx not found — Node.js required for carousel rendering.")
            break  # no point retrying if Node is missing
        except Exception as e:
            logger.warning(f"Slide {slide_num} error: {type(e).__name__}: {str(e)[:80]}")

    if rendered:
        logger.success(f"Carousel ({aspect}): {len(rendered)}/8 slides rendered → {out_dir}")
    return rendered


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


# ─── Compression Helper (module-level, importable) ───────────────────────────

def _compress_for_telegram_path(src_path: str, max_mb: float = 45.0) -> str:
    """Compress video for Telegram upload limit. Returns compressed path or original."""
    if not os.path.exists(src_path) or os.path.getsize(src_path) <= max_mb * 1024 * 1024:
        return src_path
    compressed = src_path.replace(".mp4", "_tg.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-vf", "scale=720:-2",
        "-vcodec", "libx264", "-crf", "28", "-preset", "fast",
        "-acodec", "aac", "-b:a", "128k",
        compressed,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and os.path.exists(compressed):
            logger.info(f"Compressed: {os.path.getsize(compressed)/(1024*1024):.1f}MB")
            return compressed
    except Exception:
        pass
    return src_path


# ─── Skill 5: Send Preview ────────────────────────────────────────────────────

def skill_send_preview(
    video_paths: dict,
    props: dict,
    topic: dict,
    token: str,
    chat_id: str,
    session_id: str,
    carousel_paths: list[str] | None = None,
) -> None:
    """
    Sends to Telegram:
    1. Thumbnail with hook + key point (clean preview)
    2. 9:16 video (TikTok/Reels) — compressed to fit Telegram's 50MB limit
    3. 1:1 video (Instagram/LinkedIn) — compressed if needed
    4. Carousel photo album (up to 8 slides) — if carousel_paths provided
    5. Action buttons: New Version / Different Hook / Use This / Copy Script

    Captions are summarised and distinct from the video content — they add
    context and a CTA rather than repeating what is already on screen.
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

    def _compress_for_telegram(src_path: str, max_mb: float = 18.0) -> str:
        """
        Compress video before Telegram send. Threshold is 18MB — Telegram bots
        reliably time out on files >20MB even though the API limit is 50MB.
        Scales down to 720p width for preview quality — original is kept for publishing.
        Returns compressed path, or original if already small enough.
        """
        if os.path.getsize(src_path) <= max_mb * 1024 * 1024:
            return src_path
        compressed = src_path.replace(".mp4", "_tg.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", src_path,
            "-vf", "scale=720:-2",          # 720p width, keep aspect ratio
            "-vcodec", "libx264", "-crf", "28", "-preset", "fast",
            "-acodec", "aac", "-b:a", "128k",
            compressed,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode == 0 and os.path.exists(compressed):
                size_mb = os.path.getsize(compressed) / (1024 * 1024)
                logger.info(f"Compressed for Telegram preview: {size_mb:.1f}MB")
                return compressed
        except Exception:
            pass
        logger.warning("Compression failed — sending original (may exceed Telegram limit).")
        return src_path

    def _send_video(path, caption):
        send_path = _compress_for_telegram(path)
        size_mb = os.path.getsize(send_path) / (1024 * 1024)
        try:
            with open(send_path, "rb") as f:
                requests.post(
                    TELEGRAM_API.format(token=token, method="sendVideo"),
                    data={
                        "chat_id":    chat_id,
                        "caption":    caption[:1024],
                        "parse_mode": "HTML",
                        "supports_streaming": "true",
                    },
                    files={"video": f},
                    timeout=180,
                )
        except Exception as e:
            logger.warning(f"Video send failed ({size_mb:.1f}MB): {type(e).__name__}")

    hook    = props.get("hook", "")
    insight = props.get("insight", "")
    cta     = props.get("ctaText", "")
    stat    = props.get("stat", "")

    # Short summary for captions — distinct from video content (don't repeat the hook)
    # The hook is already the first thing visible in the video; caption adds context.
    summary = insight[:140] if insight else (stat[:140] if stat else "")
    cta_line = f"👉 {cta} — aifincare.com" if cta else "👉 aifincare.com"

    # ── 1. Thumbnail ──────────────────────────────────────────────────────────
    thumb = video_paths.get("thumbnail")
    thumb_caption = (
        f"🎬 <b>Video ready for review</b>\n\n"
        f"<b>Hook:</b> {hook[:120]}\n"
        f"<b>Key point:</b> {summary}\n\n"
        f"{cta_line}"
    )
    if thumb and os.path.exists(thumb):
        _send_photo(thumb, thumb_caption)
    else:
        _send_msg(thumb_caption)

    # ── 2. 9:16 Video — TikTok / Reels ───────────────────────────────────────
    path_916 = video_paths.get("916")
    if path_916 and os.path.exists(path_916):
        caption_916 = (
            f"📱 <b>TikTok · Instagram Reels</b>  |  9:16 vertical\n\n"
            f"{summary}\n\n"
            f"{cta_line}"
        )
        _send_video(path_916, caption_916)

    # ── 3. 1:1 Video — Instagram Feed / LinkedIn ──────────────────────────────
    path_11 = video_paths.get("11")
    if path_11 and os.path.exists(path_11):
        caption_11 = (
            f"📐 <b>Instagram Feed · LinkedIn</b>  |  1:1 square\n\n"
            f"{summary}\n\n"
            f"{cta_line}"
        )
        _send_video(path_11, caption_11)

    # ── 4a. Instagram / TikTok carousel — 9:16 vertical ─────────────────────
    posts      = topic  # posts dict may be passed via topic kwarg from caller
    ig_caption = video_paths.get("instagram_caption", "") or props.get("instagram_caption", "")
    tk_caption = video_paths.get("tiktok_caption", "")    or props.get("tiktok_caption", "")
    ht_ig      = video_paths.get("hashtags_instagram", "") or props.get("hashtags_instagram", "")
    ht_tk      = video_paths.get("hashtags_tiktok", "")   or props.get("hashtags_tiktok", "")

    _carousel_916 = carousel_paths or video_paths.get("carousel_916") or []
    valid_916 = [p for p in _carousel_916 if p and os.path.exists(p)]

    def _send_album(slides, label):
        """Send up to 10 slides as a Telegram media group album."""
        if not slides:
            return
        batch = slides[:10]
        media_group = []
        for idx, slide_path in enumerate(batch):
            caption_text = label if idx == 0 else ""
            media_group.append({
                "type":       "photo",
                "media":      f"attach://slide{idx}",
                "caption":    caption_text[:1024],
                "parse_mode": "HTML",
            })
        files = {f"slide{i}": open(p, "rb") for i, p in enumerate(batch)}
        try:
            resp = requests.post(
                TELEGRAM_API.format(token=token, method="sendMediaGroup"),
                data={"chat_id": chat_id, "media": json.dumps(media_group)},
                files=files,
                timeout=120,
            )
            if resp.status_code == 200:
                logger.success(f"Album sent: {label[:40]}  ({len(batch)} slides)")
            else:
                logger.warning(f"Album send failed: {resp.status_code} {resp.text[:120]}")
        except Exception as e:
            logger.warning(f"Album send error: {type(e).__name__}")
        finally:
            for f in files.values():
                f.close()

    if valid_916:
        _send_album(valid_916, f"📱 <b>Instagram / TikTok</b> — {len(valid_916)} slides  |  9:16 vertical")

        # Instagram caption card — ready to copy-paste
        ig_card_parts = ["📸 <b>INSTAGRAM CAPTION</b> — copy and paste:\n"]
        if ig_caption:
            ig_card_parts.append(f"<code>{ig_caption[:900]}</code>\n")
        if ht_ig:
            ig_card_parts.append(f"<code>{ht_ig[:400]}</code>")
        if ig_card_parts:
            _send_msg("\n".join(ig_card_parts))

        # TikTok caption card
        tk_card_parts = ["🎵 <b>TIKTOK CAPTION</b> — copy and paste:\n"]
        if tk_caption:
            tk_card_parts.append(f"<code>{tk_caption[:700]}</code>\n")
        if ht_tk:
            tk_card_parts.append(f"<code>{ht_tk[:300]}</code>")
        if tk_card_parts:
            _send_msg("\n".join(tk_card_parts))

    # ── 4b. LinkedIn carousel — 1:1 square ───────────────────────────────────
    _carousel_11 = video_paths.get("carousel_11") or []
    valid_11 = [p for p in _carousel_11 if p and os.path.exists(p)]

    if valid_11:
        _send_album(valid_11, f"💼 <b>LinkedIn</b> — {len(valid_11)} slides  |  1:1 square")

        # LinkedIn caption card
        li_caption = video_paths.get("linkedin_company", "") or props.get("linkedin_company", "")
        ht_li      = video_paths.get("hashtags_linkedin", "") or props.get("hashtags_linkedin", "")
        li_card_parts = ["🔵 <b>LINKEDIN CAPTION</b> — copy and paste:\n"]
        if li_caption:
            li_card_parts.append(f"<code>{li_caption[:900]}</code>\n")
        if ht_li:
            li_card_parts.append(f"<code>{ht_li[:200]}</code>")
        if li_card_parts:
            _send_msg("\n".join(li_card_parts))

    # ── 4c. Carousel Video (60s animated, Christopher voice) ─────────────────
    cv_path = video_paths.get("carousel_video_916")
    if cv_path and os.path.exists(cv_path):
        _send_video(
            cv_path,
            "🎬 <b>FINVYON Video</b> — carousel animated (60s)\n"
            "Post as Reel / TikTok / LinkedIn video"
        )

    # ── 4d. Posting schedule reminder ────────────────────────────────────────
    if valid_916 or valid_11 or cv_path:
        _send_msg(
            "⏰ <b>Best time to post:</b>\n"
            "📸 Instagram carousel: 6–9 pm your time\n"
            "🎵 TikTok Photo Mode: 7–9 pm your time\n"
            "💼 LinkedIn: Tue–Thu 8–10 am\n\n"
            "💡 TikTok: upload as <b>Photo Mode</b> (not video) for maximum reach",
            reply_markup={"inline_keyboard": [[
                {"text": "🔄 Regen Slides", "callback_data": f"regen_carousel_{session_id}"},
            ]]},
        )

    # ── 5. Video action buttons (only if a video was sent) ───────────────────
    if video_paths.get("916") and os.path.exists(video_paths.get("916", "")):
        _send_msg(
            "What would you like to do with the video?",
            reply_markup={"inline_keyboard": [
                [
                    {"text": "🔄 New Version",    "callback_data": f"regen_video_{session_id}"},
                    {"text": "🎯 Different Hook", "callback_data": f"diff_hook_{session_id}"},
                ],
                [
                    {"text": "✅ Use This",        "callback_data": f"approve_video_{session_id}"},
                    {"text": "📋 Copy Script",     "callback_data": f"copy_script_{session_id}"},
                ],
            ]},
        )

    logger.success("Preview sent to Telegram.")


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


# ─── Voice Config Helper ──────────────────────────────────────────────────────

VOICE_CONFIG_PATH = os.path.join(DRAFTS_DIR, "voice_config.json")

def _load_voice_ref() -> str | None:
    """Loads the reference WAV path from drafts/voice_config.json."""
    try:
        if os.path.exists(VOICE_CONFIG_PATH):
            with open(VOICE_CONFIG_PATH) as f:
                return json.load(f).get("reference_wav")
    except Exception:
        pass
    return None


# ─── Skill 7: Clone Voice (one-time setup) ────────────────────────────────────

def skill_clone_voice(source_video_path: str, voice_name: str = "Fayez") -> str | None:
    """
    ONE-TIME SETUP — extracts a clean reference WAV from your source video.
    No API key, no subscription, no upload — runs entirely locally using ffmpeg.

    The saved WAV is used by skill_synthesise_voice() on every future video
    via Coqui XTTS v2 (local voice cloning model).

    Usage:
      cd automation
      python -c "from src.video_agent import skill_clone_voice; \\
        skill_clone_voice('../public/Stock market story _1080p_caption.mp4')"

    Returns: path to voice_reference.wav, or None on failure.
    """
    if not os.path.exists(source_video_path):
        logger.error(f"Source video not found: {source_video_path}")
        return None

    # ── Extract clean mono WAV via ffmpeg ────────────────────────────────────
    # XTTS v2 works best with 22050Hz mono WAV
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    voice_ref = os.path.join(DRAFTS_DIR, "voice_reference.wav")

    logger.step("Extracting voice reference from video...")
    cmd = [
        "ffmpeg", "-y",
        "-i", source_video_path,
        "-vn",              # no video
        "-ar", "22050",     # XTTS v2 optimal sample rate
        "-ac", "1",         # mono
        "-af", "loudnorm",  # normalise loudness for consistent cloning
        voice_ref,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0 or not os.path.exists(voice_ref):
        logger.error(f"ffmpeg extraction failed: {result.stderr[:200]}")
        return None
    logger.success(f"Voice reference extracted: {os.path.getsize(voice_ref) // 1024}KB")

    # ── Save reference path ──────────────────────────────────────────────────
    with open(VOICE_CONFIG_PATH, "w") as f:
        json.dump({
            "reference_wav": voice_ref,
            "voice_name":    voice_name,
            "source":        os.path.basename(source_video_path),
            "created_at":    datetime.now().isoformat(),
        }, f, indent=2)

    logger.success(f"Voice reference saved: {voice_ref}")
    logger.success("Run 'pip install TTS' if not installed, then generate a video to hear your voice.")
    return voice_ref


# ─── Skill 8: Generate Voiceover Script ───────────────────────────────────────

def skill_generate_voiceover_script(props: dict, posts: dict) -> str:
    """
    Calls Claude Haiku to write a natural 60-second spoken script
    timed to match the 6 Remotion scenes.

    Returns: plain text with [PAUSE] markers, ~280-320 words.
    """
    hook    = props.get("hook", "")
    insight = props.get("insight", "")
    cta     = props.get("ctaText", "")
    stat    = props.get("stat", "")
    trigger = props.get("trigger", "anxiety")
    tiktok  = posts.get("tiktok_caption", "")

    prompt = f"""Write a 60-second voiceover script for a FINVYON educational finance video.
FINVYON is a CFA Stickman Sensei — CFA charterholder, Old Money mentor, calm and analytical.
Tone: confident, educational, mentor-like — warm but not salesy, CFA-level but plain English.

Video props:
- Hook: {hook}
- Stat: {stat}
- Insight: {insight}
- CTA: {cta}
- TikTok caption context: {tiktok[:300]}

Write 10 spoken lines — one per scene (each line = max 12 words, 6 seconds of speech):
Scene 1  [Hook]           — Pattern interrupt. One scroll-stopping sentence.
Scene 2  [Concept/Trigger]— Introduce the concept or the common mistake.
Scene 3  [The Number]     — State the key stat or formula simply.
Scene 4  [The Why]        — Why this matters. One plain sentence.
Scene 5  [The Trap]       — The common investor mistake or trap.
Scene 6  [Fincare Moment] — EXACT: "I asked AIFinCare the same question."
Scene 7  [The Answer]     — The CFA solution, simply stated.
Scene 8  [The Proof]      — Evidence or result in one punchy sentence.
Scene 9  [The Result]     — The compounded patience outcome.
Scene 10 [CTA]            — Use this exact CTA: "{cta}"

Rules:
- STRICT: max 12 words per line
- Scene 6 MUST be EXACTLY: "I asked AIFinCare the same question."
- Scene 10 MUST use the CTA above verbatim
- Write as spoken words — natural, human, not robotic
- Never say buy/sell/hold or give financial advice
- Output ONLY 10 lines, one per scene, no labels or numbers"""

    script = call_llm("", prompt, tier="haiku", max_tokens=500).strip()
    logger.success(f"Voiceover script generated: {len(script.split())} words")
    return script


# ─── Skill 9: Synthesise Voice ────────────────────────────────────────────────

def skill_synthesise_voice(script: str, voice_ref: str, timestamp: str | None = None) -> str | None:
    """
    Synthesises a voiceover using Coqui XTTS v2 — fully local, zero cost.

    Requires: pip install TTS  (one-time local install, not in requirements.txt)
    First call downloads the XTTS v2 model (~2GB) to ~/.local/share/tts/
    Subsequent calls load from cache — no re-download.

    Performance:
      Apple Silicon M-chip : ~30–60s for 60s of audio
      Intel Mac / CPU-only  : ~2–3 min for 60s of audio

    Returns: path to synthesised WAV, or None on failure.
    """
    try:
        from TTS.api import TTS as CoquiTTS
    except ImportError:
        logger.error("Coqui TTS not installed. Run: pip install TTS")
        return None

    if not os.path.exists(voice_ref):
        logger.error(f"Voice reference not found: {voice_ref}. Run skill_clone_voice() first.")
        return None

    # [PAUSE] markers become natural ellipsis pauses in XTTS
    clean_script = script.replace("[PAUSE]", "... ")

    os.makedirs(DRAFTS_DIR, exist_ok=True)
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    vo_path = os.path.join(DRAFTS_DIR, f"voiceover_{ts}.wav")

    logger.step(f"Synthesising voiceover ({len(clean_script)} chars) — this takes ~30–180s...")
    try:
        tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2")
        tts.tts_to_file(
            text=clean_script,
            speaker_wav=voice_ref,
            language="en",
            file_path=vo_path,
        )
    except Exception as e:
        logger.error(f"XTTS synthesis failed: {type(e).__name__}: {str(e)[:200]}")
        return None

    if not os.path.exists(vo_path):
        logger.error("XTTS produced no output file.")
        return None

    size_kb = os.path.getsize(vo_path) // 1024
    logger.success(f"Voiceover synthesised: {os.path.basename(vo_path)} ({size_kb}KB)")
    return vo_path


# ─── Skill 9b: Edge TTS (free, no API key) ───────────────────────────────────

def skill_synthesise_voice_edge(script: str, timestamp: str | None = None) -> str | None:
    """
    Microsoft Edge TTS — free, no API key, high-quality neural voices.
    Voice: en-US-SteffanNeural (deep, warm, documentary narrator) with SSML
    for natural pauses, slower rate, and cinematic delivery.
    Returns: path to MP3 file, or None on failure.
    """
    try:
        import asyncio
        import edge_tts

        voice    = os.getenv("EDGE_TTS_VOICE", "en-US-ChristopherNeural")  # deep storytelling narrator
        os.makedirs(DRAFTS_DIR, exist_ok=True)
        ts       = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(DRAFTS_DIR, f"voiceover_edge_{ts}.mp3")

        import re

        def _normalize_numbers(text: str) -> str:
            """Expand financial abbreviations so TTS reads them correctly."""
            # Currency + magnitude: $16.3B → 16.3 billion dollars
            text = re.sub(r'\$(\d+\.?\d*)\s*[Tt]', lambda m: f"{m.group(1)} trillion dollars", text)
            text = re.sub(r'\$(\d+\.?\d*)\s*[Bb]', lambda m: f"{m.group(1)} billion dollars", text)
            text = re.sub(r'\$(\d+\.?\d*)\s*[Mm]', lambda m: f"{m.group(1)} million dollars", text)
            text = re.sub(r'\$(\d+\.?\d*)\s*[Kk]', lambda m: f"{m.group(1)} thousand dollars", text)
            # Small dollar ratios/multiples like $1.21 → "1.21" (avoid "one dollar twenty-one cents")
            # Heuristic: $X.XX where value < 50 and not followed by a large-number context → strip $
            text = re.sub(r'\$(\d{1,2}\.\d+)\b', lambda m: m.group(1), text)
            # Bare magnitude suffixes after numbers: 1.7T → 1.7 trillion
            text = re.sub(r'(\d+\.?\d*)\s*T\b', lambda m: f"{m.group(1)} trillion", text)
            text = re.sub(r'(\d+\.?\d*)\s*B\b', lambda m: f"{m.group(1)} billion", text)
            text = re.sub(r'(\d+\.?\d*)\s*M\b', lambda m: f"{m.group(1)} million", text)
            # Multiplier: 2x → 2 times, 256x → 256 times
            text = re.sub(r'(\d+\.?\d*)\s*x\b', lambda m: f"{m.group(1)} times", text)
            # Basis points
            text = re.sub(r'(\d+)\s*bps?\b', lambda m: f"{m.group(1)} basis points", text)
            # YoY / QoQ / YTD
            text = text.replace("YoY", "year over year").replace("QoQ", "quarter over quarter").replace("YTD", "year to date")
            # Colons after labels create awkward pauses — replace with a comma
            text = re.sub(r'([A-Za-z0-9 ]{2,30}):\s+', r'\1, ', text)
            # Remove ticker symbols in parentheses: (MA) → nothing (distracting when spoken)
            text = re.sub(r'\s*\([A-Z]{1,5}\)', '', text)
            # AIFinCare → Fincare (clean brand reference)
            text = text.replace("AIFinCare", "Fincare").replace("aifincare", "Fincare")
            return text

        def _to_plain(text: str) -> str:
            """Convert script to natural plain text — no XML tags (edge_tts speaks XML literally)."""
            text = _normalize_numbers(text)
            # Scene separators → sentence break (period + space = ~400ms natural pause)
            lines = [l.strip() for l in text.replace("[PAUSE]", " ... ").split(" ... ") if l.strip()]
            # Em-dash → comma pause (sounds natural, not robotic)
            clean_lines = [l.replace(" — ", ", ").replace("—", ", ") for l in lines]
            # Join scenes with ". " so TTS treats each as a new sentence
            return ". ".join(clean_lines)

        async def _generate():
            clean = _to_plain(script)
            communicate = edge_tts.Communicate(clean, voice, rate="-8%", pitch="-4Hz")
            sentences = []
            audio_chunks = []
            async for item in communicate.stream():
                if item["type"] == "audio":
                    audio_chunks.append(item["data"])
                elif item["type"] in ("WordBoundary", "SentenceBoundary"):
                    sentences.append({
                        "type":     item["type"],
                        "text":     item["text"],
                        "start":    item["offset"]   / 10_000_000,
                        "duration": item["duration"] / 10_000_000,
                    })
            with open(out_path, "wb") as f:
                for chunk in audio_chunks:
                    f.write(chunk)
            # Derive per-word timings; fall back to proportional sentence estimation
            word_events = [e for e in sentences if e["type"] == "WordBoundary"]
            if word_events:
                words = [{"word": e["text"], "start": e["start"], "duration": e["duration"]} for e in word_events]
            else:
                # Estimate word timings proportionally from sentence boundaries
                words = []
                for s in sentences:
                    if s["type"] != "SentenceBoundary":
                        continue
                    w_list = s["text"].split()
                    if not w_list:
                        continue
                    chars = [max(1, len(w)) for w in w_list]
                    total = sum(chars)
                    t = s["start"]
                    for w, c in zip(w_list, chars):
                        dur = (c / total) * s["duration"]
                        words.append({"word": w, "start": t, "duration": dur})
                        t += dur
            words_path = out_path.replace(".mp3", "_words.json")
            with open(words_path, "w") as wf:
                json.dump(words, wf)

        asyncio.run(_generate())

        if os.path.exists(out_path):
            size_kb = os.path.getsize(out_path) // 1024
            logger.success(f"Edge TTS voiceover: {os.path.basename(out_path)} ({size_kb}KB, voice={voice})")
            return out_path
    except Exception as e:
        logger.warning(f"Edge TTS failed ({type(e).__name__}: {str(e)[:80]})")
    return None


# ─── Skill 9c: ElevenLabs TTS ────────────────────────────────────────────────

def skill_synthesise_voice_elevenlabs(script: str, timestamp: str | None = None) -> str | None:
    """
    ElevenLabs TTS — generates voiceover audio via API.
    Requires: ELEVENLABS_API_KEY in .env
    Voice: Rachel (default) or set ELEVENLABS_VOICE_ID in .env.
    Returns: path to MP3 file, or None on failure.
    """
    api_key  = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None

    import requests as _req
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel
    url      = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    clean    = script.replace("[PAUSE]", "... ")[:2500]

    try:
        resp = _req.post(
            url,
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={
                "text":           clean,
                "model_id":       "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=60,
        )
        resp.raise_for_status()
        os.makedirs(DRAFTS_DIR, exist_ok=True)
        ts       = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(DRAFTS_DIR, f"voiceover_el_{ts}.mp3")
        with open(out_path, "wb") as f:
            f.write(resp.content)
        size_kb = os.path.getsize(out_path) // 1024
        logger.success(f"ElevenLabs voiceover: {os.path.basename(out_path)} ({size_kb}KB)")
        return out_path
    except Exception as e:
        logger.warning(f"ElevenLabs TTS failed ({type(e).__name__}: {str(e)[:80]}) — no audio voiceover.")
        return None


# ─── Skill 10: Mix Audio ──────────────────────────────────────────────────────

def skill_mix_audio(video_path: str, voiceover_path: str, timestamp: str | None = None) -> str | None:
    """
    Uses ffmpeg to mix audio layers into the final video:
      - Track 1 (from Remotion video): background beat + SFX, ducked to 20%
      - Track 2 (ElevenLabs voiceover): full volume foreground

    The Remotion video already has SFX baked in (typing, scanner, logo hit, clicks)
    so no separate SFX timing is needed — just duck the existing audio and add voice on top.

    Returns: path to final mixed MP4, or original video_path on failure.
    """
    if not video_path or not os.path.exists(video_path):
        logger.warning("mix_audio: video_path missing — skipping mix.")
        return video_path

    if not voiceover_path or not os.path.exists(voiceover_path):
        logger.warning("mix_audio: voiceover_path missing — returning original video.")
        return video_path

    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(video_path).replace(".mp4", "")
    out_path = os.path.join(os.path.dirname(video_path), f"{base}_voiced.mp4")

    # ffmpeg filter:
    # [0:a] = Remotion audio (beat + SFX) → duck to 20%
    # [1:a] = voiceover → full 100%
    # duration=first keeps video length; dropout_transition fades out gracefully
    # when voiceover ends early, background beat continues at 20% through end
    filter_complex = (
        "[0:a]volume=0.20[bg];"
        "[1:a]volume=1.0[vo];"
        "[bg][vo]amix=inputs=2:duration=first:dropout_transition=3[aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", voiceover_path,
        "-filter_complex", filter_complex,
        "-map", "0:v",         # keep original video stream
        "-map", "[aout]",      # use mixed audio
        "-c:v", "copy",        # no video re-encode (fast)
        "-c:a", "aac",
        "-b:a", "192k",
        out_path,
    ]

    logger.step(f"Mixing audio: voice + beat + SFX → {os.path.basename(out_path)}...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(out_path):
            size_mb = os.path.getsize(out_path) / (1024 * 1024)
            logger.success(f"Audio mixed: {os.path.basename(out_path)} ({size_mb:.1f}MB)")
            return out_path
        else:
            logger.error(f"ffmpeg mix failed: {result.stderr[:300]}")
            return video_path  # fallback to original
    except subprocess.TimeoutExpired:
        logger.error("Audio mix timeout (>5min)")
        return video_path


# ─── Subtitle helpers ─────────────────────────────────────────────────────────

def _build_ass_subtitles(words_json: str, ass_path: str) -> str | None:
    """
    Groups edge_tts word-boundary timestamps into 3-word chunks.
    Financial numbers & key terms → teal (#00C9A7). Rest → white.
    Style: dark semi-transparent pill (BorderStyle=3), bottom-center, bold Arial 52px.
    """
    import re as _re

    try:
        with open(words_json) as f:
            words = json.load(f)
    except Exception:
        return None

    if not words:
        return None

    # ASS colors (BGR, no alpha in inline override)
    TEAL  = "&H00A7C900"   # #00C9A7
    WHITE = "&H00FFFFFF"

    # Short common words that look like tickers but shouldn't be highlighted
    SKIP_CAPS = {
        "THE", "AND", "BUT", "FOR", "NOT", "ARE", "WAS", "HAS", "ITS",
        "YOU", "CAN", "ALL", "NEW", "TOP", "HOW", "WHY", "THIS", "THAT",
        "FROM", "WITH", "INTO", "OVER", "JUST", "MOST", "ONLY", "DOES",
        "THAN", "THEN", "WHEN", "ALSO", "BOTH", "EVEN", "MORE", "LESS",
        "EACH", "SUCH", "VERY", "BEEN", "HAVE", "THEY", "WILL", "WHAT",
    }

    FINANCIAL_TERMS = {
        "margin", "margins", "profit", "profits", "revenue", "revenues",
        "billion", "trillion", "million", "percent", "percentage", "earnings",
        "valuation", "dividend", "yield", "premium", "discount", "growth",
        "moat", "equity", "debt", "asset", "capital", "cash", "return",
        "roi", "roe", "eps", "ebitda", "fcf", "ipo", "basis", "points",
        "price", "stock", "market", "share", "shares", "quarter", "annual",
        "inflation", "interest", "rate", "rates", "sector", "index",
    }

    def is_key(word: str) -> bool:
        w = word.strip(".,!?;:'\"")
        if _re.match(r'^\d+\.?\d*%?[xX]?$', w):          return True   # 46.5, 46.5%, 3x
        if _re.match(r'^\$[\d,.]+[BMTKbmtk]?$', w):       return True   # $16.3B
        if _re.match(r'^[A-Z]{2,5}$', w) and w not in SKIP_CAPS:
            return True                                                   # TSMC, ROE
        if w.lower().rstrip("s") in FINANCIAL_TERMS:      return True
        return False

    def fmt_time(s: float) -> str:
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        return f"{h}:{m:02d}:{s % 60:05.2f}"

    # Group into 3-word chunks
    chunks = []
    i = 0
    while i < len(words):
        chunk = words[i:i + 3]
        if not chunk:
            break
        t_start = chunk[0]["start"]
        t_end   = chunk[-1]["start"] + chunk[-1]["duration"]
        # Bridge small gaps to avoid flicker between chunks
        if i + 3 < len(words):
            t_end = min(t_end + 0.22, words[i + 3]["start"] - 0.03)
        else:
            t_end += 0.45

        parts = []
        for w in chunk:
            txt = w["word"]
            if is_key(txt):
                parts.append(f"{{\\c{TEAL}&}}{txt}{{\\c{WHITE}&}}")
            else:
                parts.append(txt)

        chunks.append((t_start, t_end, " ".join(parts)))
        i += 3

    # ASS header
    # BorderStyle=3 → opaque background box (the dark pill)
    # BackColour &H66060E14 → rgba(20,14,6,0.60) ≈ dark navy at 60% opacity
    ass = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 1\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,52,{WHITE},&H000000FF,&H00000000,&H66060E14,"
        "-1,0,0,0,100,100,0.5,0,3,0,0,2,40,40,90,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    for t_start, t_end, text in chunks:
        ass += f"Dialogue: 0,{fmt_time(t_start)},{fmt_time(t_end)},Default,,0,0,0,,{text}\n"

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass)
    return ass_path


def skill_burn_subtitles(video_path: str, words_json: str, timestamp: str | None = None) -> str | None:
    """
    Burns Option-B chunk subtitles onto the carousel video.
    Reads edge_tts word-boundary JSON → generates ASS → ffmpeg ass= filter.
    Returns subtitled video path, or None on failure (caller keeps original).
    """
    if not words_json or not os.path.exists(words_json):
        logger.warning("burn_subtitles: no word-boundary JSON — skipping subtitles.")
        return None

    ts      = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    ass_tmp = f"/tmp/fincare_subs_{ts}.ass"

    if not _build_ass_subtitles(words_json, ass_tmp):
        return None

    out_path = video_path.replace(".mp4", "_subbed.mp4")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i",  video_path,
            "-vf", f"ass={ass_tmp}",
            "-c:a", "copy",
            "-preset", "fast",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0 and os.path.exists(out_path):
            size_mb = os.path.getsize(out_path) / (1024 * 1024)
            logger.success(f"Subtitles burned: {os.path.basename(out_path)} ({size_mb:.1f}MB)")
            return out_path
        logger.warning(f"Subtitle burn failed: {result.stderr[:200]}")
    except Exception as e:
        logger.warning(f"Subtitle burn error: {type(e).__name__}: {str(e)[:80]}")
    finally:
        if os.path.exists(ass_tmp):
            os.remove(ass_tmp)
    return None


def _build_subtitle_chunks(words_json: str, fps: int = 30) -> list:
    """
    Converts edge_tts word timings into Remotion SubtitleChunk dicts.
    Groups words 3-at-a-time; financial keywords / numbers flagged as key=True (teal).
    Returns list of {startFrame, endFrame, words, keys} dicts.
    """
    import re as _re

    try:
        with open(words_json) as f:
            words = json.load(f)
    except Exception:
        return []

    if not words:
        return []

    SKIP_CAPS = {
        "THE","AND","BUT","FOR","NOT","ARE","WAS","HAS","ITS","YOU","CAN",
        "ALL","NEW","TOP","HOW","WHY","THIS","THAT","FROM","WITH","INTO",
        "OVER","JUST","MOST","ONLY","DOES","THAN","THEN","WHEN","ALSO",
        "BOTH","EVEN","MORE","LESS","EACH","SUCH","VERY","BEEN","HAVE",
        "THEY","WILL","WHAT","SAID","THEIR","WERE","HERE","THERE",
    }
    FINANCIAL = {
        "margin","margins","profit","profits","revenue","revenues","billion",
        "trillion","million","percent","percentage","earnings","valuation",
        "dividend","yield","premium","discount","growth","moat","equity",
        "debt","asset","capital","cash","return","roi","roe","eps","ebitda",
        "fcf","ipo","basis","points","price","stock","market","share","shares",
        "quarter","annual","inflation","interest","rate","rates","sector","index",
    }

    def is_key(w: str) -> bool:
        c = w.strip(".,!?;:'\"")
        if _re.match(r'^\d+\.?\d*%?[xX]?$', c):             return True
        if _re.match(r'^\$[\d,.]+[BMTKbmtk]?$', c):          return True
        if _re.match(r'^[A-Z]{2,5}$', c) and c not in SKIP_CAPS: return True
        if c.lower().rstrip("s") in FINANCIAL:                return True
        return False

    chunks = []
    i = 0
    while i < len(words):
        group = words[i:i + 3]
        if not group:
            break
        t_start = group[0]["start"]
        t_end   = group[-1]["start"] + group[-1]["duration"]
        if i + 3 < len(words):
            t_end = min(t_end + 0.22, words[i + 3]["start"] - 0.03)
        else:
            t_end += 0.45
        chunks.append({
            "startFrame": max(0, round(t_start * fps)),
            "endFrame":   max(0, round(t_end   * fps)),
            "words":      [w["word"] for w in group],
            "keys":       [is_key(w["word"]) for w in group],
        })
        i += 3

    return chunks


# ─── Skill 11a: Image-to-Video via Kling ─────────────────────────────────────

def skill_image_to_video(image_path: str, scene_id: int, ts: str, motion_prompt: str | None = None) -> str | None:
    """
    Converts a static scene JPEG to a 5-second video clip using Kling v1.6.
    motion_prompt: if provided, overrides the default MOTION_BY_SCENE lookup.
    Returns relative path (for Remotion staticFile) or None if failed/no key.
    """
    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        return None

    import requests as _req

    MOTION_BY_SCENE = {
        1: "slow cinematic push-in, character enters from left, teal accent light builds gradually",
        3: "gentle atmospheric drift, single teal light source intensifies, stillness with subtle depth",
    }
    motion = motion_prompt or MOTION_BY_SCENE.get(scene_id, "slow cinematic drift, atmospheric depth")

    VIDEO_DIR = os.path.join(REMOTION_PROJECT, "public", "ai_bg")
    os.makedirs(VIDEO_DIR, exist_ok=True)

    try:
        import fal_client
        os.environ["FAL_KEY"] = fal_key

        logger.step(f"Kling — uploading scene {scene_id} image for video generation...")
        with open(image_path, "rb") as f:
            image_url = fal_client.upload(f.read(), "image/jpeg")

        logger.step(f"Kling v1.6 — generating scene {scene_id} video ({motion[:50]}...)...")
        result = fal_client.run(
            "fal-ai/kling-video/v1.6/standard/image-to-video",
            arguments={
                "image_url":    image_url,
                "prompt":       motion,
                "duration":     "5",
                "aspect_ratio": "9:16",
            }
        )
        video_url = result["video"]["url"]

        resp = _req.get(video_url, timeout=120)
        resp.raise_for_status()
        out_name = f"scene_vid{scene_id}_{ts}.mp4"
        out_path = os.path.join(VIDEO_DIR, out_name)
        with open(out_path, "wb") as f:
            f.write(resp.content)

        rel_path = f"ai_bg/{out_name}"
        size_kb  = os.path.getsize(out_path) // 1024
        logger.success(f"Kling scene {scene_id} saved: {out_name} ({size_kb}KB)")
        return rel_path

    except Exception as e:
        logger.warning(f"Kling scene {scene_id} failed ({type(e).__name__}: {str(e)[:80]}) — falling back to static")
        return None


# ─── Skill 11a-ext: Pika image-to-video ──────────────────────────────────────

def skill_image_to_video_pika(image_path: str, scene_id: int, motion: str, ts: str) -> str | None:
    """Pika 2.2 — creative effects & transitions, $0.36/6s."""
    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        return None
    try:
        import fal_client
        import requests as _req
        os.environ["FAL_KEY"] = fal_key
        VIDEO_DIR = os.path.join(REMOTION_PROJECT, "public", "ai_bg")
        os.makedirs(VIDEO_DIR, exist_ok=True)
        logger.step(f"Pika — uploading scene {scene_id} image...")
        with open(image_path, "rb") as f:
            image_url = fal_client.upload(f.read(), "image/jpeg")
        logger.step(f"Pika v2.2 — generating scene {scene_id} video ({motion[:40]}...)...")
        result = fal_client.run("fal-ai/pika/v2.2/image-to-video", arguments={
            "image_url": image_url,
            "prompt":    motion,
            "duration":  6,
        })
        video_url = result["video"]["url"]
        resp = _req.get(video_url, timeout=120)
        resp.raise_for_status()
        out_name = f"scene_vid{scene_id}_{ts}.mp4"
        out_path = os.path.join(VIDEO_DIR, out_name)
        with open(out_path, "wb") as f:
            f.write(resp.content)
        size_kb = os.path.getsize(out_path) // 1024
        logger.success(f"Pika scene {scene_id} saved: {out_name} ({size_kb}KB)")
        return f"ai_bg/{out_name}"
    except Exception as e:
        logger.warning(f"Pika scene {scene_id} failed ({type(e).__name__}: {str(e)[:80]}) — static fallback")
        return None


# ─── Skill 11a-ext: Luma image-to-video ──────────────────────────────────────

def skill_image_to_video_luma(image_path: str, scene_id: int, motion: str, ts: str) -> str | None:
    """Luma Dream Machine Ray-2 — fast prototyping, $0.24/5s."""
    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        return None
    try:
        import fal_client
        import requests as _req
        os.environ["FAL_KEY"] = fal_key
        VIDEO_DIR = os.path.join(REMOTION_PROJECT, "public", "ai_bg")
        os.makedirs(VIDEO_DIR, exist_ok=True)
        logger.step(f"Luma — uploading scene {scene_id} image...")
        with open(image_path, "rb") as f:
            image_url = fal_client.upload(f.read(), "image/jpeg")
        logger.step(f"Luma Ray-2 — generating scene {scene_id} video ({motion[:40]}...)...")
        result = fal_client.run("fal-ai/luma-dream-machine/ray-2/image-to-video", arguments={
            "image_url": image_url,
            "prompt":    motion,
        })
        video_url = result["video"]["url"]
        resp = _req.get(video_url, timeout=120)
        resp.raise_for_status()
        out_name = f"scene_vid{scene_id}_{ts}.mp4"
        out_path = os.path.join(VIDEO_DIR, out_name)
        with open(out_path, "wb") as f:
            f.write(resp.content)
        size_kb = os.path.getsize(out_path) // 1024
        logger.success(f"Luma scene {scene_id} saved: {out_name} ({size_kb}KB)")
        return f"ai_bg/{out_name}"
    except Exception as e:
        logger.warning(f"Luma scene {scene_id} failed ({type(e).__name__}: {str(e)[:80]}) — static fallback")
        return None


# ─── Skill 11b: Generate Scene Descriptions (Format D — 10 scenes) ───────────

def skill_generate_scenes(topic: dict, props: dict, scene_voiceovers: list | None = None) -> list[dict]:
    """
    WORKFLOW.md Agent 2: Generates 10 scene visual descriptions for Format D (FINVYON Explainer).
    Scene Director responsibility: img_prompt + motion_instruction ONLY.
    Voiceovers come from the Writer (scene_voiceovers param) and are merged in post-processing.
    motion_instruction keywords drive _select_strategy() → Kling/Static.
    Returns list of 10 scene dicts with voiceover merged in.
    """
    import hashlib

    pillar = topic.get("content_pillar", "FUNDAMENTALS").upper()

    # Rotating CTA for Scene 10 — deterministic across sessions
    FINCARE_CTAS = [
        "Download AIFinCare. Let the AI manage the emotion, you manage the vision.",
        "AIFinCare removes the emotional guesswork. Link in bio.",
        "This is what AIFinCare was built for. Try it free.",
        "Your amygdala is expensive. AIFinCare is not. Link in bio.",
    ]
    topic_str = topic.get("topic", "")
    cta_index = int(hashlib.md5(topic_str.encode()).hexdigest(), 16) % len(FINCARE_CTAS)
    scene10_cta = FINCARE_CTAS[cta_index]

    # Determine if Writer provided voiceovers
    has_voiceovers = isinstance(scene_voiceovers, list) and len(scene_voiceovers) == 10

    # Build vo_list: Writer's lines if available, else placeholder
    if has_voiceovers:
        vo_list = list(scene_voiceovers)
    else:
        vo_list = [f"Scene {i}." for i in range(1, 11)]

    # Always enforce fixed lines
    vo_list[5] = "I asked AIFinCare the same question."   # scene 6 (index 5)
    vo_list[9] = scene10_cta                              # scene 10 (index 9)

    # Researcher context for richer visual brief
    cfa_concept    = topic.get("cfa_concept", "")
    real_companies = topic.get("real_companies", [])
    key_stat       = props.get("stat", topic.get("key_stat", ""))
    misconception  = topic.get("the_misconception", "")
    correct_view   = topic.get("the_correct_view", "")

    # fincareQuestion: what FINVYON asks AIFinCare in Scene 6
    if cfa_concept:
        fincare_question = f"What does {cfa_concept} actually mean for my portfolio?"
    else:
        fincare_question = topic_str[:60] if topic_str else "How does this work?"

    vo_section = ""
    if has_voiceovers:
        vo_section = f"""
VOICEOVERS (scenes 1-10 — already written by the Writer, DO NOT modify):
{json.dumps(vo_list, indent=2)}

Your job: produce img_prompt + motion_instruction that ILLUSTRATES each voiceover line.
"""
    else:
        vo_section = """
No voiceovers provided. Generate img_prompt + motion_instruction for generic FINVYON arc.
"""

    prompt = f"""You are the Scene Director for a FINVYON 10-scene explainer video.
Your ONLY job: produce img_prompt + motion_instruction for each scene. Do NOT write voiceovers.

─── RESEARCHER'S BRIEF ───────────────────────────────────────────────────────
Topic: {topic_str}
Pillar: {pillar}
CFA Concept: {cfa_concept}
Key Stat: {key_stat}
Real Companies: {real_companies}
Misconception: {misconception}
Correct View: {correct_view}
Scene 6 Question (fincareQuestion): {fincare_question}
──────────────────────────────────────────────────────────────────────────────
{vo_section}
CHARACTER (identical in every scene):
{CHARACTER_BASE_FINVYON}

SCENE ARC:
1 Hook  2 Concept  3 Number  4 Why  5 Trap  6 Fincare  7 Answer  8 Proof  9 Result  10 CTA

SCENE 6 IS FIXED — DO NOT CHANGE:
- img_prompt: "Refined black stickman in thoughtful pose, one hand on chin, looking slightly upward. Pure white background. 9:16 vertical."
- motion_instruction: "stands with hand on chin, thoughtful pause"

PROPS (pick one per scene):
brain icon, smartphone with red chart, vertical whiteboard, framed portrait, cliff ledge,
blueprint scroll, golden coin stack, leather wingback armchair, newspaper, golden key,
magnifying glass, teacup, compass, hourglass, oak tree, pocket watch, chess piece, podium.

IMAGE RULES:
- img_prompt MUST start with: "Pure white background."
- Flat 2D, clean black outlines, 9:16 vertical.
- NO text, labels, or numbers IN the image. Exception: scene 3 may show the key_stat
  and a real company ticker as stylized text ON the whiteboard prop.
- Each img_prompt: 25-40 words. Format: "Pure white background. Full-body FINVYON + [action] + [prop]. 9:16 vertical."

MOTION RULES (keywords drive engine selection — choose carefully):
- "taps," "raises," "nods," "leans," "walks," "turns," "waves" → Kling (scenes 1/5/10 ONLY)
- "stands," "sits," "holds," "faces," "poses" → Static (default for all other scenes)

Return raw JSON array of exactly 10 objects. Each object:
{{
  "scene_id": <1-10>,
  "img_prompt": "<25-40 words starting with 'Pure white background.'>",
  "motion_instruction": "<verb phrase>",
  "type": "fincare_moment" | "standard"
}}
No voiceover field. No markdown. No explanation."""

    try:
        raw = call_llm("", prompt, tier="haiku", max_tokens=2500).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        if not raw.endswith("]"):
            last = raw.rfind("]")
            if last != -1:
                raw = raw[:last + 1]
        scenes = json.loads(raw)

        # Pad or truncate to exactly 10 scenes
        if len(scenes) < 10:
            logger.warning(f"Scene Director returned {len(scenes)} scenes — padding to 10.")
            for i in range(len(scenes) + 1, 11):
                scenes.append({
                    "scene_id": i,
                    "img_prompt": f"Pure white background. Full-body FINVYON stands holding a teacup, calm expression. 9:16 vertical.",
                    "motion_instruction": "stands calmly holding teacup",
                    "type": "standard",
                })
        elif len(scenes) > 10:
            scenes = scenes[:10]

        # Merge voiceovers from vo_list (Scene Director doesn't write them)
        for s in scenes:
            sid = s.get("scene_id", 0)
            if 1 <= sid <= 10:
                s["voiceover"] = vo_list[sid - 1]

        # Enforce Scene 6 — override regardless of what Claude returned
        for s in scenes:
            if s.get("scene_id") == 6:
                s["type"] = "fincare_moment"
                s["img_prompt"] = (
                    "Pure white background. Refined black stickman in thoughtful pose, "
                    "one hand on chin, looking slightly upward. 9:16 vertical."
                )
                s["motion_instruction"] = "stands with hand on chin, thoughtful pause"
                s["voiceover"] = "I asked AIFinCare the same question."
                s["fincareQuestion"] = fincare_question
            elif not s.get("type"):
                s["type"] = "standard"

        logger.success(f"Generated {len(scenes)} FINVYON scene descriptions.")
        return scenes
    except Exception as e:
        logger.warning(f"skill_generate_scenes failed ({type(e).__name__}) — using generic fallback scenes.")
        return []


# ─── Skill 11: Generate Scene Images (fal.ai) ────────────────────────────────

def _find_last_good_image(scene_num: int, ai_bg_dir: str) -> str:
    """
    Finds the most recently generated real FINVYON scene image for a given scene number.
    Checks both naming conventions: scene_img{N}_*.jpg and finvyon_scene{N:02d}_*.jpg
    Returns relative path (for Remotion staticFile) or "ai_bg/placeholder.jpg" as last resort.
    """
    import glob as _glob
    patterns = [
        os.path.join(ai_bg_dir, f"scene_img{scene_num}_*.jpg"),
        os.path.join(ai_bg_dir, f"finvyon_scene{scene_num:02d}_*.jpg"),
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(_glob.glob(pat))
    if candidates:
        # Pick most recently modified
        best = max(candidates, key=os.path.getmtime)
        rel  = "ai_bg/" + os.path.basename(best)
        logger.info(f"Fallback image for scene {scene_num}: {os.path.basename(best)}")
        return rel
    return "ai_bg/placeholder.jpg"


def _save_last_good_run(rel_paths: dict, ai_bg_dir: str) -> None:
    """Saves a pointer to the last successful image set so future runs can reuse them."""
    import json as _json
    pointer_path = os.path.join(ai_bg_dir, "last_good_run.json")
    good = {k: v for k, v in rel_paths.items() if k.startswith("img") and "placeholder" not in v}
    if len(good) >= 8:  # only save if most images succeeded
        try:
            with open(pointer_path, "w") as f:
                _json.dump(good, f, indent=2)
            logger.info(f"Saved last good run pointer ({len(good)} images).")
        except Exception:
            pass


def _load_last_good_run(ai_bg_dir: str) -> dict:
    """Loads the last successful image set pointer."""
    import json as _json
    pointer_path = os.path.join(ai_bg_dir, "last_good_run.json")
    try:
        if os.path.exists(pointer_path):
            with open(pointer_path) as f:
                return _json.load(f)
    except Exception:
        pass
    return {}


def _generate_image_pollinations(prompt: str, sid: int, ts: str, ai_bg_dir: str) -> str | None:
    """
    Generates a scene image using Pollinations.ai — completely free, no API key required.
    Uses FLUX model via their public URL API. Returns relative path or None on failure.
    """
    import urllib.parse
    import requests as _req
    try:
        clean = prompt[:500].replace("\n", " ")
        encoded = urllib.parse.quote(clean)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1080&height=1920&nologo=true&seed={sid * 42}&model=flux"
        )
        resp = _req.get(url, timeout=90)
        if resp.status_code == 200 and len(resp.content) > 10000:  # sanity check — real image
            fname = f"scene_img{sid}_pollinations_{ts}.jpg"
            fpath = os.path.join(ai_bg_dir, fname)
            with open(fpath, "wb") as f:
                f.write(resp.content)
            logger.success(f"Pollinations image saved: {fname}")
            return f"ai_bg/{fname}"
    except Exception as e:
        logger.warning(f"Pollinations scene {sid} failed: {type(e).__name__}")
    return None


def _generate_image_huggingface(prompt: str, sid: int, ts: str, ai_bg_dir: str) -> str | None:
    """
    Generates a scene image using HuggingFace Inference API (FLUX.1-schnell) — free tier.
    Requires HF_TOKEN in .env (free account at huggingface.co). Returns relative path or None.
    """
    import requests as _req
    hf_token = os.getenv("HF_TOKEN", "")
    if not hf_token:
        return None
    try:
        API_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
        resp = _req.post(
            API_URL,
            headers={"Authorization": f"Bearer {hf_token}"},
            json={"inputs": prompt[:400]},
            timeout=120,
        )
        if resp.status_code == 200 and not resp.content.startswith(b'{"error'):
            fname = f"scene_img{sid}_hf_{ts}.jpg"
            fpath = os.path.join(ai_bg_dir, fname)
            with open(fpath, "wb") as f:
                f.write(resp.content)
            logger.success(f"HuggingFace image saved: {fname}")
            return f"ai_bg/{fname}"
        else:
            logger.warning(f"HuggingFace scene {sid}: status {resp.status_code}")
    except Exception as e:
        logger.warning(f"HuggingFace scene {sid} failed: {type(e).__name__}")
    return None


def skill_generate_scene_images(topic: dict, posts: dict, props: dict, ts: str, video_format: str = "B") -> dict:
    """
    Format D  → FINVYON 10-scene illustrated (white bg), per-scene Kling/Pika/Luma/Static strategy
    All other → cinematic dark background, 4 scenes, Kling for scenes 1 & 3
    Returns dict: {img1..imgN, vid1..vidN, vo1..voN} relative paths + voiceover text.
    When fal.ai fails or balance is exhausted, falls back to last successful scene images.
    """
    fal_key = os.getenv("FAL_KEY")
    # fal.ai is optional — pipeline continues with Pollinations/HuggingFace/last-good fallbacks
    if fal_key:
        import fal_client
        os.environ["FAL_KEY"] = fal_key

    import requests as _req
    AI_BG_DIR = os.path.join(REMOTION_PROJECT, "public", "ai_bg")
    os.makedirs(AI_BG_DIR, exist_ok=True)

    rel_paths   = {}  # relative paths for Remotion staticFile()
    local_paths = {}  # absolute paths for AI video upload
    video_paths = {}  # vid1, vid3, etc.

    trigger = topic.get("emotional_trigger", "anxiety")

    # ── FORMAT D: FINVYON 10-scene WORKFLOW.md ────────────────────────────────
    if video_format == "D":
        brand_suffix = (
            f" {CHARACTER_BASE_FINVYON} "
            "Flat 2D illustration style. No text, no labels, no numbers in the image "
            "(except on the whiteboard prop in scene 3). 9:16 vertical aspect ratio."
        )
        negative = "photorealistic, 3D render, watermark, dark background, blurry, low quality, landscape orientation"

        # Step 1: Generate 10 scene visuals (img_prompt + motion_instruction)
        # Voiceovers come from the Writer — Scene Director only handles visuals
        writer_voiceovers = posts.get("tiktok_scene_voiceovers", []) if isinstance(posts, dict) else []
        if not isinstance(writer_voiceovers, list) or len(writer_voiceovers) != 10:
            logger.warning(f"tiktok_scene_voiceovers invalid (got {type(writer_voiceovers).__name__} len={len(writer_voiceovers) if isinstance(writer_voiceovers, list) else 'N/A'}) — Scene Director will use placeholders.")
            writer_voiceovers = None
        scenes_meta = skill_generate_scenes(topic, props, scene_voiceovers=writer_voiceovers)
        if not scenes_meta:
            logger.warning("Scene generation failed — using generic 10-scene fallback.")
            _fallback_vos = writer_voiceovers if writer_voiceovers and len(writer_voiceovers) == 10 else [f"Scene {i + 1}." for i in range(10)]
            scenes_meta = [
                {
                    "scene_id": i + 1,
                    "img_prompt": f"{CHARACTER_BASE_FINVYON} Scene {i+1}: standing calmly with a teacup. White background.",
                    "motion_instruction": "stands calmly holding teacup",
                    "voiceover": _fallback_vos[i],
                    "type": "fincare_moment" if i == 5 else "standard",
                }
                for i in range(10)
            ]

        # Step 2: Generate all scene images — provider fallback chain
        # Priority: fal.ai (best) → Pollinations.ai (free) → HuggingFace (free) → last saved
        for scene_meta in scenes_meta:
            sid = scene_meta["scene_id"]
            key = f"img{sid}"
            full_prompt = scene_meta.get("img_prompt", "") + brand_suffix
            img_rel = None

            # Provider 1: fal.ai FLUX Pro (best quality, paid)
            if fal_key and not img_rel:
                try:
                    logger.step(f"Generating scene image {sid} via fal.ai...")
                    gen = fal_client.run(
                        "fal-ai/flux-pro/v1.1",
                        arguments={
                            "prompt":           full_prompt,
                            "negative_prompt":  negative,
                            "image_size":       {"width": 1080, "height": 1920},
                            "num_images":       1,
                            "output_format":    "jpeg",
                            "safety_tolerance": "5",
                        }
                    )
                    url      = gen["images"][0]["url"]
                    img_data = _req.get(url, timeout=30).content
                    fname    = f"scene_{key}_{ts}.jpg"
                    fpath    = os.path.join(AI_BG_DIR, fname)
                    with open(fpath, "wb") as f:
                        f.write(img_data)
                    img_rel          = f"ai_bg/{fname}"
                    local_paths[key] = fpath
                    logger.success(f"fal.ai image saved: {fname}")
                except Exception as e:
                    logger.warning(f"fal.ai scene {sid} failed ({type(e).__name__}) — trying Pollinations...")

            # Provider 2: Pollinations.ai (free, no key needed)
            if not img_rel:
                img_rel = _generate_image_pollinations(full_prompt, sid, ts, AI_BG_DIR)
                if img_rel:
                    local_paths[key] = os.path.join(AI_BG_DIR, os.path.basename(img_rel))

            # Provider 3: HuggingFace FLUX.1-schnell (free, needs HF_TOKEN)
            if not img_rel:
                logger.warning(f"Pollinations scene {sid} failed — trying HuggingFace...")
                img_rel = _generate_image_huggingface(full_prompt, sid, ts, AI_BG_DIR)
                if img_rel:
                    local_paths[key] = os.path.join(AI_BG_DIR, os.path.basename(img_rel))

            # Provider 4: Last saved good image (always available)
            if not img_rel:
                logger.warning(f"All live providers failed for scene {sid} — using last good image.")
                img_rel = _find_last_good_image(sid, AI_BG_DIR)

            rel_paths[key] = img_rel

            # Store voiceover, type, and fincareQuestion for Remotion props
            rel_paths[f"vo{sid}"] = scene_meta.get("voiceover", "")
            rel_paths[f"type{sid}"] = scene_meta.get("type", "standard")
            if scene_meta.get("fincareQuestion"):
                rel_paths[f"fq{sid}"] = scene_meta["fincareQuestion"]

        # Save pointer to last good run so future fallbacks have fresh images
        _save_last_good_run(rel_paths, AI_BG_DIR)

        # Step 3: Per-scene strategy selector (WORKFLOW.md)
        # Hard cap: max 3 Kling scenes — Metronome rule: scenes 1, 5, 10 get motion (hook/turn/CTA)
        _use_kling = os.getenv("USE_KLING", "").lower() in ("1", "true", "yes")
        KLING_PRIORITY = [1, 10, 5]  # priority order when Kling is enabled
        total_cost = len(scenes_meta) * COST_IMAGE
        scene_strategies = {}
        for scene_meta in scenes_meta:
            sid = scene_meta["scene_id"]
            strat = _select_strategy(scene_meta.get("motion_instruction", ""))
            # Disable Kling unless USE_KLING=true — Ken Burns handles motion for free in Remotion
            if strat == "kling" and not _use_kling:
                strat = "static"
            scene_strategies[sid] = strat
            total_cost += STRATEGY_COST.get(strat, 0)

        # Enforce max 3 Kling scenes — downgrade all others to static
        kling_sids = [s for s, st in scene_strategies.items() if st == "kling"]
        if len(kling_sids) > 3:
            # Keep only the 3 priority scenes, downgrade the rest
            keep = set()
            for prio in KLING_PRIORITY:
                if prio in kling_sids:
                    keep.add(prio)
                if len(keep) == 3:
                    break
            # If fewer than 3 priority hits, fill from remaining kling scenes
            for sid in kling_sids:
                if len(keep) >= 3:
                    break
                keep.add(sid)
            for sid in kling_sids:
                if sid not in keep:
                    total_cost -= STRATEGY_COST.get("kling", 0)
                    scene_strategies[sid] = "static"

        # Budget check: downgrade most expensive non-static scenes if still over limit
        if total_cost > BUDGET_MAX:
            logger.warning(f"Budget ${total_cost:.2f} > max ${BUDGET_MAX} — downgrading scenes")
            sorted_sids = sorted(
                [s for s, st in scene_strategies.items() if st != "static"],
                key=lambda s: STRATEGY_COST.get(scene_strategies[s], 0),
                reverse=True,
            )
            for sid in sorted_sids:
                if total_cost <= BUDGET_MAX:
                    break
                total_cost -= STRATEGY_COST.get(scene_strategies[sid], 0)
                scene_strategies[sid] = "static"

        strategy_summary = " | ".join(f"s{s}:{st}" for s, st in sorted(scene_strategies.items()) if st != "static")
        logger.info(f"Video budget: ${total_cost:.2f} | AI motion: {strategy_summary or 'all static'}")

        # Step 4: Generate video clips for non-static scenes
        for sid, strat in scene_strategies.items():
            if strat == "static":
                continue
            abs_path = local_paths.get(f"img{sid}")
            if not abs_path or not os.path.exists(abs_path):
                continue
            scene_meta_match = next((s for s in scenes_meta if s["scene_id"] == sid), {})
            motion = scene_meta_match.get("motion_instruction", "slow cinematic drift")
            if strat == "kling":
                vid_path = skill_image_to_video(abs_path, sid, ts, motion_prompt=motion)
            elif strat == "pika":
                vid_path = skill_image_to_video_pika(abs_path, sid, motion, ts)
            elif strat == "luma":
                vid_path = skill_image_to_video_luma(abs_path, sid, motion, ts)
            else:
                vid_path = None
            if vid_path:
                video_paths[f"vid{sid}"] = vid_path

        return {**rel_paths, **video_paths}

    # ── FORMAT B/A/E: Cinematic dark background, 4 scenes ────────────────────
    hook      = props.get("hook", topic.get("hook", ""))
    stat      = props.get("stat", topic.get("key_stat", ""))
    insight   = props.get("insight", topic.get("angle", ""))
    post_text = ""
    for key in ["linkedin_company", "instagram_caption", "threads_post"]:
        if posts.get(key):
            post_text = posts[key][:300]
            break

    brand_suffix = " Deep navy background, teal accent lighting. No text, no words. Cinematic 4K photography. Premium fintech editorial."
    negative     = "text, words, letters, watermarks, cartoon, clipart, blurry, low quality, bright white background"

    prompt_request = f"""Write 4 cinematic image prompts for a Fincare finance video.
Topic: {topic.get('topic','')} | Hook: {hook} | Stat: {stat} | Insight: {insight}
Post excerpt: {post_text} | Trigger: {trigger}
Style: Deep navy #1A2B4A bg, single teal #00C9A7 accent light. NO text, NO charts. Cinematic 4K.
Scene 1 (Hook): HIGH energy — visual tension | Scene 2 (Stat): PAUSE — abstract data
Scene 3 (Insight): CLARITY — revelation teal light | Scene 4 (CTA): CALM — Old Money stillness
Return ONLY JSON: {{"scene1_hook":"...","scene2_stat":"...","scene3_insight":"...","scene4_cta":"..."}}"""

    try:
        raw = call_llm("", prompt_request, tier="haiku", max_tokens=600).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
            raw = raw.strip()
        scene_prompts = json.loads(raw)
    except Exception as e:
        logger.warning(f"Scene prompt generation failed ({type(e).__name__}) — generic prompts.")
        scene_prompts = {
            "scene1_hook":    f"Person at stock market screens, {trigger} expression, dark moody lighting, deep navy background",
            "scene2_stat":    "Abstract financial data visualization, glowing numbers, teal accent light, dark background",
            "scene3_insight": "Calm person thinking clearly, soft teal lighting, peaceful finance concept, dark studio",
            "scene4_cta":     "Aspirational investor, calm confident expression, premium minimal setting, teal and navy palette",
        }

    scene_map = [
        ("img1", scene_prompts.get("scene1_hook", "")),
        ("img2", scene_prompts.get("scene2_stat", "")),
        ("img3", scene_prompts.get("scene3_insight", "")),
        ("img4", scene_prompts.get("scene4_cta", "")),
    ]

    for key, scene_prompt in scene_map:
        try:
            if not fal_key:
                raise RuntimeError("fal.ai key not set — skipping to placeholder.")
            logger.step(f"Generating scene image: {key}...")
            gen = fal_client.run(
                "fal-ai/flux-pro/v1.1",
                arguments={
                    "prompt":           scene_prompt + brand_suffix,
                    "negative_prompt":  negative,
                    "image_size":       {"width": 1080, "height": 1920},
                    "num_images":       1,
                    "output_format":    "jpeg",
                    "safety_tolerance": "5",
                }
            )
            url      = gen["images"][0]["url"]
            img_data = _req.get(url, timeout=30).content
            fname    = f"scene_{key}_{ts}.jpg"
            fpath    = os.path.join(AI_BG_DIR, fname)
            with open(fpath, "wb") as f:
                f.write(img_data)
            rel_paths[key]   = f"ai_bg/{fname}"
            local_paths[key] = fpath
            logger.success(f"Scene image saved: {fname}")
        except Exception as e:
            logger.warning(f"Scene image {key} failed ({type(e).__name__}) — placeholder.")
            rel_paths[key] = "ai_bg/placeholder.jpg"

    # Kling for scenes 1 & 3 (legacy 4-scene strategy)
    total_cost = 4 * COST_IMAGE + 2 * COST_KLING
    if total_cost > BUDGET_MAX:
        logger.warning(f"Budget ${total_cost:.2f} > max — downgrading scene 3 to static")
        total_cost -= COST_KLING
    logger.info(f"Video budget: ${total_cost:.2f} | Scene 1: kling | Scene 3: {'kling' if total_cost > 4*COST_IMAGE+COST_KLING else 'static'}")

    for scene_id, img_key in [(1, "img1"), (3, "img3")]:
        abs_path = local_paths.get(img_key)
        if abs_path and os.path.exists(abs_path):
            vid_path = skill_image_to_video(abs_path, scene_id, ts)
            if vid_path:
                video_paths[f"vid{scene_id}"] = vid_path

    return {**rel_paths, **video_paths}


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

    # ── Step 1: Select format + template ─────────────────────────
    try:
        video_format = skill_select_format(topic)
        _template    = skill_select_template(topic)
    except Exception as e:
        logger.warning(f"Format/template selection failed: {type(e).__name__}")
        video_format = "D"  # default — all pillars produce FINVYON explainer
        _template    = "emotional"

    # ── Step 2: Generate props variants ───────────────────────────
    try:
        variants = skill_generate_props(topic, viral_intel, n=3, video_format=video_format)
        best_props = variants[0]
    except Exception as e:
        logger.error(f"Props generation failed: {type(e).__name__}: {str(e)}")
        return {"916": None, "11": None, "thumbnail": None, "props": None, "variants": []}

    # Save props for reference + regeneration
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    props_path = os.path.join(DRAFTS_DIR, f"video_props_{ts}.json")
    with open(props_path, "w") as f:
        json.dump({"best": best_props, "variants": variants, "topic": topic}, f, indent=2)

    # ── Step 3: Carousel Slides only pipeline ────────────────────
    final_916 = None
    final_11  = None

    # ── Step 4: Carousel Slides → Video ──────────────────────────
    carousel_916 = []
    carousel_11  = []
    if posts.get("carousel_slides"):
        try:
            logger.step("Generating carousel slides (Instagram / TikTok Photo Mode)...")
            carousel_916 = skill_generate_carousel(topic, posts, ts, aspect="916")
            carousel_11  = skill_generate_carousel(topic, posts, ts, aspect="11")
            logger.success(f"Carousel: {len(carousel_916)} slides (9:16) + {len(carousel_11)} slides (1:1)")
        except Exception as e:
            logger.warning(f"Carousel generation failed (non-critical): {type(e).__name__}: {str(e)[:80]}")

    # ── Step 5: Thumbnail ─────────────────────────────────────────
    thumbnail = None
    if final_916:
        try:
            thumbnail = skill_extract_thumbnail(final_916, frame_sec=3.0)
        except Exception as e:
            logger.warning(f"Thumbnail failed (non-critical): {type(e).__name__}")

    # ── Step 5: Voiceover ─────────────────────────────────────────
    vo_path   = None
    vo_lines  = []
    script    = None
    if final_916 or (carousel_916 and len(carousel_916) == 8):
        try:
            logger.step("Voiceover pipeline starting...")
            scene_vos = posts.get("tiktok_scene_voiceovers", [])
            if isinstance(scene_vos, list) and len(scene_vos) >= 8:
                # Sanitize: remove banned phrases and clean brand name
                cleaned = []
                for v in scene_vos:
                    v = v.strip()
                    v = v.replace("AIFinCare", "Fincare").replace("aifincare.com", "Fincare dot com")
                    # Remove "I asked Fincare/AIFinCare..." filler lines
                    if v.lower().startswith("i asked") or v.lower().startswith("i used"):
                        v = "Fincare runs the same analysis in seconds."
                    cleaned.append(v)
                vo_lines = [v for v in cleaned if v]
                script   = " ... ".join(vo_lines)
                logger.info(f"Using writer scene_voiceovers for audio ({len(scene_vos)} scenes).")
            else:
                script   = skill_generate_voiceover_script(best_props, posts)
                vo_lines = script.split(" ... ") if script else []
            vo_path = skill_synthesise_voice_elevenlabs(script, timestamp=ts)
            if not vo_path:
                vo_path = skill_synthesise_voice_edge(script, timestamp=ts)
            if not vo_path:
                voice_ref = _load_voice_ref()
                if voice_ref:
                    vo_path = skill_synthesise_voice(script, voice_ref, timestamp=ts)
            if vo_path and final_916:
                mixed = skill_mix_audio(final_916, vo_path, timestamp=ts)
                if mixed:
                    final_916 = mixed
                if final_11:
                    mixed_11 = skill_mix_audio(final_11, vo_path, timestamp=ts)
                    if mixed_11:
                        final_11 = mixed_11
        except Exception as e:
            logger.warning(f"Voiceover pipeline failed (non-critical): {type(e).__name__}: {str(e)[:100]}")

    # ── Carousel Video — animate carousel slides into a branded ~60s video ──
    carousel_video = None
    if carousel_916 and len(carousel_916) == 8:
        try:
            logger.step("Rendering carousel video from slide images...")
            slide_rel_paths = _copy_slides_to_public(carousel_916, ts)
            if len(slide_rel_paths) == 8:
                scene_durations = _compute_scene_durations(vo_lines)
                # Scale scene durations to match actual audio length so video never ends early
                if vo_path:
                    try:
                        probe = subprocess.run(
                            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                             "-of", "csv=p=0", vo_path],
                            capture_output=True, text=True, timeout=10,
                        )
                        audio_secs = float(probe.stdout.strip())
                        target_frames = round(audio_secs * 30)
                        orig_total    = sum(scene_durations)
                        if abs(target_frames - orig_total) > 15:  # only adjust if >0.5s off
                            scale = target_frames / orig_total
                            scene_durations = [max(90, round(d * scale)) for d in scene_durations]
                            drift = target_frames - sum(scene_durations)
                            scene_durations[-2] = max(90, scene_durations[-2] + drift)
                            logger.step(f"Scene durations scaled: {orig_total}f → {sum(scene_durations)}f to match {audio_secs:.1f}s audio")
                    except Exception:
                        pass
                total_frames    = sum(scene_durations)
                words_json      = vo_path.replace(".mp3", "_words.json") if vo_path else None
                subtitle_chunks = _build_subtitle_chunks(words_json) if words_json and os.path.exists(words_json) else []
                if subtitle_chunks:
                    logger.step(f"Subtitles: {len(subtitle_chunks)} chunks ready for render")
                carousel_video_props = {
                    "slides":         [{"path": p} for p in slide_rel_paths],
                    "hook":           best_props.get("hook", ""),
                    "sceneDurations": scene_durations,
                    "totalFrames":    total_frames,
                    "subtitles":      subtitle_chunks,
                }
                cv_paths = skill_render(
                    carousel_video_props,
                    formats=["carousel_video_916"],
                    timestamp=ts,
                )
                carousel_video = cv_paths.get("carousel_video_916")
                if carousel_video:
                    logger.success(f"Carousel video: {os.path.basename(carousel_video)}")
                    if vo_path:
                        mixed_cv = skill_mix_audio(carousel_video, vo_path, timestamp=ts + "_cv")
                        if mixed_cv:
                            carousel_video = mixed_cv
                            logger.success(f"Carousel video + voice: {os.path.basename(carousel_video)}")
                    # Promote carousel video as the main 916 output for thumbnail + send
                    if not final_916:
                        final_916 = carousel_video
                        thumbnail = skill_extract_thumbnail(final_916, frame_sec=6.0) or thumbnail
                        logger.info("Carousel video promoted to main 9:16 output.")
            # Clean up temp slide copies after render
            import shutil as _shutil
            _shutil.rmtree(
                os.path.join(REMOTION_PROJECT, "public", f"carousel_temp_{ts}"),
                ignore_errors=True,
            )
        except Exception as e:
            logger.warning(f"Carousel video render failed (non-critical): {type(e).__name__}: {str(e)[:120]}")

    result = {
        "916":                final_916,
        "11":                 final_11,
        "thumbnail":          thumbnail,
        "props":              best_props,
        "variants":           variants,
        "timestamp":          ts,
        "carousel_916":       carousel_916,
        "carousel_11":        carousel_11,
        "carousel_video_916": carousel_video,
        # Post captions — carried through so skill_send_preview can build per-platform cards
        "instagram_caption":  posts.get("instagram_caption", ""),
        "tiktok_caption":     posts.get("tiktok_caption", ""),
        "linkedin_company":   posts.get("linkedin_company", ""),
        "hashtags_instagram": posts.get("hashtags_instagram", ""),
        "hashtags_tiktok":    posts.get("hashtags_tiktok", ""),
        "hashtags_linkedin":  posts.get("hashtags_linkedin", ""),
    }

    if result["916"]:
        logger.success(f"Video agent done. 9:16={os.path.basename(result['916'])}")
    else:
        logger.warning("Video agent: render skipped or failed. Props saved for later use.")

    return result
