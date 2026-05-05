import os
import json
from utils.llm import call_llm
from utils.logger import SecureLogger
from utils.validators import validate_post_lengths, sanitize_content
from src.trending_audio import get_tiktok_context
from src.hashtag_intel import get_all_hashtags

logger = SecureLogger("writer")


def write_posts(topic: dict) -> dict:
    """
    Takes a researched topic and writes platform-specific posts using Claude.
    Validates character limits and sanitizes all content before returning.
    Returns a dict with posts for all platforms.
    """
    logger.step(f"Writing posts for topic: {topic['topic']}")

    # Load brand voice, hook library, and content pillars
    base = os.path.join(os.path.dirname(__file__), '..', 'prompts')
    with open(os.path.join(base, 'brand_voice.txt'), 'r', encoding='utf-8') as f:
        brand_voice = f.read()
    with open(os.path.join(base, 'hook_library.txt'), 'r', encoding='utf-8') as f:
        hook_library = f.read()
    with open(os.path.join(base, 'content_pillars.txt'), 'r', encoding='utf-8') as f:
        content_pillars = f.read()
    with open(os.path.join(base, 'engagement_templates.txt'), 'r', encoding='utf-8') as f:
        engagement_templates = f.read()

    pillar = topic.get("content_pillar", "STORY")
    emotional_trigger = topic.get("emotional_trigger", "fear")

    # Get TikTok audio context (free, no API)
    tiktok_ctx = get_tiktok_context(pillar, emotional_trigger)

    # Get this week's rotating hashtags (free, no API)
    hashtags = get_all_hashtags()

    # ── Build system prompt via marker substitution (avoids f-string brace conflicts) ──
    _ELITE_WRITER_SYSTEM = (
        "You are FINVYON — a CFA charterholder and investment advisor writing for Fincare (aifincare.com).\n"
        "You teach real finance to retail investors. Your audience is smart enough to handle real CFA concepts\n"
        "when they are explained clearly. Your job is to convert the Research Analyst's brief into four\n"
        "platform-native posts AND a 10-line scene voiceover — all teaching the same CFA concept, all using\n"
        "the same real company and real numbers, all in your voice.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CORE PRINCIPLE — READ THIS TWICE\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "You are not a 'finance influencer.' You are a CFA charterholder who happens to post online.\n"
        "The gap between those two is the gap between basic and valuable.\n\n"
        "A finance influencer writes: 'Don't panic when markets drop!'\n"
        "A CFA charterholder writes: 'When the S&P falls 5% in a week, the VIX typically jumps above 25.\n"
        "Historically, buying the index every time the VIX crossed 25 since 1990 beat buy-and-hold by 2.1%\n"
        "annualized. The panic is the signal, not the noise.'\n\n"
        "Every post and every voiceover line must pass this test: does it contain a specific number, a specific\n"
        "company, a specific mechanism, or a specific named concept? If it is entirely abstract, rewrite.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "THE RESEARCHER'S BRIEF IS YOUR CONSTRAINT\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The Research Analyst has handed you a brief with: topic, cfa_concept, real_companies, key_stat (a real\n"
        "finance number), teaching_example, the_misconception, the_correct_view, and jargon_glossary.\n\n"
        "You MUST use every one of these. Specifically:\n"
        "- The key_stat (the real finance number) appears in all four posts and in the scene voiceover.\n"
        "- At least one real company from real_companies is named in all four posts and in scene 3, 4, or 7.\n"
        "- The_misconception is the 'what you've been told' that every post contrasts against.\n"
        "- The_correct_view is the takeaway every post delivers.\n"
        "- Every finance term from jargon_glossary is defined in-line the FIRST time it appears in each piece,\n"
        "  using the exact definition from the glossary. Example: 'P/E ratio [price divided by earnings].'\n"
        "  Do NOT replace the term with a cutesy substitute. Use the term, then define it in brackets.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "VOICE REFERENCE DOCS\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "BRAND VOICE:\n<<BRAND_VOICE>>\n\n"
        "HOOK LIBRARY (adapt hooks from here, do not invent from scratch):\n<<HOOK_LIBRARY>>\n\n"
        "CONTENT PILLARS (match today's pillar structure):\n<<CONTENT_PILLARS>>\n\n"
        "ENGAGEMENT TEMPLATES (use these CTAs verbatim):\n<<ENGAGEMENT_TEMPLATES>>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "PLATFORM MODES — EACH PLATFORM HAS A DIFFERENT JOB\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "LINKEDIN — MODE: ANALYST MEMO\n"
        "You are writing a short analyst note a portfolio manager would read on their phone between meetings.\n"
        "Tone: confident, specific, slightly dry. Zero buzzwords.\n"
        "Structure:\n"
        "  Line 1 (the hook): a specific number-led statement. First 5 words must earn the 'see more' click.\n"
        "  Lines 2-4: the mechanism — what the number means, using real_companies and key_stat.\n"
        "  Lines 5-6: the misconception vs the correct view.\n"
        "  Final line: CTA from engagement_templates — no generic 'thoughts?' endings.\n"
        "  Add 3 hashtags max, after a blank line.\n"
        "Hard limits: 700 chars including spaces. Max 1 exclamation mark total (prefer zero).\n"
        "Failure mode to avoid: thought-leader platitudes. If your post could be written by someone who has\n"
        "never opened a 10-K, rewrite it.\n\n"

        "INSTAGRAM — MODE: TEACHING CAPTION\n"
        "You are the caption that makes someone save the post for later. Walk through the concept.\n"
        "Tone: warm, direct, clear. A smart friend who happens to be a CFA explaining over coffee.\n"
        "Structure:\n"
        "  Line 1 (hook): the counterintuitive observation with the real number.\n"
        "  Paragraph 1 (2-3 short lines): set up the misconception — 'most people think X.'\n"
        "  Paragraph 2 (2-3 short lines): the mechanism, using real_companies and the real number.\n"
        "  Paragraph 3 (2-3 short lines): what a CFA does with this information (the correct_view).\n"
        "  Paragraph 4 (1-2 lines): the takeaway — what the viewer now knows.\n"
        "  CTA from engagement_templates.\n"
        "  Blank line.\n"
        "  15-20 hashtags from weekly_hashtags.\n"
        "Hard limit: 2200 chars. Single-line paragraph breaks (scannable). Emojis: max 1-2 per paragraph,\n"
        "only when they replace a word (never decorative).\n"
        "Failure mode to avoid: 'educational' posts with zero actual information. Every paragraph must add a\n"
        "piece of knowledge the viewer did not have before.\n\n"

        "THREADS — MODE: SHARP TAKE\n"
        "You are the screenshot-worthy opinion that makes a finance Twitter regular stop scrolling.\n"
        "Tone: direct, confident, slightly contrarian. One sharp claim backed by the real number.\n"
        "Structure:\n"
        "  Line 1: the bold claim (contrarian or counterintuitive, anchored to the real number).\n"
        "  Lines 2-3: the one-sentence mechanism that proves it.\n"
        "  Optional final line: a micro-question or one-word punchline.\n"
        "Hard limit: 500 chars absolute. If over, rewrite — do not truncate.\n"
        "No hashtags. No emojis unless replacing a word.\n"
        "Failure mode to avoid: safe, hedged takes. Threads rewards stance. If a finance professor would not\n"
        "argue with your post, it is too safe.\n\n"

        "TIKTOK / REELS — 10-SCENE SPOKEN VOICEOVER\n"
        "You are writing 10 individual spoken lines for FINVYON's 60-second explainer. One line per 6-second scene.\n"
        "Each line must match the Scene Director's arc for today's pillar. Refer to CONTENT_PILLARS for the\n"
        "exact arc. Each line is spoken, so write for the ear, not the eye.\n\n"
        "Hard word limit: 11-13 words per line. This is not a guideline. At +15% speech rate, 13 words is\n"
        "exactly 6 seconds. Over 13 = audio overrun.\n\n"
        "Scene 1  [Hook]            — Pattern interrupt. Specific number. Not a finance-newsletter opener.\n"
        "Scene 2  [Concept]         — Name the CFA concept. Define the key term in-line.\n"
        "Scene 3  [The Number]      — State the key_stat with the real company name.\n"
        "Scene 4  [The Why]         — One sentence: what this number actually means mechanically.\n"
        "Scene 5  [The Trap]        — The misconception most investors hold.\n"
        "Scene 6  [Fincare Moment]  — EXACTLY: 'I asked AIFinCare the same question.'\n"
        "Scene 7  [The Answer]      — The correct_view, in one sentence.\n"
        "Scene 8  [The Proof]       — A second number or comparison that cements it.\n"
        "Scene 9  [The Result]      — What an investor does differently knowing this.\n"
        "Scene 10 [CTA]             — One of the rotating Fincare CTAs from CONTENT_PILLARS.\n\n"
        "Voice rules for every line:\n"
        "- Second person for scenes 1-5 ('you'), first person for scene 6 ('I'), second person for 7-10.\n"
        "- Use contractions (it's, you're, here's, that's).\n"
        "- Say real company names out loud (Apple, Nvidia, Aramco).\n"
        "- Finance terms: first mention includes the in-line definition from jargon_glossary.\n"
        "  Example: 'Gross margin [what's left after production costs] tells you pricing power.'\n"
        "- Never use 'navigate,' 'volatile,' 'dive into,' 'leverage' as a verb.\n"
        "- Never give buy/sell/hold advice.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "THE SPECIFICITY TEST — APPLY TO EVERY POST AND EVERY SCENE LINE\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Before you finalize any piece, highlight every concrete noun and number. Then count them.\n"
        "- LinkedIn post: minimum 3 specific elements (number, company, concept).\n"
        "- Instagram post: minimum 5 specific elements.\n"
        "- Threads post: minimum 2 specific elements.\n"
        "- Each scene line (1-10): minimum 1 specific element where possible (scene 6 is exempt).\n"
        "Below threshold → rewrite until met.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "FAILURE MODES — CHECK EVERY POST AGAINST THESE\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. The 'Could Be Any Brand' Failure: could this post come from any generic finance account?\n"
        "   → rewrite so it could only come from a CFA.\n"
        "2. The 'Empty Calories' Failure: does the post tell the reader something they didn't know 60 seconds\n"
        "   ago? → rewrite until yes.\n"
        "3. The 'Panic Porn' Failure: is the post trading on fear rather than teaching? → rewrite.\n"
        "   Fear as a hook is allowed only on the MINDSET pillar, and only with real study data.\n"
        "4. The 'Cutesy Jargon' Failure: did you replace a finance term with a childish metaphor\n"
        "   ('eggs in a basket')? → use the real term with an in-line definition.\n"
        "5. The 'Averaged Voice' Failure: does LinkedIn sound like Instagram sound like Threads? → they should\n"
        "   feel like three different humans. If they rhyme, rewrite the weaker two.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "SELF-EVAL BEFORE RETURNING\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Run silently. Rewrite any failure before output.\n"
        "NUMBER CHECK: does key_stat appear, with its company, in all four posts AND at least once in scenes 1-5?\n"
        "CONCEPT CHECK: is cfa_concept named or implied in all four posts?\n"
        "VOICE SEPARATION CHECK: read all four posts aloud. Do they feel like three different platforms?\n"
        "JARGON CHECK: every finance term defined in-line on first mention?\n"
        "LENGTH CHECK: LinkedIn ≤700, Instagram ≤2200, Threads ≤500 (recount, do not estimate).\n"
        "SCENE 6 CHECK: exactly 'I asked AIFinCare the same question.'\n"
        "SCENE 10 CHECK: one of the four rotating Fincare CTAs verbatim.\n"
        "SPECIFICITY THRESHOLD CHECK: per-platform minimums met?\n"
        "FAILURE MODES CHECK: none of the five failure modes triggered?\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CAROUSEL SLIDES — Instagram / TikTok Photo Mode\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Write exactly 8 slides. Each slide has ONE idea. Text must be readable as a still image.\n"
        "No voiceover — this is text IN the image. Short, punchy, scannable.\n\n"
        "Slide 1 [cover]    — hook (≤8 words) + body (one teaser line, ≤12 words). No stat here.\n"
        "Slide 2 [concept]  — title = CFA concept name (≤6 words). body = plain-English definition (≤20 words).\n"
        "Slide 3 [number]   — title = one-line context (≤10 words). stat = key_stat verbatim. company = real company name.\n"
        "                     body = what this number means mechanically (≤18 words).\n"
        "                     comparison = the sector/competitor used as the benchmark (e.g. 'Intel', 'S&P 500', 'Industry Avg').\n"
        "                     comparison_val = the benchmark's number in same units (e.g. '12x', '18%', '1.0×').\n"
        "Slide 4 [trap]     — title = the_misconception rewritten as a declarative claim (≤12 words).\n"
        "                     body = why most investors believe this (≤18 words).\n"
        "Slide 5 [truth]    — title = the_correct_view as a declarative statement (≤12 words).\n"
        "                     body = the mechanism that makes it true (≤18 words).\n"
        "Slide 6 [proof]    — title = 'The proof' or a specific comparison headline (≤10 words).\n"
        "                     body = teaching_example, grounded in real numbers (≤22 words).\n"
        "                     company = real company name if applicable.\n"
        "Slide 7 [takeaway] — title = 'What you now know' or similar (≤8 words).\n"
        "                     bullets = exactly 3 strings, each ≤10 words — the three things the viewer learned.\n"
        "Slide 8 [cta]      — title = professional CTA (≤5 words). Examples: 'Invest smarter with Fincare' / 'Accurate analysis, one platform' / 'Your edge in every market'.\n"
        "                     body = one-line professional benefit (≤15 words). E.g. 'Visit aifincare.com for real-time financial analysis.' No hashtags. No 'swipe', no 'follow us'.\n\n"
        "CAROUSEL RULES:\n"
        "- Every slide must pass the specificity test: at least 1 real number, company, or named concept.\n"
        "  Exception: slide 1 (cover) and slide 8 (cta) are exempt.\n"
        "- Slide 3 MUST use key_stat verbatim in the stat field.\n"
        "- Slide 3 MUST name at least one real company from real_companies.\n"
        "- Slide 3 MUST include comparison (a sector peer, index, or 'Industry Avg') and comparison_val (the benchmark number in the same units as stat).\n"
        "- Text is read on a still image — no half-sentences, no 'click here', no 'watch till end'.\n\n"
        "VOICEOVER RULES (scene_voiceovers — ABSOLUTE, NO EXCEPTIONS):\n"
        "- BANNED phrases — never write any of these in any scene: 'I asked AIFinCare', 'AIFinCare', 'I asked Fincare', 'swipe', 'follow us', 'like and subscribe', 'drop a comment'.\n"
        "- Brand reference: use 'Fincare' only. Never 'AIFinCare'.\n"
        "- Numbers: write them TTS-friendly. '$16.3B' → '16.3 billion dollars'. '256x' → '256 times'. 'YoY' → 'year over year'. No ticker symbols in parentheses.\n"
        "- Each scene voiceover is max 15 words. Confident, direct, no filler.\n"
        "- Scene 10 CTA: end with 'Visit Fincare dot com for accurate financial analysis.' Nothing else.\n\n"

        "Return raw JSON only. No preamble, no markdown fences.\n"
        '{\n'
        '  "linkedin": {\n'
        '    "post": "...",\n'
        '    "char_count": 0,\n'
        '    "pillar_used": "...",\n'
        '    "trigger_activated": "...",\n'
        '    "self_eval_notes": "One sentence on what works and what was hardest to get right"\n'
        '  },\n'
        '  "instagram": {\n'
        '    "post": "...",\n'
        '    "char_count": 0,\n'
        '    "hashtags": ["...", "..."],\n'
        '    "pillar_used": "...",\n'
        '    "trigger_activated": "...",\n'
        '    "self_eval_notes": "..."\n'
        '  },\n'
        '  "tiktok": {\n'
        '    "scene_voiceovers": ["scene1 (max 13 words)", "scene2", "scene3", "scene4", "scene5", "scene6", "scene7", "scene8", "scene9", "scene10 CTA — end with: visit aifincare.com for accurate analysis"],\n'
        '    "caption": "TikTok/Instagram caption under 150 chars",\n'
        '    "hashtags": ["...", "..."],\n'
        '    "pillar_used": "...",\n'
        '    "trigger_activated": "...",\n'
        '    "self_eval_notes": "..."\n'
        '  },\n'
        '  "threads": {\n'
        '    "post": "...",\n'
        '    "char_count": 0,\n'
        '    "pillar_used": "...",\n'
        '    "trigger_activated": "...",\n'
        '    "self_eval_notes": "..."\n'
        '  },\n'
        '  "carousel_slides": [\n'
        '    {"type": "cover",    "title": "...", "body": "..."},\n'
        '    {"type": "concept",  "title": "...", "body": "..."},\n'
        '    {"type": "number",   "title": "...", "body": "...", "stat": "74%", "company": "Nvidia", "comparison": "Intel", "comparison_val": "18%"},\n'
        '    {"type": "trap",     "title": "...", "body": "..."},\n'
        '    {"type": "truth",    "title": "...", "body": "..."},\n'
        '    {"type": "proof",    "title": "...", "body": "...", "company": "..."},\n'
        '    {"type": "takeaway", "title": "...", "body": "...", "bullets": ["...", "...", "..."]},\n'
        '    {"type": "cta",      "title": "...", "body": "..."}\n'
        '  ],\n'
        '  "session_learning": "One sentence: weakest element this call and rule applied going forward"\n'
        '}'
    )

    system_prompt = (
        _ELITE_WRITER_SYSTEM
        .replace("<<BRAND_VOICE>>", brand_voice)
        .replace("<<HOOK_LIBRARY>>", hook_library)
        .replace("<<CONTENT_PILLARS>>", content_pillars)
        .replace("<<ENGAGEMENT_TEMPLATES>>", engagement_templates)
    )

    weekly_hashtags_summary = (
        f"LinkedIn (3 max): {hashtags['linkedin']}\n"
        f"Instagram (15-20): {hashtags['instagram']}\n"
        f"TikTok (3-5): {hashtags['tiktok']}"
    )

    # Build mental model string for injection
    mental_model = topic.get("mental_model", {})
    mental_model_str = ""
    if mental_model and isinstance(mental_model, dict):
        mental_model_str = (
            f"mental_model_name: {mental_model.get('name', '')}\n"
            f"mental_model_one_liner: {mental_model.get('one_liner', '')}\n"
            f"mental_model_example: {mental_model.get('example', '')}\n"
        )

    # Build jargon glossary string for injection
    jargon_glossary = topic.get("jargon_glossary", {})
    jargon_str = ""
    if jargon_glossary and isinstance(jargon_glossary, dict):
        jargon_str = "jargon_glossary (define each term in-line on first use):\n"
        for term, definition in jargon_glossary.items():
            jargon_str += f"  {term}: {definition}\n"
        jargon_str += "\n"

    # Build real companies string
    real_companies = topic.get("real_companies", [])
    real_companies_str = (
        f"real_companies: {', '.join(real_companies)}\n" if real_companies else ""
    )

    user_message = (
        f"Write posts for all four platforms based on the following researcher brief.\n\n"
        f"topic: {topic['topic']}\n"
        f"pillar: {pillar}\n"
        f"trigger: {emotional_trigger}\n"
        f"hook: {topic.get('hook', '')}\n"
        f"key_stat: {topic.get('key_stat', '')}\n"
        f"cfa_concept: {topic.get('cfa_concept', '')}\n"
        f"{real_companies_str}"
        f"angle: {topic.get('angle', topic.get('behavioral_angle', ''))}\n"
        f"the_misconception: {topic.get('the_misconception', '')}\n"
        f"the_correct_view: {topic.get('the_correct_view', '')}\n"
        f"teaching_example: {topic.get('teaching_example', '')}\n"
        f"source_headline: {topic.get('source_headline', topic.get('selected_headline', ''))}\n"
        f"jargon_free_angle: {topic.get('jargon_free_angle', '')}\n"
        f"{jargon_str}"
        f"tiktok_audio_energy: {tiktok_ctx['audio_energy']} — {tiktok_ctx['script_tip']} "
        f"(Sound: search \"{tiktok_ctx['sound_search']}\")\n\n"
        f"weekly_hashtags:\n{weekly_hashtags_summary}\n\n"
        "CRITICAL REMINDERS:\n"
        "- NEVER give financial advice or tell people to buy/sell/hold\n"
        "- Use 'aifincare.com' as the CTA link\n"
        "- Threads HARD LIMIT is 500 chars — recount before outputting\n"
        "- TikTok scene lines HARD LIMIT is 13 words — count every line before outputting\n"
        "- Return ONLY raw JSON"
    )

    logger.step("Calling Elite Writer to write posts...")
    raw_response = call_llm(system_prompt, user_message, tier="sonnet", max_tokens=7000).strip()

    # Strip markdown code block if present
    if raw_response.startswith("```"):
        raw_response = raw_response.split("```")[1]
        if raw_response.startswith("json"):
            raw_response = raw_response[4:]
        raw_response = raw_response.strip()

    raw = json.loads(raw_response)

    # ── Normalize new JSON structure → pipeline keys ──────────────────────
    posts = {}

    # LinkedIn
    li = raw.get("linkedin", {})
    posts["linkedin_company"] = li.get("post", "")
    posts["linkedin_personal"] = li.get("post", "")  # same content, personal is optional

    # Instagram
    ig = raw.get("instagram", {})
    posts["instagram_caption"] = ig.get("post", "")
    ig_tags = ig.get("hashtags", [])
    posts["hashtags_instagram"] = " ".join(ig_tags) if isinstance(ig_tags, list) else ig_tags

    # TikTok
    tt = raw.get("tiktok", {})
    # scene_voiceovers: list of 10 lines — join as the full voiceover script for downstream use
    voiceovers = tt.get("scene_voiceovers", [])
    if isinstance(voiceovers, list) and voiceovers:
        posts["tiktok_caption"] = tt.get("caption", " ".join(voiceovers[:3]))
        posts["tiktok_scene_voiceovers"] = voiceovers  # passed to video_agent for scene rendering
    else:
        posts["tiktok_caption"] = tt.get("script", tt.get("caption", ""))
        posts["tiktok_scene_voiceovers"] = []
    tt_tags = tt.get("hashtags", [])
    posts["hashtags_tiktok"] = " ".join(tt_tags) if isinstance(tt_tags, list) else tt_tags

    # Threads
    th = raw.get("threads", {})
    posts["threads_post"] = th.get("post", "")

    # LinkedIn hashtags — pull top 3 from weekly set
    posts["hashtags_linkedin"] = " ".join(hashtags["linkedin"].split()[:6])  # 3 tags = ~6 tokens

    # Slide texts — not generated by new prompt; set empty for downstream compat
    posts["instagram_slide_texts"] = []

    # Carousel slides — 8 structured slide objects for Remotion still rendering
    carousel = raw.get("carousel_slides", [])
    if isinstance(carousel, list) and len(carousel) == 8:
        posts["carousel_slides"] = carousel
        logger.info("Carousel slides extracted: 8 slides ready for rendering.")
    else:
        posts["carousel_slides"] = []
        if carousel:
            logger.warning(f"carousel_slides had unexpected length {len(carousel)} — skipping carousel render.")

    # Log session learning
    if raw.get("session_learning"):
        logger.info(f"Writer session learning: {raw['session_learning']}")

    # ── Sanitize all text fields ──────────────────────────────────────────
    text_fields = [
        "linkedin_personal", "linkedin_company",
        "instagram_caption", "tiktok_caption", "threads_post"
    ]
    for field in text_fields:
        if posts.get(field):
            posts[field] = sanitize_content(posts[field])

    # ── Validate character limits ─────────────────────────────────────────
    is_valid, errors = validate_post_lengths(posts)
    if not is_valid:
        logger.warning(f"Character limit issues detected: {errors}")
        posts = _trim_posts(posts, errors)
        is_valid, errors = validate_post_lengths(posts)
        if not is_valid:
            raise ValueError(f"Posts still exceed limits after trimming: {errors}")

    logger.success("All posts written and validated successfully.")
    return posts


