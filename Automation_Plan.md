# Fincare Social Media Automation Plan
**aifincare.com | Minimum Cost, Maximum Control**
*Goal: Research → Write → Generate Image → Get Approval → Post — automatically*

---

## THE FULL PIPELINE (How It Works)

```
Step 1: RESEARCH
Claude searches trending finance/investing topics on social media

Step 2: WRITE
Claude writes 4 platform-specific posts (LinkedIn + Instagram + TikTok + Threads)
Following brand voice + character limits automatically

Step 3: GENERATE IMAGE
DALL-E 3 or Ideogram generates a branded visual for the post

Step 4: APPROVAL
Telegram Bot sends you a preview:
  → Post copy ✍️
  → Image 🖼️
  → Platform + time 📅
  You tap ✅ Approve or ❌ Reject

Step 5: SCHEDULE & POST
If approved → posts automatically at the optimal time
If rejected → sends back for revision

Step 6: REPEAT
Runs on a schedule (daily or weekly)
```

---

## TOOLS & APIS — WHAT YOU NEED

### Orchestration (The Brain)
| Tool | Cost | What It Does |
|---|---|---|
| **n8n** (self-hosted) | **FREE** (+ ~$4/mo VPS) | Connects everything. Runs the workflow. |
| **n8n** (cloud free tier) | **FREE** | Same but limited to 1 workflow + 200 runs/month |

**Recommendation:** Start on n8n cloud free. Migrate to self-hosted when needed.
→ Sign up: n8n.io

---

### Content Research + Writing (The Brain)
| Tool | Cost | What It Does |
|---|---|---|
| **Claude API** (Anthropic) | ~$5–15/month | Researches trends, writes all posts |
| **Serper API** (Google Search) | FREE (100 searches/month) | Finds trending topics |

→ Sign up: console.anthropic.com
→ Sign up: serper.dev

---

### Image Generation (The Creative)
| Tool | Cost | What It Does |
|---|---|---|
| **DALL-E 3** (OpenAI API) | ~$0.04/image (~$2–5/month) | Generates branded post images |
| **Ideogram API** | Free tier available | Better for text-in-image designs |
| **Stable Diffusion** (Replicate) | Pay per run (~$0.01/image) | Cheapest option |

**Recommendation:** Start with Ideogram (free tier) or DALL-E 3.
→ Sign up: ideogram.ai/api or platform.openai.com

---

### Approval Workflow (Your Control)
| Tool | Cost | What It Does |
|---|---|---|
| **Telegram Bot** | **FREE forever** | Sends you post previews with Approve/Reject buttons |

→ Create bot in 2 minutes: open Telegram → search @BotFather → /newbot

---

### Platform APIs (The Publishers)
| Platform | API | Cost | Status |
|---|---|---|---|
| **Instagram** | Meta Graph API | **FREE** | Requires App Review (1 week) |
| **Threads** | Threads API (Meta) | **FREE** | Same Meta app as Instagram ✅ |
| **LinkedIn** | LinkedIn Posts API | **FREE** | Requires Developer App approval |
| **TikTok** | TikTok Content API | **FREE** | Requires audit for PUBLIC posts |

**Important notes:**
- Instagram: Must be a Business or Creator account, linked to a Facebook Page
- Threads: Uses the exact same Meta developer app as Instagram — zero extra setup
- LinkedIn: Company page posting works well. Personal profile has restrictions.
- TikTok: Until you pass their audit, posts go out as **private**. Plan for 2–4 weeks for audit.

---

## TOTAL MONTHLY COST

| Item | Cost |
|---|---|
| n8n (cloud free) | $0 |
| Claude API | ~$5–15/month |
| Image generation (Ideogram free) | $0 |
| Image generation (DALL-E if needed) | ~$2–5/month |
| Telegram Bot | $0 |
| Instagram API | $0 |
| LinkedIn API | $0 |
| TikTok API | $0 |
| Serper API (100 searches free) | $0 |
| **TOTAL (minimum)** | **$5–20/month** |

**vs. Blotato: $49+/month | Buffer: $15+/month | Metricool: $18+/month**

---

## STEP-BY-STEP SETUP ORDER

### Phase 1 — Accounts & Keys (Week 1)
These don't cost anything. Just setup time.

**Step 1.1 — Meta Developer (Instagram)**
1. Go to developers.facebook.com
2. Create a developer account
3. Create a new App → choose "Business" type
4. Add "Instagram Graph API" product
5. Connect your Instagram Business account to a Facebook Page
6. Submit for App Review
7. *Estimated time: 1–2 hours setup + 1 week review*

**Step 1.2 — LinkedIn Developer App**
1. Go to linkedin.com/developers
2. Create a new App
3. Associate it with your Fincare company page
4. Request access to: "Share on LinkedIn", "Sign In with LinkedIn"
5. *Estimated time: 30 minutes + a few days for approval*

