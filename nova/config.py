"""Environment configuration for the Nova external worker.

All configuration comes from environment variables (GitHub Actions secrets).
No secrets are ever logged or written to files.
"""
import os

# Base44 app (the storefront + database)
BASE44_APP_ID = os.environ.get("BASE44_APP_ID", "69a8ac47693d18e683d96447")
BASE44_SERVICE_TOKEN = os.environ.get("BASE44_SERVICE_TOKEN", "")
# Empty here is fine for offline tests; any real API call without it fails
# with a clear 401 at request time.
BASE44_API = f"https://base44.app/api/apps/{BASE44_APP_ID}/entities"

# The Odds API (https://the-odds-api.com) — optional; if absent, odds ingest skips.
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# LLM provider for narrative fields — optional; if absent, reads use
# deterministic template narratives derived from kernel decision fields.
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")  # Anthropic-style key
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5")


# LLM bridge (Base44 AI, Oct 16 2026): OpenAI is removed. The worker
# cannot reach Base44's gateway externally, so prompts go through the
# LLMTask queue; an app-side function runs them on Base44 AI credits.
# No queue entity -> honest fail-open, reads stay deterministic/cached.
LLM_QUEUE_ENTITY = os.environ.get("NOVA_LLM_QUEUE_ENTITY") or "LLMTask"
RESEARCH_DISABLED = os.environ.get("NOVA_RESEARCH", "") == "0"
RESEARCH_CONTEXT_SIZE = os.environ.get("NOVA_RESEARCH_CONTEXT", "high")
RESEARCH_MAX_GAMES = int(os.environ.get("NOVA_RESEARCH_MAX_GAMES", "6"))
RESEARCH_WINDOW_H = float(os.environ.get("NOVA_RESEARCH_WINDOW_H", "48"))
RESEARCH_TIMEOUT_S = int(os.environ.get("NOVA_RESEARCH_TIMEOUT_S", "180"))

# MODE 2 synthesis layer (structured game-construction) — runs on the
# Base44 AI bridge (LLMTask queue). Rollout: "fixture" (one game per
# cycle, verify before widening) | "slate" | "off". Fail-open: no queue
# entity -> MODE 0 baseline stands.
SYNTH_MODE = os.environ.get("NOVA_SYNTH_MODE") or "fixture"
SYNTH_SANITY_MAX = float(os.environ.get("NOVA_SYNTH_SANITY_MAX", "3.0"))
SYNTH_MAX_RANGE_WIDTH = float(os.environ.get("NOVA_SYNTH_MAX_RANGE_WIDTH", "6.0"))
SYNTH_MAX_GAMES = int(os.environ.get("NOVA_SYNTH_MAX_GAMES", "3"))
SYNTH_TIMEOUT_S = int(os.environ.get("NOVA_SYNTH_TIMEOUT_S", "240"))
# MODE 1 cached-synthesis freshness: a pack with unchanged inputs is
# re-applied (not re-synthesized) while younger than this (doctrine 43).
SYNTH_TTL_H = float(os.environ.get("NOVA_SYNTH_TTL_H", "2.0"))

# freshness budget by proximity to start (hours)
RESEARCH_TTL_NEAR = float(os.environ.get("NOVA_RESEARCH_TTL_NEAR", "2"))   # <4h to game
RESEARCH_TTL_MID = float(os.environ.get("NOVA_RESEARCH_TTL_MID", "6"))    # 4-24h
RESEARCH_TTL_FAR = float(os.environ.get("NOVA_RESEARCH_TTL_FAR", "12"))   # >24h

# Cycle behavior
LEAGUES = [l.strip().lower() for l in os.environ.get(
    "NOVA_LEAGUES", "nfl,ncaaf,nhl,nba,ncaab,wnba,mlb").split(",") if l.strip()]
LOOKAHEAD_DAYS = int(os.environ.get("NOVA_LOOKAHEAD_DAYS", "2"))
RECONCILE_BACK_DAYS = int(os.environ.get("NOVA_RECONCILE_BACK_DAYS", "5"))  # past-game status sync window
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