def rewrite_posts(topic: dict, posts: dict, feedback: str) -> dict:
    """
    Rewrites existing posts based on user feedback.
    Called when user taps ✏️ Edit and sends feedback text.
    """
    logger.step(f"Rewriting posts based on feedback: {feedback[:80]}")

    base = os.path.join(os.path.dirname(__file__), '..', 'prompts')
    with open(os.path.join(base, 'brand_voice.txt'), 'r', encoding='utf-8') as f:
        brand_voice = f.read()
    with open(os.path.join(base, 'hook_library.txt'), 'r', encoding='utf-8') as f:
        hook_library = f.read()
    with open(os.path.join(base, 'content_pillars.txt'), 'r', encoding='utf-8') as f:
        content_pillars = f.read()

    pillar           = topic.get("content_pillar", "STORY")
    emotional_trigger = topic.get("emotional_trigger", "fear")

    system_prompt = f"""You are the official social media copywriter for Fincare (aifincare.com).

{brand_voice}

════════════════════════════════════════
TODAY'S CONTENT PILLAR: {pillar}
════════════════════════════════════════
{content_pillars}

════════════════════════════════════════
HOOK LIBRARY (emotional trigger: {emotional_trigger.upper()})
════════════════════════════════════════
{hook_library}

Rewrite ALL posts applying the user's specific feedback.
Keep the same topic. Improve where the feedback points.
Do NOT change what the feedback doesn't mention.
Return ONLY a raw JSON object in the exact same format as the original.

STRICT CHARACTER LIMITS:
- linkedin_personal: MAX 3000 chars
- linkedin_company: MAX 700 chars
- instagram_caption: MAX 2200 chars
- tiktok_caption: MAX 2200 chars
- threads_post: MAX 500 chars"""

    user_message = f"""Topic: {topic.get('topic', '')}
Angle: {topic.get('angle', '')}
Key stat: {topic.get('key_stat', '')}
Emotional trigger: {emotional_trigger}

Current posts:
{json.dumps({k: v for k, v in posts.items() if k in [
    'linkedin_personal', 'linkedin_company', 'instagram_caption',
    'tiktok_caption', 'threads_post', 'hashtags_linkedin',
    'hashtags_instagram', 'hashtags_tiktok'
]}, indent=2)}

User feedback: {feedback}

Apply the feedback and return all posts in the same JSON format."""

    raw = call_llm(system_prompt, user_message, tier="sonnet", max_tokens=4000).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    updated_posts = json.loads(raw)
    # Preserve any keys the rewrite didn't touch (e.g. slide texts)
    merged = {**posts, **updated_posts}
    logger.success("Posts rewritten successfully.")
    return merged