**Step 1.3 — TikTok Developer App**
1. Go to developers.tiktok.com
2. Create a developer account
3. Create an App → add "Content Posting API"
4. Submit for audit (for public posting)
5. *Estimated time: 30 minutes setup + 2–4 weeks for audit*
6. *In the meantime, you can still test with private posts*

**Step 1.4 — Telegram Bot**
1. Open Telegram → search @BotFather
2. Send /newbot → follow the steps
3. Name it: "Fincare Approval Bot"
4. Save the API token it gives you
5. *Estimated time: 5 minutes*

**Step 1.5 — Claude API**
1. Go to console.anthropic.com
2. Create account → generate API key
3. Add $10 credit to start (lasts ~1 month)
4. *Estimated time: 10 minutes*

**Step 1.6 — Image Generation**
1. Go to ideogram.ai → sign up → get API key (free tier)
2. OR go to platform.openai.com → generate API key (for DALL-E 3)
3. *Estimated time: 10 minutes*

---

### Phase 2 — n8n Workflow Setup (Week 2)
**Good news:** n8n already has a template for exactly this use case.
Search in n8n: *"Generate & schedule social media posts with Telegram approval workflow"*
We adapt it to use Claude instead of GPT-4 and add all 3 platforms.

**The workflow nodes (in order):**
```
1. TRIGGER (Scheduled - daily at 9am)
      ↓
2. RESEARCH NODE (Claude API + Serper)
   → Search trending finance topics
   → Pick the most relevant viral angle
      ↓
3. WRITE NODE (Claude API)
   → LinkedIn Version A (personal, 3,000 chars)
   → LinkedIn Version B (company page, 700 chars)
   → Instagram caption + 7 slide texts (2,200 chars)
   → TikTok caption + overlay script (2,200 chars)
   → Threads post or thread chain (500 chars/post)
      ↓
4. IMAGE GENERATION NODE (Ideogram/DALL-E)
   → Generate branded image for the post
      ↓
5. APPROVAL NODE (Telegram Bot)
   → Send you: post text + image preview + platform labels
   → Two buttons: ✅ Approve | ❌ Reject/Edit
      ↓
6a. IF APPROVED → SCHEDULE NODE
    → Posts at optimal time per platform
    → Instagram → 11am–1pm
    → LinkedIn → 8am–10am
    → TikTok → 7–9pm
      ↓
6b. IF REJECTED → REVISION NODE
    → Sends back to Claude with your feedback
    → Regenerates → sends for approval again
```

---

### Phase 3 — Testing (Week 2–3)
1. Test the research step alone
2. Test post writing (check character limits are respected)
3. Test Telegram approval flow
4. Test posting to one platform first (Instagram usually easiest)
5. Add LinkedIn, then TikTok

---

## WHAT I (AS YOUR SMM) WILL DO IN THIS SYSTEM

Each time the workflow runs, Claude will:

1. **Research** — Search what's trending in investing, behavioral finance, fintech, AI
2. **Select** — Pick the angle most aligned with Fincare's brand voice
3. **Write** — Draft all posts following the Brand Voice Guide + character limits
4. **Image brief** — Write the exact prompt for image generation
5. **Quality check** — Verify: correct character count, brand voice, no financial advice language, proper CTA, hashtags
6. **Send for approval** — You get a Telegram message with everything

**You only need to:**
- Tap ✅ or ❌ on your phone
- Add any notes if rejecting ("change the tone", "use different stat")
- That's it.

---

## HONEST LIMITATIONS TO KNOW

| Limitation | Impact | Workaround |
|---|---|---|
| TikTok audit required for public posts | Delay of 2–4 weeks before TikTok goes live | Post manually on TikTok while waiting |
| Instagram doesn't support Reels via API | Reels must be posted manually | Use API for carousels, post Reels manually |
| LinkedIn personal profile API is restricted | Company page works fine, personal is harder | Post company page via API, personal manually |
| n8n free cloud = 1 workflow only | Can't split into multiple workflows | Build everything into one smart workflow |
| Image generation may need prompting | First images may not be perfect | We refine the image prompt over first 2 weeks |

---

## PHASE SUMMARY

| Phase | Timeline | Cost | What Gets Done |
|---|---|---|---|
| Phase 1: Setup accounts | Week 1 | $0 | All API accounts + keys created |
| Phase 2: Build workflow | Week 2 | $0 | n8n workflow built and connected |
| Phase 3: Testing | Week 2–3 | ~$5 (API test calls) | All platforms tested, bugs fixed |
| Phase 4: Live | Week 3–4 | $5–20/month | Full automation running |

---

## NEXT STEP

Before building anything, the accounts need to be set up first.

**Start here — in this order:**
1. Meta Developer App (Instagram) → highest priority, longest review time
2. LinkedIn Developer App → second priority
3. Telegram Bot → 5 minutes, do it today
4. Anthropic Claude API → 10 minutes
5. Ideogram API → 10 minutes
6. TikTok → start the audit process early (it takes longest)

---

*Once Phase 1 is done — share the API keys and we build the workflow in n8n together.*
