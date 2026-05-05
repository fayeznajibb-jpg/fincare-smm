"""
Fincare Topic Researcher
========================
Step 1: Fetches real headlines from RSS feeds (Yahoo Finance, CNBC, MarketWatch, FT)
Step 2: Uses Claude to pick the most relevant topic and generate a Fincare-specific angle,
        real behavioral insight, and current context.
"""

import os
import json
import random
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("researcher")

# Free RSS feeds — no API key needed
RSS_FEEDS = [
    # S&P 500 & broad market
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^DJI&region=US&lang=en-US",
    # Reuters
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/companyNews",
    # CNBC
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://www.cnbc.com/id/15839069/device/rss/rss.html",   # CNBC earnings
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
    # MarketWatch
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    # Investing.com
    "https://www.investing.com/rss/news.rss",
]

# ─── Company rotation — one real company featured per day ────────────────────
# Rotated by day-of-year so a different company appears each day without repeating
COMPANY_ROTATION = [
    "AAPL",    # Apple
    "NVDA",    # Nvidia
    "TSLA",    # Tesla
    "AMZN",    # Amazon
    "MSFT",    # Microsoft
    "GOOGL",   # Alphabet / Google
    "META",    # Meta
    "NFLX",    # Netflix
    "JPM",     # JPMorgan Chase
    "BRK-B",   # Berkshire Hathaway
    "2222.SR", # Saudi Aramco (Arab audience relevance)
    "V",       # Visa
    "MA",      # Mastercard
    "BABA",    # Alibaba
    "TSM",     # TSMC
]

# Known ticker keywords in headlines — used for NEWS pillar company detection
HEADLINE_TICKERS = {
    "apple": "AAPL", "nvidia": "NVDA", "tesla": "TSLA", "amazon": "AMZN",
    "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL", "meta": "META",
    "netflix": "NFLX", "jpmorgan": "JPM", "jp morgan": "JPM", "berkshire": "BRK-B",
    "aramco": "2222.SR", "saudi aramco": "2222.SR", "visa": "V", "mastercard": "MA",
    "alibaba": "BABA", "tsmc": "TSM",
}


def _pick_todays_company() -> str:
    """Returns today's featured company ticker based on day-of-year rotation."""
    day_index = datetime.now().timetuple().tm_yday % len(COMPANY_ROTATION)
    return COMPANY_ROTATION[day_index]


def _fetch_company_fundamentals(ticker: str) -> dict | None:
    """
    Fetches real financial data for a company using yfinance.
    Free — no API key, no account. Data sourced from Yahoo Finance.
    Returns a cleaned dict of fundamentals, or None on failure.
    Never blocks the pipeline.
    """
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        info = t.info

        def _pct(val):
            return round(val * 100, 1) if val is not None else None

        def _round(val, dp=1):
            return round(val, dp) if val is not None else None

        return {
            "ticker":              ticker,
            "name":                info.get("shortName", ticker),
            "sector":              info.get("sector", ""),
            "pe_ratio":            _round(info.get("trailingPE")),
            "eps":                 _round(info.get("trailingEps"), 2),
            "roe":                 _pct(info.get("returnOnEquity")),
            "gross_margin":        _pct(info.get("grossMargins")),
            "net_margin":          _pct(info.get("profitMargins")),
            "free_cash_flow_b":    _round(info.get("freeCashflow", 0) / 1e9) if info.get("freeCashflow") else None,
            "debt_to_equity":      _round(info.get("debtToEquity")),
            "revenue_growth_pct":  _pct(info.get("revenueGrowth")),
            "market_cap_b":        _round(info.get("marketCap", 0) / 1e9) if info.get("marketCap") else None,
            "dividend_yield":      _round(info.get("dividendYield"), 2),  # already in % (e.g. 0.4 = 0.4%)
            "beta":                _round(info.get("beta"), 2),
            "price":               _round(info.get("currentPrice", info.get("regularMarketPrice")), 2),
            "week52_high":         _round(info.get("fiftyTwoWeekHigh"), 2),
            "week52_low":          _round(info.get("fiftyTwoWeekLow"), 2),
        }
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {ticker}: {type(e).__name__}")
        return None


def _format_company_data_block(data: dict) -> str:
    """Formats company fundamentals as a clean text block for Claude prompts."""
    lines = [
        f"REAL COMPANY DATA — use these exact numbers, do not estimate or change them:",
        f"Company: {data['name']} ({data['ticker']})",
    ]
    if data.get("sector"):
        lines.append(f"Sector: {data['sector']}")
    if data.get("price"):
        lines.append(f"Current Price: ${data['price']}")
    if data.get("market_cap_b"):
        lines.append(f"Market Cap: ${data['market_cap_b']}B")
    if data.get("pe_ratio"):
        lines.append(f"P/E Ratio: {data['pe_ratio']}")
    if data.get("eps"):
        lines.append(f"EPS: ${data['eps']}")
    if data.get("gross_margin") is not None:
        lines.append(f"Gross Margin: {data['gross_margin']}%")
    if data.get("net_margin") is not None:
        lines.append(f"Net Margin: {data['net_margin']}%")
    if data.get("roe") is not None:
        lines.append(f"Return on Equity: {data['roe']}%")
    if data.get("free_cash_flow_b") is not None:
        lines.append(f"Free Cash Flow: ${data['free_cash_flow_b']}B")
    if data.get("revenue_growth_pct") is not None:
        lines.append(f"Revenue Growth (YoY): {data['revenue_growth_pct']}%")
    if data.get("debt_to_equity") is not None:
        lines.append(f"Debt-to-Equity: {data['debt_to_equity']}")
    if data.get("beta") is not None:
        lines.append(f"Beta: {data['beta']}")
    if data.get("week52_low") and data.get("week52_high"):
        lines.append(f"52-week range: ${data['week52_low']} — ${data['week52_high']}")
    if data.get("dividend_yield") is not None:
        lines.append(f"Dividend Yield: {data['dividend_yield']}%")  # e.g. 0.4 = 0.4% per year
    lines.append("\nBuild the lesson around this company using these exact numbers.")
    return "\n".join(lines)