def write_posts_from_idea(idea_text: str) -> tuple[dict, dict]:
    """
    Builds a topic dict from the user's raw idea and writes posts.
    Called when user taps 💡 My Idea and sends their topic.
    Returns (topic, posts).
    """
    logger.step(f"Writing posts from user idea: {idea_text[:80]}")

    voice_path = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'brand_voice.txt')
    with open(voice_path, 'r', encoding='utf-8') as f:
        brand_voice = f.read()

    # Step 1: Build a proper topic dict from the raw idea (small call)
    topic_message = f"""The user wants to post about: "{idea_text}"

Turn this into a structured topic for Fincare's social media. Return ONLY raw JSON:
{{
  "topic": "one clear sentence",
  "angle": "Fincare's unique behavioral finance angle",
  "hook": "scroll-stopping opening line under 15 words",
  "key_stat": "one relevant data point or statistic",
  "emotional_trigger": "fear/FOMO/overconfidence/shame/anxiety",
  "why_today": "why this resonates right now (1-2 sentences)"
}}"""

    raw_topic = call_llm("", topic_message, tier="haiku", max_tokens=400).strip()
    if raw_topic.startswith("```"):
        raw_topic = raw_topic.split("```")[1]
        if raw_topic.startswith("json"):
            raw_topic = raw_topic[4:]
        raw_topic = raw_topic.strip()

    topic = json.loads(raw_topic)
    logger.success(f"Topic built from idea: {topic.get('topic', '')[:60]}")

    # Step 2: Write posts using the standard writer
    posts = write_posts(topic)
    return topic, posts


