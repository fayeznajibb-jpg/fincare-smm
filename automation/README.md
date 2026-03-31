# Fincare SMM Automation
**aifincare.com — Automated Social Media Pipeline**
`Research → Write → Telegram Approval → Post`
Cost: $0/month (GitHub Actions free tier)

---

## HOW IT WORKS

1. GitHub Actions triggers the pipeline every weekday at 8am UTC
2. Claude researches a trending investing topic
3. Claude writes posts for LinkedIn, Threads, and TikTok (following Fincare brand voice)
4. You receive a Telegram message with a preview of all posts
5. You tap ✅ APPROVE or ❌ REJECT on your phone
6. If approved → posts automatically to LinkedIn + Threads
7. TikTok draft is saved locally (requires video via Remotion — Phase 2)

---

## SETUP

### 1. Clone and push to GitHub
```bash
git init
git add .
git commit -m "Initial Fincare SMM automation"
git remote add origin https://github.com/YOUR_USERNAME/fincare-smm
git push -u origin main
```

### 2. Add GitHub Secrets
Go to: GitHub repo → Settings → Secrets and variables → Actions → New secret

Add each of these:
| Secret Name | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | @userinfobot on Telegram |
| `LINKEDIN_ACCESS_TOKEN` | linkedin.com/developers |
| `LINKEDIN_PERSON_ID` | LinkedIn API: GET /v2/userinfo |
| `LINKEDIN_ORGANIZATION_ID` | Your LinkedIn company page URL |
| `THREADS_ACCESS_TOKEN` | Meta developer app |
| `THREADS_USER_ID` | Meta developer app |

### 3. Test it manually
Go to GitHub repo → Actions → "Fincare Daily SMM Automation" → Run workflow

---

## FILE STRUCTURE
```
automation/
├── .github/workflows/daily_smm.yml   # GitHub Actions scheduler
├── src/
│   ├── main.py          # Orchestrator
│   ├── researcher.py    # Trend research (Claude API)
│   ├── writer.py        # Post writing (Claude API + brand voice)
│   ├── telegram_bot.py  # Approval workflow
│   └── publisher.py     # Post to all platforms
├── utils/
│   ├── logger.py        # Secure logging (masks secrets)
│   └── validators.py    # Character limits + input validation
├── prompts/
│   ├── brand_voice.txt  # Fincare brand voice guide
│   └── research_prompt.txt
├── drafts/              # Auto-saved post backups (gitignored)
├── logs/                # Daily logs (gitignored)
├── .env.example         # Template — copy to .env locally
└── requirements.txt
```

---

## SECURITY
- All API keys stored as GitHub Secrets — never in code
- Logs automatically mask any sensitive data
- Telegram approval verifies your Chat ID before accepting any response
- Session IDs prevent replay attacks on approval callbacks
- Content is sanitized before posting

---

## MONTHLY COST
| Service | Cost |
|---|---|
| GitHub Actions | $0 (free tier: 2,000 min/month) |
| Claude API | ~$5–15/month |
| All platform APIs | $0 |
| **Total** | **~$5–15/month** |

---

## PHASE 2 — COMING SOON
- Remotion video generation for TikTok + Instagram Reels
- Instagram carousel auto-posting
- Instagram/Meta integration (pending account access)