# Behavioral finance angles mapped to common finance keywords
TOPIC_ANGLES = {
    "market": {
        "angle": "How market noise triggers emotional decision-making in retail investors",
        "emotional_trigger": "fear",
        "key_stat": "71% of retail investors sell at a loss during market downturns due to emotional panic"
    },
    "inflation": {
        "angle": "How inflation anxiety pushes investors into impulsive, low-return decisions",
        "emotional_trigger": "anxiety",
        "key_stat": "Investors who react to inflation headlines underperform the market by 3.2% annually"
    },
    "stock": {
        "angle": "Why chasing individual stocks is a psychological trap, not a strategy",
        "emotional_trigger": "FOMO",
        "key_stat": "Over 90% of active retail traders underperform a simple index fund over 10 years"
    },
    "invest": {
        "angle": "The hidden emotional patterns that sabotage long-term investing success",
        "emotional_trigger": "overconfidence",
        "key_stat": "Overconfident investors trade 45% more and earn 3.7% less annually"
    },
    "fed": {
        "angle": "How Fed announcements trigger irrational investor behaviour rooted in fear",
        "emotional_trigger": "fear",
        "key_stat": "Markets swing 2-3x more than fundamentals justify on Fed announcement days"
    },
    "rate": {
        "angle": "Why interest rate changes cause investors to make predictably bad decisions",
        "emotional_trigger": "anxiety",
        "key_stat": "Investors who react to rate changes sell at the worst possible time 68% of the time"
    },
    "recession": {
        "angle": "The psychology of recession fear and why it costs investors more than the recession itself",
        "emotional_trigger": "fear",
        "key_stat": "Investors who pulled out in 2020 missed a 100% recovery in 12 months"
    },
    "crypto": {
        "angle": "Why crypto volatility is the ultimate test of investor emotional control",
        "emotional_trigger": "FOMO",
        "key_stat": "Average crypto investor buys near peaks and sells near bottoms, losing 54% on timing alone"
    },
    "ai": {
        "angle": "How AI is changing personal finance — and what it means for how you invest",
        "emotional_trigger": "anxiety",
        "key_stat": "AI-assisted investors make 40% fewer emotional trades than those flying solo"
    },
    "debt": {
        "angle": "The shame spiral of financial debt and why it stops people from investing at all",
        "emotional_trigger": "shame",
        "key_stat": "64% of people with debt avoid investing entirely, even when they could afford to start small"
    },
}