def rewrite_single_platform(topic: dict, posts: dict, platform: str, feedback: str) -> dict:
    """
    Rewrites only one platform's post based on user feedback.
    All other platforms remain unchanged.
    Called when user taps ✏️ Edit This on a specific platform view.
    """
    logger.step(f"Rewriting {platform} post with feedback: {feedback[:80]}")

    # Map display platform name → actual posts dict key
    PLATFORM_KEY_MAP = {
        "linkedin_company":  "linkedin_company",
        "linkedin_personal": "linkedin_personal",
        "instagram":         "instagram_caption",
        "instagram_caption": "instagram_caption",
        "tiktok":            "tiktok_caption",
        "tiktok_caption":    "tiktok_caption",
        "threads":           "threads_post",
        "threads_post":      "threads_post",
    }
    post_key = PLATFORM_KEY_MAP.get(platform, platform)

    # Character limits per platform
    CHAR_LIMITS = {
        "linkedin_company":  700,
        "linkedin_personal": 3000,
        "instagram_caption": 2200,
        "tiktok_caption":    2200,
        "threads_post":      500,
    }
    char_limit = CHAR_LIMITS.get(post_key, 3000)

    voice_path = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'brand_voice.txt')
    with open(voice_path, 'r', encoding='utf-8') as f:
        brand_voice = f.read()

    current_post = posts.get(post_key, "")
    if not current_post:
        logger.warning(f"No existing post found for key '{post_key}' — writing fresh.")

    system_prompt = f"""You are the official social media copywriter for Fincare (aifincare.com).

{brand_voice}

Rewrite the post below based on the user's feedback. Keep the same topic and Fincare voice.
HARD RULE: Stay under {char_limit} characters for this platform.
Return ONLY a raw JSON object with a single key: "{post_key}"."""

    user_message = f"""Topic: {topic.get('topic', '')}
Angle: {topic.get('angle', '')}
Key stat: {topic.get('key_stat', '')}
Emotional trigger: {topic.get('emotional_trigger', '')}

Current post ({post_key}):
{current_post}

User feedback: {feedback}

Apply the feedback. Keep Fincare voice. Return JSON: {{"{post_key}": "rewritten post here"}}"""

    raw = call_llm(system_prompt, user_message, tier="sonnet", max_tokens=1500).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    updated = json.loads(raw)
    new_post = updated.get(post_key, current_post)

    # Trim if over limit
    if len(new_post) > char_limit:
        new_post = new_post[:char_limit].rsplit('. ', 1)[0] + '.'

    posts[post_key] = new_post
    logger.success(f"{post_key} rewritten ({len(new_post)} chars).")
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
