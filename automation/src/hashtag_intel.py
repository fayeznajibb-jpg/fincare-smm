"""
Fincare Hashtag Intelligence
=============================
Rotates curated hashtag pools weekly per platform.
No stale tags. No API needed. Deterministic rotation via ISO week number.
"""

import os
import json
from datetime import datetime
from utils.logger import SecureLogger

logger = SecureLogger("hashtag_intel")

STATE_PATH = "logs/hashtag_rotation_state.json"

# ─────────────────────────────────────────
# MASTER HASHTAG POOLS — 4 pools per platform, rotate weekly
# ─────────────────────────────────────────

HASHTAG_POOLS = {
    "linkedin": [
        ["#PersonalFinance", "#BehavioralFinance", "#InvestingMindset", "#FinancialLiteracy",
         "#WealthBuilding", "#InvestorPsychology", "#SmartInvesting", "#MoneyMindset", "#Fincare"],
        ["#FinancialFreedom", "#RetailInvestors", "#InvestingTips", "#MarketPsychology",
         "#LongTermInvesting", "#MoneyManagement", "#FinancialWellness", "#Fintech", "#Fincare"],
        ["#InvestingForBeginners", "#StockMarket", "#PortfolioManagement", "#FinancialPlanning",
         "#WealthManagement", "#PassiveIncome", "#MoneyTips", "#AIFinance", "#Fincare"],
        ["#RetirementPlanning", "#FinancialIndependence", "#InvestmentStrategy", "#RiskManagement",
         "#CompoundInterest", "#MoneyPsychology", "#FinancialGoals", "#FintechInnovation", "#Fincare"],
    ],
    "instagram": [
        ["#personalfinance", "#investing", "#moneymindset", "#financialliteracy", "#wealthbuilding",
         "#investingtips", "#financialfreedom", "#behaviouralfinance", "#fincare", "#aifincare",
         "#moneyadvice", "#investorlife", "#richhabits"],
        ["#stockmarket", "#passiveincome", "#moneygoals", "#financialtips", "#investingforbeginners",
         "#wealthmindset", "#financialindependence", "#moneymotivation", "#fincare", "#aifincare",
         "#moneymanagement", "#buildwealth", "#smartmoney"],
        ["#retirementplanning", "#compoundinterest", "#moneytips", "#investmentmindset", "#fintech",
         "#aifinance", "#financialwellbeing", "#moneycoach", "#fincare", "#aifincare",
         "#investorpsychology", "#wealthcreation", "#financialgrowth"],
        ["#financialanxiety", "#moneystress", "#fomo", "#investorpsychology", "#panicSelling",
         "#emotionalinvesting", "#marketvolatility", "#investorlife", "#fincare", "#aifincare",
         "#mindfulinvesting", "#calmInvesting", "#financialwellness"],
    ],
    "tiktok": [
        ["#personalfinance", "#investing", "#moneytips", "#financialliteracy",
         "#wealthbuilding", "#fincare", "#aifinance", "#investingtips"],
        ["#stockmarket", "#passiveincome", "#moneyadvice", "#financialfreedom",
         "#investingforbeginners", "#fincare", "#aifinance", "#richtok"],
        ["#moneymindset", "#financialtips", "#wealthmindset", "#behaviouralfinance",
         "#investorpsychology", "#fincare", "#aifinance", "#moneymotivation"],
        ["#financialanxiety", "#emotionalinvesting", "#fomoinvesting", "#marketpsychology",
         "#smartinvesting", "#fincare", "#aifinance", "#moneytok"],
    ],
    "threads": [
        ["#personalfinance", "#investing", "#moneymindset", "#fincare", "#behaviouralfinance"],
        ["#stockmarket", "#financialfreedom", "#investingtips", "#fincare", "#wealthbuilding"],
        ["#moneytips", "#investorpsychology", "#fintech", "#fincare", "#aifinance"],
        ["#financialanxiety", "#emotionalinvesting", "#marketpsychology", "#fincare", "#smartinvesting"],
    ],
}

# Always included regardless of pool rotation
ALWAYS_INCLUDE = {
    "linkedin":  ["#Fincare"],
    "instagram": ["#fincare", "#aifincare"],
    "tiktok":    ["#fincare", "#aifinance"],
    "threads":   ["#fincare"],
}


def get_week_pool_index(num_pools: int) -> int:
    """Returns pool index for this week. Rotates every Monday."""
    iso_week = datetime.now().isocalendar()[1]
    return iso_week % num_pools


def get_hashtags_for_platform(platform: str, extras: list = None) -> str:
    """
    Returns the active hashtag set for a platform this week.
    Merges weekly pool + always-include tags + any extras.
    """
    pools = HASHTAG_POOLS.get(platform, [])
    if not pools:
        logger.warning(f"No hashtag pools found for platform: {platform}")
        return ""

    pool_index = get_week_pool_index(len(pools))
    pool = pools[pool_index].copy()

    # Add always-include tags
    for tag in ALWAYS_INCLUDE.get(platform, []):
        if tag not in pool and tag.lower() not in [t.lower() for t in pool]:
            pool.append(tag)

    # Add any extras
    if extras:
        for tag in extras:
            if tag not in pool:
                pool.append(tag)

    logger.info(f"{platform}: week pool {pool_index} → {len(pool)} hashtags")
    return " ".join(pool)


def get_all_hashtags() -> dict:
    """Returns hashtag strings for all platforms. Called by writer.py."""
    result = {
        "linkedin":  get_hashtags_for_platform("linkedin"),
        "instagram": get_hashtags_for_platform("instagram"),
        "tiktok":    get_hashtags_for_platform("tiktok"),
        "threads":   get_hashtags_for_platform("threads"),
    }
    save_rotation_state()
    return result


def save_rotation_state():
    """Saves the current week's active pool indices to logs/ for auditing."""
    os.makedirs("logs", exist_ok=True)
    state = {
        "week": datetime.now().isocalendar()[1],
        "year": datetime.now().year,
        "generated_at": datetime.now().isoformat(),
        "active_pools": {
            platform: get_week_pool_index(len(pools))
            for platform, pools in HASHTAG_POOLS.items()
        }
    }
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def get_rotation_report() -> str:
    """Returns a human-readable Telegram summary of this week's hashtag pools."""
    iso_week = datetime.now().isocalendar()[1]
    lines = [f"<b>🏷️ Hashtag Rotation — Week {iso_week}</b>\n"]
    for platform, pools in HASHTAG_POOLS.items():
        idx = get_week_pool_index(len(pools))
        sample = " ".join(pools[idx][:4]) + "..."
        lines.append(f"<b>{platform}:</b> Pool {idx+1}/{len(pools)} — {sample}")
    return "\n".join(lines)