# Fallback topics — used when RSS feeds fail or Claude analysis fails.
# RULE: Every fallback MUST have: cfa_concept, real_companies, key_stat (a real number),
# teaching_example, the_misconception, the_correct_view, and jargon_glossary.
# Psychology/mindset is the HOOK only — the CFA concept is the LESSON.
FALLBACK_TOPICS = [
    {
        "topic": "Nvidia's P/E at 35x — what the earnings multiple actually tells you",
        "cfa_concept": "Price-to-Earnings Ratio (P/E)",
        "real_companies": ["Nvidia", "Intel"],
        "key_stat": "35x",
        "hook": "Nvidia trades at 35x earnings. Intel trades at 12x. One number hides everything.",
        "angle": "P/E ratio measures how much investors pay per dollar of profit. High P/E = high growth expectation. Low P/E = doubt or value trap. The ratio without context is useless.",
        "teaching_example": "Nvidia P/E = 35x means investors pay $35 for every $1 of earnings. Intel at 12x means the market expects slower growth. A CFA uses forward P/E (next year's earnings) not trailing P/E to compare them properly.",
        "the_misconception": "A high P/E always means a stock is expensive and overvalued.",
        "the_correct_view": "P/E must be compared to growth rate (PEG ratio) — a 35x P/E on 40% earnings growth is cheaper than a 12x P/E on 2% growth.",
        "jargon_glossary": {
            "P/E ratio": "price per share divided by earnings per share — how much you pay for $1 of profit",
            "forward P/E": "price divided by next year's expected earnings — more useful than trailing P/E",
            "PEG ratio": "P/E divided by earnings growth rate — adjusts valuation for growth speed"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "P/E ratio is the most misunderstood metric in retail investing. Every financial news segment uses it without explaining what it actually measures.",
    },
    {
        "topic": "Apple's gross margin at 46% — why this number matters more than revenue",
        "cfa_concept": "Gross Margin Analysis",
        "real_companies": ["Apple", "Samsung"],
        "key_stat": "46%",
        "hook": "Apple keeps $0.46 of every dollar in revenue. Samsung keeps $0.30. That gap is the moat.",
        "angle": "Gross margin (revenue minus cost of goods sold, divided by revenue) measures pricing power and operational efficiency. High gross margin = strong brand or unique product. It compounds into free cash flow.",
        "teaching_example": "Apple gross margin = 46%. Every iPhone sold generates $0.46 of gross profit before operating costs. That margin has expanded from 38% in 2016 to 46% in 2024 — showing pricing power growing, not shrinking.",
        "the_misconception": "Revenue growth is the most important indicator of a company's health.",
        "the_correct_view": "Gross margin tells you whether growth is profitable. A company growing revenue at 20% with 10% gross margin is less valuable than one growing 10% with 45% gross margin.",
        "jargon_glossary": {
            "gross margin": "revenue minus cost of goods sold, divided by revenue — what's left before operating costs",
            "pricing power": "ability to raise prices without losing customers — gross margin expanding over time signals this",
            "free cash flow": "cash generated after all operating expenses and capital investment"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "Most retail investors focus on revenue headlines. Gross margin is the real signal of competitive advantage.",
    },
    {
        "topic": "Saudi Aramco's FCF yield vs P/E — two numbers, two different stories",
        "cfa_concept": "Free Cash Flow Yield vs P/E Ratio",
        "real_companies": ["Saudi Aramco", "ExxonMobil"],
        "key_stat": "5.2%",
        "hook": "Aramco's P/E is 18x. Its free cash flow yield is 5.2%. They tell opposite stories.",
        "angle": "FCF yield (free cash flow divided by market cap) measures actual cash generation, not accounting earnings. A company with high FCF yield is generating real cash even when P/E looks expensive.",
        "teaching_example": "Aramco P/E = 18x (looks expensive vs. ExxonMobil at 14x). But Aramco's FCF yield = 5.2% vs ExxonMobil's 4.1%. A CFA looks at FCF yield first for capital-heavy industries like energy — earnings can be manipulated, cash cannot.",
        "the_misconception": "The P/E ratio is the best way to judge if an oil company is cheap or expensive.",
        "the_correct_view": "For capital-intensive companies, free cash flow yield is more reliable than P/E — cash generation after all investment is what pays dividends and funds buybacks.",
        "jargon_glossary": {
            "FCF yield": "free cash flow divided by market cap — the cash return you get for every dollar invested",
            "free cash flow": "operating cash flow minus capital expenditure — cash left after maintaining and growing the business",
            "P/E ratio": "price divided by earnings per share — can be distorted by accounting choices"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "Energy investing requires different metrics than tech. FCF yield is the first thing a CFA checks for Aramco, not P/E.",
    },
    {
        "topic": "What Visa's 97% gross margin teaches you about business quality",
        "cfa_concept": "Return on Invested Capital (ROIC)",
        "real_companies": ["Visa", "Mastercard"],
        "key_stat": "97%",
        "hook": "Visa's gross margin is 97%. It costs almost nothing to process another payment.",
        "angle": "ROIC (net operating profit after tax divided by invested capital) measures how efficiently a company turns every dollar of investment into profit. Visa's near-zero marginal cost of processing transactions creates compounding ROIC.",
        "teaching_example": "Visa processes 200 billion transactions per year. Adding one more transaction costs essentially $0. This is why Visa's ROIC exceeds 30% — the business requires almost no additional capital to grow. Compare to airlines, where ROIC rarely exceeds 8%.",
        "the_misconception": "A company with a high stock price is automatically a good business.",
        "the_correct_view": "ROIC above the company's cost of capital is what creates shareholder value. Visa's ROIC at 30%+ against a cost of capital of ~9% means every dollar invested creates $3+ of value.",
        "jargon_glossary": {
            "ROIC": "return on invested capital — net operating profit divided by total capital invested",
            "marginal cost": "cost of producing one additional unit — near-zero for software and payment networks",
            "cost of capital": "the minimum return a company must earn to satisfy investors and debt holders"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "COMPANY",
        "why_today": "Business model quality is invisible in revenue headlines. ROIC is the lens that reveals it.",
    },
    {
        "topic": "Tesla's EV/EBITDA at 40x — how analysts actually value growth companies",
        "cfa_concept": "EV/EBITDA Valuation Multiple",
        "real_companies": ["Tesla", "Toyota"],
        "key_stat": "40x",
        "hook": "Tesla's EV/EBITDA is 40x. Toyota's is 8x. Neither is wrong. Here's why.",
        "angle": "EV/EBITDA (enterprise value divided by earnings before interest, tax, depreciation, and amortization) is the multiple professional analysts use to compare companies across different capital structures and tax environments.",
        "teaching_example": "Tesla EV/EBITDA = 40x. Toyota = 8x. Toyota has decades of stable cash flows and low growth expectations. Tesla's 40x multiple prices in expansion into robotics, AI, and energy storage. A CFA compares EV/EBITDA to the industry average and to the company's own 5-year history — not to an unrelated sector.",
        "the_misconception": "You can use P/E to compare any two companies in any industry.",
        "the_correct_view": "EV/EBITDA removes distortions from different debt levels, tax rates, and depreciation policies — making it the professional standard for cross-company comparison.",
        "jargon_glossary": {
            "EV/EBITDA": "enterprise value divided by EBITDA — compares company value to operating earnings before capital structure effects",
            "enterprise value": "market cap plus debt minus cash — the total price to buy the whole business",
            "EBITDA": "earnings before interest, taxes, depreciation, and amortization — a proxy for operating cash flow"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "COMPANY",
        "why_today": "EV/EBITDA is used in every investment bank model but rarely explained to retail investors.",
    },
    {
        "topic": "JPMorgan's net interest margin — how banks actually make money",
        "cfa_concept": "Net Interest Margin (NIM) and Bank Profitability",
        "real_companies": ["JPMorgan Chase", "Bank of America"],
        "key_stat": "2.7%",
        "hook": "JPMorgan earns 2.7% net interest margin. That 2.7% is printed on $3.3 trillion of assets.",
        "angle": "Net interest margin (interest income minus interest expense, divided by earning assets) is the core profitability driver for banks. When the Fed raises rates, banks earn more on loans but also pay more on deposits — NIM captures the spread.",
        "teaching_example": "JPMorgan NIM = 2.7% on $3.3 trillion in assets = ~$89 billion in net interest income annually. When the Fed raised rates from 0% to 5.25% in 2022-2023, JPMorgan's NIM expanded from 1.8% to 2.7% — a 50% improvement in core earnings.",
        "the_misconception": "Rising interest rates are bad for all stocks.",
        "the_correct_view": "Rising rates hurt growth stocks (future cash flows worth less) but help banks (NIM expands when rates rise). A CFA separates rate-sensitive sectors before making portfolio allocation decisions.",
        "jargon_glossary": {
            "net interest margin": "interest earned on loans minus interest paid on deposits, divided by total earning assets",
            "earning assets": "loans and securities that generate interest income for the bank",
            "NIM": "net interest margin — the spread between borrowing and lending rates"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "Most retail investors don't know how banks earn money. NIM is the single number that explains bank profitability.",
    },
    {
        "topic": "Amazon's operating leverage — why revenue growth at scale is different",
        "cfa_concept": "Operating Leverage and Margin Expansion",
        "real_companies": ["Amazon", "Walmart"],
        "key_stat": "11%",
        "hook": "Amazon's operating margin went from 2% to 11% in 3 years. Revenue barely doubled. That's operating leverage.",
        "angle": "Operating leverage measures how much profit increases relative to revenue growth. A company with high fixed costs and low variable costs generates exponentially more profit as revenue scales — because the fixed costs are already paid.",
        "teaching_example": "Amazon AWS (cloud) revenue = $100B with ~30% operating margins. Amazon retail = $400B with ~3% margins. As AWS grows faster than retail, the total operating margin expands. Each new AWS customer added costs almost nothing — the servers are already built. That is operating leverage compounding.",
        "the_misconception": "A company's operating profit margin stays the same as the company grows.",
        "the_correct_view": "Companies with high fixed costs and scalable models see margins expand as revenue grows — this is operating leverage, and it is the primary driver of long-term shareholder value creation.",
        "jargon_glossary": {
            "operating leverage": "how much operating profit increases relative to revenue growth — high fixed costs create leverage",
            "operating margin": "operating profit divided by revenue — what remains after all operating costs",
            "fixed costs": "costs that stay constant regardless of revenue volume (data centers, salaries, buildings)"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "COMPANY",
        "why_today": "Operating leverage explains why big tech companies generate disproportionate profit growth — and why their valuations look expensive until you account for it.",
    },
    {
        "topic": "Beta and portfolio risk — why adding a volatile stock can reduce your risk",
        "cfa_concept": "Beta, Correlation, and Portfolio Diversification",
        "real_companies": ["Gold ETF (GLD)", "S&P 500 (SPY)"],
        "key_stat": "-0.2",
        "hook": "Gold's beta to the S&P 500 is -0.2. Adding it to your portfolio cuts total risk — even though gold is volatile.",
        "angle": "Beta measures how much a security moves relative to the market. A beta of 1 = moves with the market. Beta < 0 = moves opposite. Correlation (not just volatility) is what drives portfolio risk — two volatile assets with negative correlation create a stable portfolio.",
        "teaching_example": "S&P 500 beta = 1.0. Gold beta = -0.2 (tends to rise when equities fall). A portfolio of 90% SPY + 10% GLD has lower total risk (standard deviation) than 100% SPY — because gold's negative correlation reduces portfolio variance even though gold itself is volatile.",
        "the_misconception": "Adding a volatile asset to your portfolio always increases risk.",
        "the_correct_view": "Portfolio risk depends on correlation between assets, not just individual volatility. A low-beta or negatively-correlated asset reduces total portfolio standard deviation.",
        "jargon_glossary": {
            "beta": "a measure of a security's sensitivity to market movements — beta of 1 means it moves with the market",
            "correlation": "how two assets move relative to each other — correlation of -1 means they move in opposite directions",
            "standard deviation": "measure of how much an asset's returns vary around the average — the CFA's definition of risk"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "PORTFOLIO",
        "why_today": "Diversification is misunderstood. Most retail investors think 10 different stocks = diversification. A CFA looks at correlation.",
    },
    {
        "topic": "Berkshire's book value vs market value — what Buffett actually measures",
        "cfa_concept": "Price-to-Book Ratio (P/B) and Intrinsic Value",
        "real_companies": ["Berkshire Hathaway", "S&P 500"],
        "key_stat": "1.6x",
        "hook": "Berkshire trades at 1.6x book value. Buffett has never paid more than 1.2x for his own shares.",
        "angle": "Price-to-book ratio (market price divided by book value per share) compares what the market pays versus what the company's assets are worth on paper. For value investors, P/B anchors intrinsic value estimation.",
        "teaching_example": "Berkshire Hathaway's book value per share = ~$400,000. Market price = ~$640,000. P/B = 1.6x. Buffett's stated buyback threshold is 1.2x book — meaning he believes Berkshire is undervalued below that level. When P/B exceeds 1.6x, he stops buying back stock. This is systematic intrinsic value investing made explicit.",
        "the_misconception": "The stock market price always reflects a company's real value.",
        "the_correct_view": "Price-to-book measures the premium investors pay over accounting value. A P/B above the company's historical average suggests the market is pricing in growth that may not materialize.",
        "jargon_glossary": {
            "price-to-book ratio": "market cap divided by book value — the premium investors pay over net asset value",
            "book value": "total assets minus total liabilities — what shareholders would receive if the company were liquidated today",
            "intrinsic value": "estimated true value of a company based on its future cash flows, assets, and earnings power"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "P/B is the foundation of value investing. Understanding it explains Warren Buffett's entire buyback strategy.",
    },
    {
        "topic": "Microsoft's debt-to-equity — why some debt is better than no debt",
        "cfa_concept": "Capital Structure and Debt-to-Equity Ratio",
        "real_companies": ["Microsoft", "Apple"],
        "key_stat": "0.35x",
        "hook": "Microsoft has a debt-to-equity of 0.35x. Apple's is 1.8x. Apple is more 'in debt' — and it's deliberate.",
        "angle": "Debt-to-equity ratio (total debt divided by shareholders' equity) measures financial leverage. Strategic debt is not a sign of weakness — it is a tax-efficient way to fund growth when the company's return on capital exceeds the cost of borrowing.",
        "teaching_example": "Apple borrows at ~3% interest (tax-deductible) to fund share buybacks that yield ~8% in earnings accretion. Net cost to Apple = 3% × (1 - 21% tax rate) = 2.4%. Return = 8%. The spread is 5.6%. This is why Apple deliberately carries $105B in debt despite having $67B in cash — debt is cheaper than equity at Apple's scale.",
        "the_misconception": "Companies with high debt are always riskier investments.",
        "the_correct_view": "Debt is a tool. When a company's ROIC exceeds its cost of debt, leverage amplifies shareholder returns. The risk is when ROIC falls below the interest rate — which is why debt level must be compared to earnings stability.",
        "jargon_glossary": {
            "debt-to-equity ratio": "total debt divided by shareholders' equity — measures how much of the business is financed by borrowing",
            "financial leverage": "using borrowed money to amplify returns — profitable when ROIC exceeds interest rate",
            "cost of debt": "the interest rate paid on borrowings, adjusted for tax deductibility"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "Retail investors fear debt. CFAs understand that strategic leverage creates value when managed correctly.",
    },
    {
        "topic": "The yield curve inversion — what happens to stocks when short rates beat long rates",
        "cfa_concept": "Yield Curve Analysis and Fixed Income Duration",
        "real_companies": ["US Treasury", "JPMorgan Chase"],
        "key_stat": "0.5%",
        "hook": "The 2-year Treasury yielded 0.5% more than the 10-year for 22 months straight. That has preceded every US recession since 1955.",
        "angle": "The yield curve plots bond yields against maturity. Normally, longer-term bonds yield more. When short-term rates exceed long-term rates (inversion), it signals the market expects rate cuts ahead — typically because a slowdown or recession is anticipated.",
        "teaching_example": "2022-2024: US 2-year yield = 5.0%, 10-year = 4.5%. Spread = -0.5% (inverted). Historically, S&P 500 returns 6 months after first inversion: +2.1% average. But returns 12-18 months later, when recession hits: -15% average. A CFA uses yield curve steepening/flattening to adjust portfolio duration, not to panic sell.",
        "the_misconception": "An inverted yield curve means the stock market will crash immediately.",
        "the_correct_view": "Inversion signals future economic stress, but markets often rally 6-12 months after inversion before the slowdown arrives. Timing matters as much as the signal.",
        "jargon_glossary": {
            "yield curve": "a chart of bond yields from shortest to longest maturity — normally slopes upward",
            "inversion": "when short-term yields exceed long-term yields — historically a recession indicator with 12-18 month lag",
            "duration": "a bond's sensitivity to interest rate changes — longer duration = bigger price move per rate change"
        },
        "emotional_trigger": "anxiety",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "Yield curve inversion is discussed in every financial news cycle but almost never explained mechanically to retail investors.",
    },
    {
        "topic": "Dividend yield vs dividend growth — why the number is the wrong thing to chase",
        "cfa_concept": "Dividend Discount Model and Dividend Growth Rate",
        "real_companies": ["Coca-Cola", "Microsoft"],
        "key_stat": "3.1%",
        "hook": "Coca-Cola yields 3.1%. Microsoft yields 0.8%. Microsoft's dividend has grown 10% per year for a decade. Which is the better income investment?",
        "angle": "Dividend discount model (DDM) values a stock by discounting future dividends. A high current yield is less valuable than a rapidly growing dividend — because compounding turns 0.8% at 10% growth into 2.1% yield on cost in 10 years.",
        "teaching_example": "Coca-Cola: 3.1% yield, dividend growth 4%/year. Microsoft: 0.8% yield, dividend growth 10%/year. In 10 years: Coke pays $3.40 per $100 invested. Microsoft pays $2.07 — already closing fast. In 15 years Microsoft surpasses Coke's dollar payout. A CFA looks at dividend growth rate (g) as the primary input, not current yield.",
        "the_misconception": "The best income investments are stocks with the highest dividend yields.",
        "the_correct_view": "Dividend growth rate, not current yield, determines long-term income. A growing dividend on a growing stock price creates compounding — a high yield with no growth can indicate a dying business.",
        "jargon_glossary": {
            "dividend yield": "annual dividend divided by stock price — current income as a percentage of price paid",
            "dividend growth rate": "annual percentage increase in the dividend per share — the key input in long-term income investing",
            "yield on cost": "dividend divided by the original purchase price — grows over time as dividends increase"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "Income investors systematically overpay for high-yield stocks and ignore compounding dividend growers.",
    },
    {
        "topic": "TSMC's capital expenditure — why the chip shortage was predictable",
        "cfa_concept": "Capital Expenditure Analysis and Asset-Intensive Business Models",
        "real_companies": ["TSMC", "Intel"],
        "key_stat": "$38B",
        "hook": "TSMC spent $38 billion building factories in 2023. It takes 3 years to build one chip fab. That is why supply cannot respond fast to demand.",
        "angle": "Capital expenditure (capex) is the cash a company spends on physical assets. Asset-intensive companies like semiconductor fabs have years-long capex cycles — meaning supply shortages persist because new capacity cannot be added quickly.",
        "teaching_example": "A TSMC 3nm fab costs ~$20 billion and takes 3+ years to build. TSMC capex in 2023 = $38B. When iPhone demand spiked in 2021, TSMC couldn't add capacity for 3 years — creating a chip shortage that affected cars, phones, and data centers. A CFA analyzing TSMC looks at capex-to-revenue ratio (35%+) to understand the capital intensity and cash flow constraints.",
        "the_misconception": "Companies with high profits always have strong cash flows.",
        "the_correct_view": "Capex-intensive companies can have high profits but weak free cash flow — because most earnings are immediately reinvested into assets. Free cash flow = operating cash flow minus capex is the real measure.",
        "jargon_glossary": {
            "capital expenditure": "cash spent on physical assets — factories, equipment, property — not expensed immediately",
            "capex-to-revenue ratio": "capital expenditure divided by revenue — high ratio means capital-intensive business model",
            "free cash flow": "operating cash flow minus capital expenditure — cash available after maintaining and growing assets"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "COMPANY",
        "why_today": "The chip shortage affected every industry. TSMC's capex cycle is why — and it's a perfect CFA lesson in capital-intensive analysis.",
    },
    {
        "topic": "Meta's price-to-sales — valuing a company with no physical assets",
        "cfa_concept": "Price-to-Sales Ratio and Asset-Light Business Valuation",
        "real_companies": ["Meta", "Alphabet"],
        "key_stat": "8x",
        "hook": "Meta trades at 8x revenue. It owns almost no physical assets. The entire value is in your attention.",
        "angle": "Price-to-sales ratio (market cap divided by annual revenue) is used when earnings are volatile or negative. For asset-light platforms, it measures how much investors pay per dollar of revenue — and whether the margin profile justifies the premium.",
        "teaching_example": "Meta P/S = 8x, operating margin = 35%. Alphabet P/S = 6x, operating margin = 27%. A CFA compares P/S to operating margin — a company with higher margin justifies a higher P/S. Meta's margin expansion from 20% (2022) to 35% (2024) while P/S stayed at 8x means the stock got cheaper on a forward earnings basis even as the P/S ratio held flat.",
        "the_misconception": "Price-to-earnings is always the right valuation metric.",
        "the_correct_view": "P/E requires positive earnings. P/S works across all phases of a company's lifecycle. For asset-light companies, P/S combined with operating margin reveals whether growth is profitable.",
        "jargon_glossary": {
            "price-to-sales ratio": "market cap divided by annual revenue — how much investors pay per dollar of revenue",
            "asset-light": "a business model requiring minimal physical assets to operate — software, platforms, marketplaces",
            "operating margin": "operating profit divided by revenue — the percentage of revenue that becomes operating profit"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "FUNDAMENTALS",
        "why_today": "Platform companies are valued differently from industrial companies. Retail investors need P/S to understand them.",
    },
    {
        "topic": "Alibaba's EV/FCF — how to value a company in a different market",
        "cfa_concept": "Cross-Market Valuation and Country Risk Premium",
        "real_companies": ["Alibaba", "Amazon"],
        "key_stat": "12x",
        "hook": "Alibaba trades at 12x free cash flow. Amazon trades at 40x. Are Chinese stocks actually cheap — or is the discount justified?",
        "angle": "EV/FCF (enterprise value divided by free cash flow) is one of the cleanest valuation metrics. When comparing companies across different markets, a country risk premium must be added to the discount rate — which lowers the intrinsic value of stocks in markets with higher political or regulatory risk.",
        "teaching_example": "Alibaba EV/FCF = 12x. Amazon = 40x. A naive comparison says Alibaba is 70% cheaper. A CFA adds a China country risk premium of 5-7% to the discount rate — meaning Alibaba's future cash flows are worth less per dollar because of regulatory uncertainty, capital controls, and delisting risk. 12x is not obviously cheap once you account for this.",
        "the_misconception": "A lower P/E or EV/FCF always means a stock is a better value.",
        "the_correct_view": "Valuation multiples must be adjusted for the risk of the market. A lower multiple in a higher-risk market may reflect fair value, not undervaluation.",
        "jargon_glossary": {
            "EV/FCF": "enterprise value divided by free cash flow — a cleaner valuation multiple than P/E for capital-intensive comparisons",
            "country risk premium": "extra return required by investors to compensate for political and regulatory risk in a specific country",
            "discount rate": "the rate used to calculate the present value of future cash flows — higher risk = higher rate = lower intrinsic value"
        },
        "emotional_trigger": "curiosity",
        "content_pillar": "COMPANY",
        "why_today": "Emerging market valuation is misunderstood. The 'discount' often just reflects higher required returns, not undervaluation.",
    },
]


SOURCE_NAMES = {
    RSS_FEEDS[0]: "Yahoo Finance (S&P 500)",
    RSS_FEEDS[1]: "Yahoo Finance (Dow Jones)",
    RSS_FEEDS[2]: "Reuters Business",
    RSS_FEEDS[3]: "Reuters Company",
    RSS_FEEDS[4]: "CNBC Markets",
    RSS_FEEDS[5]: "CNBC Earnings",
    RSS_FEEDS[6]: "CNBC Search",
    RSS_FEEDS[7]: "MarketWatch Top Stories",
    RSS_FEEDS[8]: "MarketWatch Market Pulse",
    RSS_FEEDS[9]: "Investing.com",
}


def _fetch_rss_headlines() -> list[dict]:
    """Fetches headlines from multiple free RSS feeds.
    Returns list of dicts: {title, source, timestamp}."""
    headlines = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FincareBot/1.0)"}

    for url in RSS_FEEDS:
        source_name = SOURCE_NAMES.get(url, url.split("/")[2])
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            # RSS format
            for item in root.iter("item"):
                title = item.find("title")
                pub_date = item.find("pubDate")
                if title is not None and title.text:
                    headlines.append({
                        "title": title.text.strip(),
                        "source": source_name,
                        "timestamp": pub_date.text.strip() if pub_date is not None and pub_date.text else "",
                    })
            # Atom format
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title = entry.find("{http://www.w3.org/2005/Atom}title")
                published = entry.find("{http://www.w3.org/2005/Atom}published")
                if title is not None and title.text:
                    headlines.append({
                        "title": title.text.strip(),
                        "source": source_name,
                        "timestamp": published.text.strip() if published is not None and published.text else "",
                    })

            logger.info(f"Fetched {len(headlines)} headlines so far...")
        except Exception as e:
            logger.warning(f"RSS feed failed ({url[:40]}...): {type(e).__name__}")
            continue

    return headlines


def _get_last_7_topics() -> list[str]:
    """Reads the last 7 days of draft files to avoid repeating recent topics."""
    import glob
    topics = []
    files = sorted(glob.glob("drafts/posts_*.json"), reverse=True)[:7]
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
                t = data.get("topic", {}).get("topic", "")
                if t:
                    topics.append(t)
        except Exception:
            pass
    return topics


_TOPIC_SCOUT_SYSTEM = """You are the Research Analyst for FINVYON — a CFA charterholder teaching real finance to retail investors on social media. Your job is to find ONE topic today that is genuinely worth 60 seconds of a smart investor's attention, and hand the Writer a rigorous brief they can turn into education, not emotion.

FINVYON's audience: retail investors who are smart enough to want real knowledge but were never taught it. They do not need another reminder that markets are scary. They need to understand how markets actually work — P/E ratios, cash flow, portfolio construction, what today's news means mechanically, how to read a company's numbers. If they wanted panic-bait, they would scroll somewhere else.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHO YOU ARE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are a CFA-trained research analyst with a journalist's instinct for what makes a story land. You read headlines the way a portfolio manager does — asking what the number actually means, what the mechanism is, what a well-informed investor would do differently after reading it. You do not chase emotion. You chase signal.

Behavioral finance is ONE of your tools, not your default lens. You deploy it only on the MINDSET pillar (Friday), and even then as applied psychology backed by real data — not as generic "your brain is panicking" content.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PILLAR-SPECIFIC SELECTION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Today's pillar: {content_pillar}

FUNDAMENTALS (Mon): Pick a single CFA concept worth teaching. It can be company-specific OR a broader market/investing concept — but must always have a specific number. Examples: "The Rule of 72 — why 7% returns double money in 10 years", "Yield curve inversion: what the 10Y-2Y spread of -0.4% actually predicts", "Apple's P/E is 28 vs Microsoft's 34, here's what that 6-point gap actually tells you", "Why dollar-cost averaging into S&P 500 over 20 years beats 94% of active fund managers." Vary between pure concepts, macro topics, and company examples — do NOT default to a single company every time.

NEWS (Tue): Pick a real headline from the last 24 hours (score 0 if older than 48h). The headline must have a specific number or policy change you can teach around — Fed decision basis points, an earnings beat/miss with a specific percentage, a bond yield move, a specific company guidance change. Generic "markets are volatile" is disqualified. The test: can you name ONE specific metric an informed investor would watch because of this news?

COMPANY (Wed): Pick one well-known public company and ONE financial metric where the current number tells a story. Must have: company ticker, the exact current number, the sector benchmark or direct competitor's number, and the non-obvious interpretation. Never generic — "Nvidia has high margins" fails, "Nvidia's 74% gross margin is 2x the semiconductor sector average — here's what that pricing power means" passes.

PORTFOLIO (Thu): Pick ONE portfolio construction principle and a concrete counterexample of retail investors getting it wrong. Must include specific numbers — correlation coefficients, position sizes, allocation percentages, rebalancing thresholds. Never abstract.

MINDSET (Fri): This is the ONLY pillar where behavioral finance leads. Even so, you must ground it in a specific published study with real data (author, year, percentage or dollar figure). Generic "investors panic" fails. "DALBAR 2023 QAIB: average equity fund investor underperformed S&P 500 by 5.5% annually over 20 years" passes.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING — INTERNAL ONLY, DO NOT OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Score every candidate topic on these four dimensions. The weights shift by pillar.

EDUCATIONAL DEPTH (0-10): Does this teach something a retail investor genuinely does not know and would be smarter for knowing? A topic that just restates common knowledge ("stocks go up and down") is disqualified. Weight: 3x on FUNDAMENTALS, COMPANY, PORTFOLIO.

SPECIFICITY (0-10): Does the topic have a real company, a real number, a real formula, a real study? Anything abstract is disqualified. Score 0 if you cannot name the specific number the video will be built around.

TIMELINESS (0-10): Weight 3x on NEWS pillar (must be <24h). Weight 1x on FUNDAMENTALS/PORTFOLIO (evergreen is fine). Weight 2x on COMPANY (earnings season increases weight).

UNIQUENESS (0-10): Cross-check last_7_topics. Subtract 5 if a near-duplicate was covered in the last 5 days. Same company with a different metric is fine; same metric on same company is not.

TRENDING (0-5, weight 1×): Does this topic appear in 2 or more of the provided headlines? Count how many headlines share the same company, event, or metric before scoring. Score 5 if multi-source confirmation, 0 if single-source only. Multi-source = genuinely trending = higher audience relevance.

Automatic disqualifiers:
- Cannot name a specific number
- Cannot name a specific company, fund, or published study
- Topic is "markets will be volatile" or equivalent empty observation
- Topic requires the viewer to feel something before they understand something (except MINDSET pillar)
- The topic could be written with zero financial knowledge

Pick the highest-scoring candidate. On a tie, pick the one where the specificity score is higher.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FIELDS — EVERY FIELD MUST PASS THE RIGOR TEST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The Rigor Test: before writing any field, ask "would a CFA charterholder reading this think 'yes, that is accurate and that is the right number to highlight'?" If not, rewrite.

topic: 4-8 words naming the specific concept + company/headline. Example: "Nvidia's 74% gross margin — what it means." Never: "Understanding company profits."

hook: Pattern-interrupt hook. Must follow ONE of these structures:
  A. "[Company]'s [metric] is [number]. Here's what most investors miss."
  B. "[Number] [metric]. [Company] vs [competitor/sector]. One number changes how you value it."
  C. "[Specific CFA observation using the stat and company] — and that changes how you analyse it."
  The hook must name at least one specific company AND one specific number. Must work as a standalone tweet — if it only makes sense with more context it is too weak. If a CFA charterholder would call it vague, rewrite it.
  BANNED opening words: "Did you know", "Most people", "Here's why", "The secret", "What if I told you", "You won't believe", "This is why", "Everyone knows"
  Good example: "Apple's gross margin hit 46%. Most investors celebrate. CFA analysts ask what comes next."
  Bad example: "Here's why gross margin matters for investors."

key_stat: The single most important number for this topic. Must be a real, verifiable finance number — a P/E ratio, a margin, a yield, a percentage change, a formula output. NOT a behavioral statistic unless pillar is MINDSET. Format: "[Number/percentage] [what it measures] [for what company/index/period]." Flag [verify] if uncertain.

angle: 2-3 sentences explaining the specific thing the video will teach. Must name the concept, the calculation or mechanism, and why the number is interesting. This is your brief to the Writer — be precise enough that the Writer could not accidentally make it generic.

behavioral_angle: ONLY populate on MINDSET pillar. Leave as empty string on every other pillar. When populated, must cite a specific study or data source.

content_pillar: The pillar for today (already supplied in input).

real_companies: Array of 1-3 specific public companies (with tickers) the Writer should reference. Required on FUNDAMENTALS, COMPANY, PORTFOLIO, and NEWS pillars when applicable.

cfa_concept: The specific CFA curriculum concept being taught (e.g., "P/E ratio interpretation," "Modigliani-Miller Proposition," "Duration-convexity," "Sharpe ratio"). This anchors FINVYON's credibility.

the_misconception: One sentence naming what retail investors typically get wrong about this topic. This is what the video will correct.

the_correct_view: One sentence naming what a CFA-trained view of the topic looks like. This is what the video will teach.

teaching_example: A concrete example the Writer can use — real company, real numbers, real comparison. Must be verifiable.

source_headline: Only for NEWS pillar. The exact headline, publication, and publication timestamp. Empty string on all other pillars.

jargon_glossary: A dictionary of every finance term that will appear in the video, with a one-clause plain-English definition the Writer can drop inline. Format: {"P/E ratio": "price divided by earnings — how much you pay per dollar of profit"}. This is how FINVYON stays educational without being patronizing. Do NOT replace the term — define it in-line the first time it appears.

emotional_trigger: The dominant investor emotion this topic activates. Choose one: curiosity | anxiety | shame | fomo | overconfidence | fear | regret. Even on education-first pillars, one emotion is always present — name it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WRITING RULES FOR EVERY FIELD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Every claim is either a verifiable fact, a real number, or clearly flagged as [verify].
- FINVYON never uses cutesy substitutions for finance terms. He uses the real term and defines it. "P/E ratio [price divided by earnings]" — not "the price-to-profit ratio" or "how expensive a stock is."
- Use contractions (it's, you're, here's). This is spoken-register English.
- Banned phrases: "navigate," "volatile," "dive into," "game-changer," "unlock," "empower," "synergy," "holistic," "leverage" (as a verb), "in conclusion," "it's important to note."
- Never start a sentence with "This."
- Never use passive voice.
- If the field reads like a LinkedIn thought-leader post, rewrite it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SELF-CHECK BEFORE OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Run these silently. Rewrite any field that fails.

SPECIFICITY CHECK: Does key_stat contain a real finance number with a real company or index attached? If no → rewrite.
RIGOR CHECK: Could a CFA charterholder read this brief and think it's accurate? If no → rewrite.
DEPTH CHECK: Does angle teach something non-obvious? If a retail investor would say "I already knew that" → rewrite.
PILLAR FIT CHECK: Does behavioral_angle appear only on MINDSET? If it leaked into another pillar → delete.
VERIFIABILITY CHECK: Is every number either a well-known finance fact or flagged [verify]? If any number is hallucinated → flag it.

Return ONLY raw JSON. No preamble. No markdown fences. Start with { and end with }."""


def _analyze_headlines_with_claude(headlines: list[dict], pillar: str,
                                    last_7_topics: list[str] | None = None,
                                    company_data: dict | None = None) -> dict | None:
    """
    Uses Claude (Topic Scout) to pick the most emotionally resonant headline
    and generate a full behavioral finance content brief.
    """
    from utils.llm import call_llm

    today = datetime.now().strftime("%A, %B %d, %Y")

    # Format headlines with source and timestamp
    headlines_text = "\n".join(
        f"- [{h['source']}] {h['title']}"
        + (f" ({h['timestamp'][:16]})" if h.get("timestamp") else "")
        for h in headlines[:40]
    )

    last_7_text = (
        "\n".join(f"- {t}" for t in last_7_topics)
        if last_7_topics else "No recent topics on record."
    )

    company_block = (
        f"\n\n{_format_company_data_block(company_data)}\n"
        if company_data else ""
    )

    user_message = (
        f"Today is {today}. Today's content pillar is: {pillar}\n\n"
        f"HEADLINES:\n{headlines_text}\n\n"
        f"LAST 7 TOPICS (avoid repeating these):\n{last_7_text}\n\n"
        f"MARKET CONTEXT: No additional context provided — infer from headlines."
        f"{company_block}\n\n"
        "Return ONLY raw JSON matching this structure exactly:\n"
        '{\n'
        '  "topic": "4-8 words: specific concept + company/headline",\n'
        '  "hook": "Pattern-interrupt. Must contain company name + number. Follows structure A/B/C from instructions.",\n'
        '  "key_stat": "One real verifiable finance number. Format: [Number] [what it measures] [for what]. Flag [verify] if uncertain.",\n'
        '  "angle": "2-3 sentences. Name the concept, calculation/mechanism, and why the number is interesting.",\n'
        '  "behavioral_angle": "MINDSET pillar only — cite specific study/data. Empty string on all other pillars.",\n'
        '  "content_pillar": "FUNDAMENTALS | NEWS | COMPANY | PORTFOLIO | MINDSET",\n'
        '  "real_companies": ["TICKER1 — Company Name", "TICKER2 — Company Name"],\n'
        '  "cfa_concept": "Specific CFA curriculum concept being taught",\n'
        '  "the_misconception": "One sentence: what retail investors typically get wrong about this topic.",\n'
        '  "the_correct_view": "One sentence: what a CFA-trained view of this topic looks like.",\n'
        '  "teaching_example": "Concrete example: real company, real numbers, real comparison. Must be verifiable.",\n'
        '  "source_headline": "NEWS pillar only: exact headline + publication + timestamp. Empty string otherwise.",\n'
        '  "jargon_glossary": {"finance term": "plain-English definition in one clause"},\n'
        '  "emotional_trigger": "curiosity | anxiety | shame | fomo | overconfidence | fear | regret",\n'
        '  "jargon_free_angle": "The angle rewritten as FINVYON would explain it over coffee — uses real terms but defines each one inline.",\n'
        '  "session_learning": "One sentence. Hardest field to write and rule applied going forward."\n'
        '}'
    )

    try:
        raw = call_llm(
            _TOPIC_SCOUT_SYSTEM.replace("{content_pillar}", pillar),
            user_message, tier="sonnet", max_tokens=1500,
        ).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        topic = json.loads(raw)
        logger.success(f"Topic Scout picked: {topic.get('topic', '')[:60]}")
        if topic.get("session_learning"):
            logger.info(f"Session learning: {topic['session_learning']}")
        return topic
    except Exception as e:
        logger.warning(f"Claude topic analysis failed ({type(e).__name__}) — using fallback.")
        return None


CONTENT_PILLARS = {
    0: {"name": "FUNDAMENTALS", "day": "Monday"},
    1: {"name": "NEWS",         "day": "Tuesday"},
    2: {"name": "COMPANY",      "day": "Wednesday"},
    3: {"name": "PORTFOLIO",    "day": "Thursday"},
    4: {"name": "MINDSET",      "day": "Friday"},
    5: {"name": "FUNDAMENTALS", "day": "Saturday"},  # Weekend fallback — no scheduled post
    6: {"name": "FUNDAMENTALS", "day": "Sunday"},    # Weekend fallback — no scheduled post
}


def get_todays_pillar() -> dict:
    """Returns today's content pillar based on day of week."""
    weekday = datetime.now().weekday()  # 0=Monday, 6=Sunday
    pillar = CONTENT_PILLARS[weekday]
    logger.info(f"Today's content pillar: {pillar['name']} ({pillar['day']})")
    return pillar


def research_topic() -> dict:
    """
    Main research function.
    Step 1: Fetch headlines from RSS feeds.
    Step 1b: Fetch real company fundamentals via yfinance (free, no API key).
    Step 2: Use Claude (Topic Scout) to pick the best topic, enriched with real data.
    Step 3: Normalize output keys for downstream compatibility.
    Step 4: If Claude fails, fall back to pre-written topics.
    """
    logger.step("Starting topic research...")

    pillar = get_todays_pillar()
    pillar_name = pillar["name"]
    last_7_topics = _get_last_7_topics()
    if last_7_topics:
        logger.info(f"Last 7 topics loaded: {len(last_7_topics)} entries")

    headlines = _fetch_rss_headlines()
    logger.info(f"Total headlines fetched: {len(headlines)}")

    # ── Fetch real company data (yfinance — free, no API key) ────────────────
    company_data = None
    ticker = None

    if pillar_name in ("FUNDAMENTALS", "COMPANY", "PORTFOLIO"):
        # Pre-fetch today's rotating company before Claude writes
        ticker = _pick_todays_company()
        logger.info(f"Fetching fundamentals for {ticker}...")
        company_data = _fetch_company_fundamentals(ticker)
        if company_data:
            logger.success(f"Company data: {company_data['name']} | P/E={company_data.get('pe_ratio')} | GM={company_data.get('gross_margin')}%")
        else:
            logger.warning(f"Could not fetch {ticker} — lesson will proceed without real data.")

    elif pillar_name == "NEWS":
        # For NEWS: detect company from headlines, fetch after headlines are known
        headline_text = " ".join(h["title"].lower() for h in headlines[:20])
        for keyword, tkr in HEADLINE_TICKERS.items():
            if keyword in headline_text:
                ticker = tkr
                logger.info(f"Detected company in headlines: {tkr} ({keyword})")
                company_data = _fetch_company_fundamentals(tkr)
                if company_data:
                    logger.success(f"NEWS company data: {company_data['name']}")
                break

    # MINDSET: no company data needed — behavioral finance is Claude's domain

    if headlines:
        topic = _analyze_headlines_with_claude(
            headlines, pillar_name, last_7_topics,
            company_data=company_data,
        )
        if topic:
            # Normalize fields to pipeline aliases used by writer.py and video_agent.py
            # New prompt uses "angle" directly; old prompt used "behavioral_angle" as the angle
            if not topic.get("angle") and topic.get("behavioral_angle"):
                topic["angle"] = topic["behavioral_angle"]
            # source_headline: new prompt sets it directly; old prompt used "selected_headline"
            if not topic.get("source_headline") and topic.get("selected_headline"):
                topic["source_headline"] = topic["selected_headline"]
            # jargon_free_angle: new prompt returns it directly; fall back to angle
            if not topic.get("jargon_free_angle"):
                topic["jargon_free_angle"] = topic.get("angle", "")
            # mental_model: new prompt omits it — writer.py handles missing gracefully
            topic.setdefault("mental_model", {})
            # content_pillar: always authoritative from today's schedule
            topic["content_pillar"] = pillar_name
            # Attach real company data so writer + video agent use accurate numbers
            if company_data:
                topic["company_data"]   = company_data
                topic["company_ticker"] = company_data["ticker"]
                topic["company_name"]   = company_data["name"]
            topic = _add_video_format(topic)
            return topic
        logger.warning("Claude analysis failed — using fallback.")
    else:
        logger.warning("All RSS feeds failed — using fallback topic.")

    # Use a rotating CFA-anchored fallback topic.
    # All fallback topics have: cfa_concept, real_companies, teaching_example,
    # the_misconception, the_correct_view, jargon_glossary — the writer needs all of these.
    day_index = datetime.now().timetuple().tm_yday % len(FALLBACK_TOPICS)
    topic = FALLBACK_TOPICS[day_index].copy()
    topic["content_pillar"] = pillar_name
    # Attach any company data already fetched (best-effort enrichment)
    if company_data:
        topic["company_data"]   = company_data
        topic["company_ticker"] = company_data["ticker"]
        topic["company_name"]   = company_data["name"]
    topic = _add_video_format(topic)
    logger.success(
        f"CFA fallback topic: '{topic['topic']}' | "
        f"Concept: {topic.get('cfa_concept', 'N/A')} | Pillar: {pillar_name}"
    )
    return topic


def _add_video_format(topic: dict) -> dict:
    """
    Adds `suggested_video_format` and `is_breaking` to the topic dict.

    All pillars → Format D (FINVYON 10-scene 60s explainer).
    Breaking news is handled by the NEWS pillar in Format D (CFA analysis > reactive short clip).
    """
    PILLAR_FORMAT_MAP = {
        # New pillars — all FINVYON 10-scene explainer (Format D)
        "FUNDAMENTALS": "D",
        "NEWS":         "D",
        "COMPANY":      "D",
        "PORTFOLIO":    "D",
        "MINDSET":      "D",
        # Legacy fallbacks
        "STORY":    "D",
        "DATA":     "D",
        "OPINION":  "D",
        "QUESTION": "D",
        "INSIGHT":  "D",
    }

    BREAKING_KEYWORDS = [
        "breaking", "just in", "alert", "crash", "collapse", "surge",
        "plunge", "spike", "halt", "suspended", "emergency", "crisis",
    ]

    topic_text = (topic.get("topic", "") + " " + topic.get("hook", "")).lower()
    is_breaking = any(kw in topic_text for kw in BREAKING_KEYWORDS)

    if is_breaking:
        # Breaking news still uses Format D (FINVYON 10-scene) — NEWS pillar handles it with CFA analysis
        # Format E (15s reactive) is retired
        fmt = "D"
    else:
        pillar = topic.get("content_pillar", "STORY").upper()
        fmt = PILLAR_FORMAT_MAP.get(pillar, "B")

    topic["is_breaking"] = is_breaking
    topic["suggested_video_format"] = fmt
    logger.info(f"Video format: {fmt} | is_breaking: {is_breaking}")
    return topic
