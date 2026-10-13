"""Environment configuration for the Nova external worker.

All configuration comes from environment variables (GitHub Actions secrets).
No secrets are ever logged or written to files.
"""
import os

# Base44 app (the storefront + database)
BASE44_APP_ID = os.environ.get("BASE44_APP_ID", "69a8ac47693d18e683d96447")
BASE44_SERVICE_TOKEN = os.environ["BASE44_SERVICE_TOKEN"]  # required
BASE44_API = f"https://base44.app/api/apps/{BASE44_APP_ID}/entities"

# The Odds API (https://the-odds-api.com) — optional; if absent, odds ingest skips.
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# LLM provider for narrative fields — optional; if absent, reads use
# deterministic template narratives derived from kernel decision fields.
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")  # Anthropic-style key
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5")

# Cycle behavior
LEAGUES = [l.strip().lower() for l in os.environ.get(
    "NOVA_LEAGUES", "nfl,ncaaf,nhl,nba,ncaab,wnba,mlb").split(",") if l.strip()]
LOOKAHEAD_DAYS = int(os.environ.get("NOVA_LOOKAHEAD_DAYS", "2"))
DRY_RUN = os.environ.get("NOVA_DRY_RUN", "") == "1"

# Write throttling (be a polite client; keep Base44 API calls minimal)
WRITE_DELAY_S = float(os.environ.get("NOVA_WRITE_DELAY_S", "0.4"))

# ESPN endpoints (verified working with a plain urllib User-Agent)
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_LEAGUES = {
    "nfl":   "football/nfl",
    "ncaaf": "football/college-football",
    "ncaab": "basketball/mens-college-basketball",
    "nba":   "basketball/nba",
    "wnba":  "basketball/wnba",
    "nhl":   "hockey/nhl",
    "mlb":   "baseball/mlb",
}
ODDS_API_SPORTS = {
    "nfl":   "americanfootball_nfl",
    "ncaaf": "americanfootball_ncaaf",
    "nba":   "basketball_nba",
    "ncaab": "basketball_ncaab",
    "wnba":  "basketball_wnba",
    "nhl":   "icehockey_nhl",
    "mlb":   "baseball_mlb",
}

# Game statuses that never get scores; bridge-style reconciliation must exempt them.
TERMINAL_EXEMPT = {"canceled", "postponed", "suspended", "delayed"}

# Status taxonomy (V3 directive) — never invent new states.
STATUS_TAXONOMY = ["PLAY", "LEAN", "MONITOR", "PASS", "NUMBER GONE", "AWAITING DATA"]

# Edge sanity gate: |edge| never > 8 points vs true line (hard product rule).
MAX_EDGE_POINTS = 8.0
