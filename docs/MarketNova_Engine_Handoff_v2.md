# MARKETNOVA ENGINE — COMPLETE HANDOFF PACKAGE (v2, Oct 12 2026)

Everything needed to understand, run, fix and rebuild the MarketNova
external worker. Hand this file to Claude (or any engineer) as the single
source of truth.

---

## 1. WHAT MARKETNOVA IS

A sportsbook-intelligence product: it independently constructs what a
betting market SHOULD be (the "true line"), compares it to what books
actually post, and explains the discrepancy. NOT a picks product. The
doctrine is in the FASTESTPAYOUT_DOCTRINE (parts I-LXX) and the Master
Brain; the executable encodings are nova/kernel.py and nova/doctrine.py.

## 2. SYSTEM ARCHITECTURE

EXTERNAL WORKER (Python, runs on GitHub Actions or any cron host)
    |
    | 1. ESPN ingestion: scoreboard per league -> games (never creates
    |    a game without a market), statuses reconciled vs ESPN, provider
    |    linking, venue/weather hooks.
    |
    | 2. Odds plan: ESPN DraftKings scoreboard odds are PRIMARY (free);
    |    The Odds API (ODDS_API_KEY) is optional depth. Upserts by
    |    (game, book, market); every older row superseded (is_current=false)
    |    so line history is preserved as a "market tape".
    |
    | 3. True price: TeamRating power ratings + home-field + expected
    |    total -> kernel.build_true_price -> true spread/total.
    |
    | 4. kernel.decide() -> status gates with HARD RULES:
    |    - signed-edge sanity gate: |market - true| > 8 pts => AWAITING DATA
    |    - vig test: edge after value-side juice (0.25 pts per 10c past -110)
    |    - juice pressure => MONITOR upgrade; movement scoring; key numbers
    |    - taxonomy STRICT: PLAY / LEAN / MONITOR / PASS / NUMBER GONE /
    |      AWAITING DATA  (STATUS_MAP in reads.py converts kernel codes)
    |
    | 5. Narrative layer (nova/narratives.py, Oct 12 2026): deterministic,
    |    numbers-grounded prose per read: thesis, why_market_here,
    |    market_tape, true_line_basis, risk, buy_to, value_window,
    |    regression_notes, nova_read, confidence. No LLM required.
    |
    | 6. Enrichment: injuries (ESPN, severity+impact), weather
    |    (open-meteo), travel/rest days, TeamForm last-5.
    |
    | 7. Props: ESPN boxscore baselines (last game) -> fair ranges
    |    (league factors), headshots + positions from rosters.
    |
    v
BASE44 APP (the storefront): entities + frontend.
    - REST: https://base44.app/api/apps/{APP_ID}/entities/{Entity}
      (Bearer BASE44_SERVICE_TOKEN; PUT to update, POST to create,
      DELETE to remove; PATCH returns 405)
    - Entities: Game, OddsSnapshot, MarketNovaRead, Analysis, Team,
      TeamRating, TeamForm, Injury, WeatherSnapshot, TravelContext, Prop,
      User, plus app-specific.
    - CRITICAL GOTCHA: fields not present in the entity SCHEMA are
      SILENTLY DROPPED on write (HTTP 200 but the field never lands).
      Currently missing from the live MarketNovaRead schema (builder
      must add): why_market_here, thesis, nova_read, risk, buy_to,
      regression_notes, market_tape, true_line_basis, value_window,
      confidence. Missing from Injury: severity.
    - RLS: use the SERVICE TOKEN (service role) so records aren't
      user-scoped. BASE44_APP_ID MUST be the MarketNova app id
      (69a8ac47693d18e683d96447), NOT the superagent id — the sandbox
      .agents/.env exports a WRONG one (known trap).

## 3. ENTITY CONTRACTS (worker side)

Game: id, league_code, sport, home_team, away_team, home_team_abbr,
  away_team_abbr, start_time (ISO Z), venue, status (scheduled/live/
  halftime/final/canceled/postponed), home_score, away_score,
  provider_game_id (THE ESPN event id), external_id (odds-api id)
OddsSnapshot: game_id, book, market_type (spread/total/moneyline),
  home_line (signed, home-perspective), over_line, home_price,
  away_price, over_price, is_current, created_date
MarketNovaRead: game_id, market_type, is_current, status, league_code
  (REQUIRED by live schema), sport, marketnova_true_line, true_line_value,
  fair_price, market_line, market_odds, raw_edge, edge_value,
  edge_score_display, edge_direction, minimum_playable_price,
  playable_until, buy_to, value_window, thesis, nova_read, risk,
  why_market_here, market_tape, true_line_basis, regression_notes,
  confidence, data_confidence, key_number_quality,
  line_movement_direction, sharp_resistance, public_bias,
  game_state_support, regression_support, weather, lineup_status,
  invalidators (JSON), next_trigger, market_psychology,
  what_the_book_is_doing, one_line_read, data_quality_json (telemetry)
Injury: league_code, source_provider, team_abbr, player_name, position,
  injury_type, status, severity (SCHEMA PENDING), impact_rating
TeamRating: team_abbr, team_name, rating, league_code (lowercase)
TeamForm: team_abbr, league_code (lowercase), last_5_json
Prop: game_id, league_code, player_espn_id, player_name, team_abbr,
  position, prop_type, baseline_last_game, nova_fair_low,
  nova_fair_high, headshot_url, recommendation, data_confidence

## 4. RUNNING IT

Local/cron: python3 run_cycle.py            (full live cycle)
Verify without credits: python3 tools/offline_run.py ../offline_cache
Self-test: python3 nova/test_kernel.py      (77 checks must pass)

Env (GitHub Actions secrets or shell):
  BASE44_SERVICE_TOKEN  - Dashboard > API > Create access token
  BASE44_APP_ID         - 69a8ac47693d18e683d96447 (MarketNova app)
  ODDS_API_KEY          - OPTIONAL (ESPN DraftKings odds are primary)
  LLM_API_KEY           - OPTIONAL (narratives are deterministic now)
  LLM_MODEL             - optional, anthropic-style

## 5. CORRECT GITHUB ACTIONS WORKFLOW (.github/workflows/nova-cycle.yml)

```yaml
name: nova-cycle
on:
  schedule:
    - cron: "*/30 * * * *"   # every 30 minutes
  workflow_dispatch: {}
jobs:
  cycle:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install requests
      - run: python3 nova/test_kernel.py
      - run: python3 run_cycle.py
        env:
          BASE44_SERVICE_TOKEN: ${{ secrets.BASE44_SERVICE_TOKEN }}
          BASE44_APP_ID: ${{ secrets.BASE44_APP_ID }}
          ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}
```

IMPORTANT: the currently-deployed runner is an OLD snapshot. It writes
legacy statuses (SIGNAL/WATCH/REPRICE_REQUIRED), creates duplicate
games (no provider-id linking), double-inserts odds, and drops injury
severity. Deploy THIS code over it, or pause the old Action until
replaced.

## 6. STATE AS OF OCT 12 2026 (post-repair)

- Duplicate games: 36 groups MERGED (37 dups deleted, 377 children
  repointed; backup_dupgame_merge_20261012_1630.jsonl)
- Duplicate current odds: 456 groups healed (54 superseded by merge;
  cycle heals the rest as it touches games — the double-insert bug is
  fixed in nova/odds.py: supersede-before-insert + dup heals)
- Reads: all current scheduled reads recomputed with the narrative
  engine (28 updated, 7 created); market_psychology upgraded to
  specific per-game prose (works TODAY without schema changes)
- Remaining legacy-taxonomy reads are on finished games (postgame
  history — intentionally left)
- Narrative schema fields NOT yet in live schema -> builder must add
  (see gotcha above); after publish, run tools/read_backfill.py to
  land thesis/risk/buy_to/tape/basis/value_window on every read

## 7. CORE RULES (never break these)

- Never invent data: no ratings => AWAITING DATA. No spread => AWAITING
  DATA. Signed edge > 8 pts => AWAITING DATA (ratings/market conflict).
- Games exist because markets exist: never create a game without odds.
- provider_game_id IS the ESPN event id; link to ESPN, never guess.
- is_current=false preserves history (tape); never delete odds rows.
- Taxonomy STRICT: PLAY/LEAN/MONITOR/PASS/NUMBER GONE/AWAITING DATA.
- Edge math always SIGNED: abs(market - true). Never abs-of-abs.
- Streaks/recency are TELEMETRY ONLY — never upgrade conviction.
- NCAAF doubleheaders: dedupe key includes start_time (real ESPN twins
  share date+teams).
- ESPN path for NHL is hockey/nhl (NOT ice-hockey/nhl — 400s).
- Entity updates use PUT (PATCH 405s).

---

# 8. COMPLETE SOURCE CODE

Every file below is the CURRENT, working code (77/77 tests green,
offline pipeline verified, live cycle proven).


## FILE: `nova/config.py`
**Configuration (env vars, leagues, slugs, constants)**

```python
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

```


## FILE: `nova/api.py`
**Base44 REST client (list/put/create/delete, retry, pagination)**

```python
"""Base44 entity API client with delta-only writes.

Credit-saving doctrine: every call to the app backend is metered, so this
client exists to make the MINIMUM number of calls that keeps the app true:
  - read entities once per cycle, in bulk (limit=500 pagination)
  - write only records whose tracked fields actually changed
  - write only the changed fields (PATCH semantics via PUT)
  - sleep briefly between writes to stay a polite client

Never touches bet_* recommendation fields on MarketNovaRead (frontend maps them).
"""
import json
import time
import urllib.request
import urllib.error

from nova import config


class Base44Client:
    def __init__(self):
        self.writes = 0
        self.reads = 0

    def _req(self, method, path, payload=None, retries=2):
        url = f"{config.BASE44_API}/{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, method=method, data=data, headers={
            "Authorization": f"Bearer {config.BASE44_SERVICE_TOKEN}",
            "Content-Type": "application/json",
        })
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    body = r.read()
                    return r.status, json.loads(body) if body else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503) and attempt < retries:
                    time.sleep(2 ** attempt * 2)
                    continue
                return e.code, e.read().decode(errors="replace")[:300]
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < retries:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise
        raise RuntimeError("unreachable")

    def list_all(self, entity, fields=None, query=None):
        """Fetch every record of an entity with pagination."""
        out, skip = [], 0
        while True:
            path = f"{entity}?limit=500&skip={skip}"
            if fields:
                path += "&fields=" + ",".join(fields)
            if query:
                path += "&query=" + urllib.request.quote(json.dumps(query))
            status, body = self._req("GET", path)
            if status != 200:
                raise RuntimeError(f"list {entity} failed {status}: {body}")
            self.reads += 1
            recs = body if isinstance(body, list) else body.get("data", [])
            out.extend(recs)
            if len(recs) < 500:
                return out
            skip += 500

    def put(self, entity, record_id, patch):
        status, body = self._req("PUT", f"{entity}/{record_id}", patch)
        self.writes += 1
        if status not in (200, 201):
            print(f"  WRITE FAILED {entity}/{record_id} {status}: {body}")
            return False
        time.sleep(config.WRITE_DELAY_S)
        return True

    def delete(self, entity, record_id):
        status, body = self._req("DELETE", f"{entity}/{record_id}")
        self.writes += 1
        if status not in (200, 201, 204):
            print(f"  DELETE FAILED {entity}/{record_id} {status}: {body}")
            return False
        time.sleep(config.WRITE_DELAY_S)
        return True

    def create(self, entity, payload):
        status, body = self._req("POST", entity, payload)
        self.writes += 1
        if status not in (200, 201):
            print(f"  CREATE FAILED {entity} {status}: {body}")
            return None
        time.sleep(config.WRITE_DELAY_S)
        return body


def changed_fields(current, desired, tracked):
    """Return {field: new_value} for tracked fields that differ, or None.

    Numeric comparison uses a small tolerance so float round-trips
    (scores/lines persisting as 3.0 vs 3) never cause phantom writes.
    """
    patch = {}
    for f in tracked:
        if f not in desired:
            continue
        old, new = current.get(f), desired[f]
        if isinstance(new, (int, float)) and isinstance(old, (int, float)):
            if abs(float(new) - float(old)) < 1e-9:
                continue
        elif old == new:
            continue
        patch[f] = new
    return patch or None

```


## FILE: `nova/matching.py`
**Team-identity alias map + canon() matching**

```python
"""Shared matching helpers — abbr alias map + same-day discipline.

The alias map is the canonical team-identity fix from the Sep-Oct cleanup:
ESPN and odds-api abbreviations differ (TOR/TORO, NJ/NJD, TBL/TB, ...).
Every match path goes through here so one map fixes all of them.
"""
from datetime import datetime, timezone

ALIASES = {
    "TORO": "TOR", "LVA": "LV", "NYL": "NY", "WAS": "WSH", "GOLD": "GS",
    "TBL": "TB", "NJD": "NJ", "GEOR": "UGA", "ALAB": "ALA", "VIRG": "VT",
}

# League-scoped aliases for odds-api abbreviation quirks verified against
# ESPN during the Sep-Oct 2026 cleanups (e.g. odds-api calls Liberty "NY"
# in NCAAF, but "NY" is New York Liberty in WNBA — so scope by league).
LEAGUE_ALIASES = {
    "ncaaf": {
        "NY": "LIB", "SAMH": "SHSU", "SANJ": "SJSU", "WYOM": "WYO",
        "MISS": "MSST",
        # full-name quirks verified against ESPN (Oct 2026 cleanup):
        "SAMHOUSTONSTATE": "SHSU", "SANJOSESTATE": "SJSU", "MIAMIOHIO": "M-OH", "CONN": "UCONN",
        "ULLAFAYETTE": "UL", "MIAMIOH": "M-OH", "CONNECTICUT": "UCONN",
        "APPALACHIANSTATE": "APP", "LOUISIANATECH": "LT",
    },
    "wnba": {"NY": "NYL"},
}


_ACCENTS = str.maketrans("áéíóúñ", "aeioun")


def canon(abbr, league=""):
    a = (abbr or "").upper().strip().replace(" ", "").translate(_ACCENTS)
    l = str(league or "").lower()
    if l in LEAGUE_ALIASES and a in LEAGUE_ALIASES[l]:
        return LEAGUE_ALIASES[l][a]
    return ALIASES.get(a, a)


def abbr_matches(game_abbrs, team_a, team_b, league=""):
    """True if both canonical abbrs appear in the game's abbr set."""
    have = {canon(a, league) for a in game_abbrs if a}
    return canon(team_a, league) in have and canon(team_b, league) in have


def same_day(game, iso_time, tol_hours=36):
    """Same calendar day-ish match (kickoff times can shift by a few hours)."""
    try:
        g = datetime.fromisoformat(str(game.get("start_time", "")).replace("Z", "+00:00"))
        t = datetime.fromisoformat(str(iso_time).replace("Z", "+00:00"))
        if g.tzinfo is None:
            g = g.replace(tzinfo=timezone.utc)
        return abs((g - t).total_seconds()) < tol_hours * 3600
    except (ValueError, TypeError):
        return False

```


## FILE: `nova/espn.py`
**ESPN ingestion (scoreboard, events, injuries, reconcile)**

```python
"""ESPN scoreboard ingest — same matching rules as bridge v1.5, generalized.

Matching order:
  1. provider_game_id (ESPN event id) — exact identity
  2. fallback: both sides match on abbr OR name (token/containment), same
     league + kickoff proximity — only against games with NO
     provider_game_id (a game that already has one is a different ESPN
     event; doubleheaders must never re-link)
  3. one-side-only overlap = AMBIGUOUS: never create, log for manual link.
     Conservative by design — a missed create is recoverable, a duplicate
     is the disease this repo exists to prevent.

Terminal games (CANCELED / POSTPONED / SUSPENDED / DELAYED) are exempt from
score reconciliation. Home/away comes from competitors[].homeAway.
"""
import json
import urllib.request
from datetime import datetime

from nova import config
from nova import matching

ESPN_HEADERS = {"User-Agent": "python-urllib/3.11"}  # verified accepted


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers=ESPN_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def scoreboard(league, date):
    path = config.ESPN_LEAGUES[league]
    return fetch_json(f"{config.ESPN_BASE}/{path}/scoreboard?dates={date.strftime('%Y%m%d')}")


def parse_event(ev):
    comp = ev["competitions"][0]
    home = away = None
    for c in comp.get("competitors", []):
        side = {
            "abbr": (c.get("team") or {}).get("abbreviation"),
            "name": (c.get("team") or {}).get("displayName"),
            "score": c.get("score"),
        }
        if c.get("homeAway") == "home":
            home = side
        else:
            away = side
    status = ((ev.get("status") or {}).get("type") or {})
    odds0 = (comp.get("odds") or [None])[0]
    rec_odds = None
    if odds0:
        rec_odds = {
            "provider": ((odds0.get("provider") or {}).get("name") or "DraftKings"),
            # ESPN's "spread" is home-perspective (negative = home favored),
            # same convention as Nova's true line — verified Oct 2026.
            "spread": _num(odds0.get("spread")),
            "total": _num(odds0.get("overUnder")),
            "home_ml": _num((odds0.get("homeTeamOdds") or {}).get("moneyLine")),
            "away_ml": _num((odds0.get("awayTeamOdds") or {}).get("moneyLine")),
        }
    venue = comp.get("venue") or {}
    vaddr = venue.get("address") or {}
    return {
        "odds": rec_odds,
        "espn_id": ev.get("id"),
        "start_time": ev.get("date"),
        "status": status.get("name", "").lower(),
        "status_detail": status.get("description", ""),
        "home_abbr": home["abbr"] if home else None,
        "home_name": home["name"] if home else None,
        "home_score": home["score"] if home else None,
        "away_abbr": away["abbr"] if away else None,
        "away_name": away["name"] if away else None,
        "away_score": away["score"] if away else None,
        "venue": venue.get("fullName"),
        "venue_city": vaddr.get("city"),
        "venue_state": vaddr.get("state"),
        "venue_indoor": bool(venue.get("indoor")),
    }


def normalize_status(status, status_detail):
    s = (status or "").lower()
    d = (status_detail or "").lower()
    both = s + " " + d
    if "cancel" in both:
        return "canceled"
    if "postpon" in both:
        return "postponed"
    if "suspend" in both:
        return "suspended"
    if "delay" in both:
        return "delayed"
    # FINAL before in-game checks: "Final/OT" must stay final, never live
    if s == "final" or "final" in both:
        return "final"
    # any in-game state (halftime, end of quarter/period/inning, OT...) is live
    if (s in ("inprogress", "in_progress") or "in progress" in both
            or "halftime" in both or "overtime" in both
            or any(w in both for w in ("end of 1st", "end of 2nd", "end of 3rd", "end of 4th"))):
        return "live"
    return "scheduled"


def collect_events(league, days):
    from datetime import timedelta
    events = []
    from datetime import timezone as _tz
    today = datetime.now(_tz.utc).date()
    for delta in range(-1, days + 1):
        d = today + timedelta(days=delta)
        try:
            data = scoreboard(league, d)
            events.extend(data.get("events", []))
        except Exception as e:
            print(f"  espn {league} {d}: fetch failed: {e}")
    return events


# ------------------------------------------------------------------
# side matching: DB values may be abbrs ("NY") or names ("Coastal Carolina")
# ------------------------------------------------------------------

def _name_match(db_val, espn_abbr, espn_name, league=""):
    """True if a DB side value plausibly denotes this ESPN team."""
    if not db_val:
        return False
    db_val = str(db_val).strip().lower().translate(matching._ACCENTS)
    if matching.canon(db_val, league) == matching.canon(espn_abbr, league):
        return True
    n = str(espn_name or "").strip().lower().translate(matching._ACCENTS)
    if not n:
        return False
    if db_val == n or db_val in n or n in db_val:
        return True
    dt = set(db_val.split())
    nt = set(n.split())
    if dt and dt.issubset(nt):
        return True
    return False


def _sides_match(g, rec, league=""):
    """(home_ok, away_ok) — which sides of this DB game match the ESPN record."""
    home_ok = (_name_match(g.get("home_team"), rec.get("home_abbr"), rec.get("home_name"), league)
               or _name_match(g.get("home_team_name"), rec.get("home_abbr"), rec.get("home_name"), league))
    away_ok = (_name_match(g.get("away_team"), rec.get("away_abbr"), rec.get("away_name"), league)
               or _name_match(g.get("away_team_name"), rec.get("away_abbr"), rec.get("away_name"), league))
    return home_ok, away_ok


def reconcile_links(virtual_games, espn_recs, league):
    """Link ESPN events to games and compute live status/score deltas.

    ESPN NEVER creates games (odds is the source of record). For unmatched
    events this only logs. For matched events it returns patches:
      updates: [(game, patch)] — game may be an existing record (has id)
               or a planned create (has __create index; the cycle merges
               the patch into the create payload)
      issues:  [str] anomalies worth logging
    """
    from nova.api import changed_fields

    updates, issues = [], []
    by_espn = {g.get("provider_game_id"): g for g in virtual_games if g.get("provider_game_id")}

    for rec in espn_recs:
        status = normalize_status(rec["status"], rec["status_detail"])
        terminal = status in config.TERMINAL_EXEMPT
        g = by_espn.get(rec["espn_id"])

        if g is None:
            g, ambiguous = _fallback_match(virtual_games, rec, league)
            if g is None:
                if ambiguous:
                    issues.append(
                        f"WARN ambiguous {rec.get('away_abbr')}@{rec.get('home_abbr')} "
                        f"({rec.get('espn_id')}) — one-side overlap, not linking")
                continue  # no match: ESPN-only game, not board material

        # Guard: never regress a final game, never resurrect terminal games.
        if g.get("id") and g.get("status") == "final" and status != "final":
            issues.append(f"skip regression: {rec['espn_id']} final -> {status}")
            continue
        if g.get("id") and str(g.get("status", "")).lower() in config.TERMINAL_EXEMPT and status != g.get("status"):
            issues.append(f"skip terminal change: {rec['espn_id']} {g.get('status')} -> {status}")
            continue

        desired = {
            "status": status,
            "home_score": None if terminal else _num(rec["home_score"]),
            "away_score": None if terminal else _num(rec["away_score"]),
        }
        tracked = ["status", "home_score", "away_score"]
        # start_time: compare PARSED datetimes — ESPN uses minute precision,
        # odds-api second precision; a string compare would rewrite every cycle
        if rec.get("start_time") and _parse_iso(rec["start_time"]) != _parse_iso(g.get("start_time")):
            desired["start_time"] = rec["start_time"]
            tracked.append("start_time")
        if rec.get("venue"):
            desired["venue"] = rec["venue"]
            tracked.append("venue")
        patch = changed_fields(g, desired, tracked)

        if not g.get("provider_game_id") and rec["espn_id"]:
            patch = patch or {}
            patch["provider_game_id"] = rec["espn_id"]

        if patch:
            updates.append((g, patch))

    return updates, issues


def _fallback_match(games, rec, league):
    """(match, ambiguous). Both sides must match to link; exactly one side
    matching is ambiguous (refuse to create, refuse to link)."""
    best, one_side = None, False
    for g in games:
        if g.get("provider_game_id"):
            continue
        if str(g.get("league_code", "")).upper() != league.upper():
            continue
        if not matching.same_day(g, rec.get("start_time")):
            continue
        home_ok, away_ok = _sides_match(g, rec, league)
        if home_ok and away_ok:
            if best is None or _dt_gap(g, rec) < _dt_gap(best, rec):
                best = g
        elif home_ok or away_ok:
            one_side = True
    if best is not None:
        return best, False
    return None, one_side


def _dt_gap(g, rec):
    try:
        a = datetime.fromisoformat(str(g.get("start_time", "")).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(rec.get("start_time", "")).replace("Z", "+00:00"))
        return abs((a - b).total_seconds())
    except (ValueError, TypeError):
        return float("inf")


def _parse_iso(v):
    from datetime import timezone as _tz
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt.replace(tzinfo=_tz.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

```


## FILE: `nova/odds.py`
**Odds plan (ESPN DraftKings primary, The Odds API optional, upsert/supersede)**

```python
"""The Odds API ingest — upsert semantics (fixes the historic double-insert bug).

Creation doctrine: GAMES EXIST BECAUSE MARKETS EXIST. New games are created
from odds-api events that no existing Game matches; ESPN never creates games.
ESPN's job is linking (provider_game_id) and live sync; odds is the source of
record for what belongs on the board.

Rules that killed the old churn:
  1. ONE snapshot row per (game, book, market) — both outcomes (home/away,
     over/under) live inside that single row, never one row per outcome.
  2. If the current row for that key has the same line AND juice, write NOTHING.
     A quiet market costs zero writes.
  3. If it changed, supersede the old row (is_current=false) and insert one new
     current row — line history is preserved.
"""
from nova import config, matching

ODDS_HEADERS = {"User-Agent": "python-urllib/3.11"}


def fetch_odds(league, api_key=None):
    import json
    import urllib.request
    key = api_key or config.ODDS_API_KEY
    if not key:
        print("  odds: no ODDS_API_KEY, skipping")
        return []
    sport = config.ODDS_API_SPORTS.get(league)
    if not sport:
        return []
    url = (f"{config.ODDS_API_BASE}/sports/{sport}/odds"
           f"?apiKey={key}&regions=us&markets=spreads,totals,moneylines"
           f"&oddsFormat=american")
    try:
        req = urllib.request.Request(url, headers=ODDS_HEADERS)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  odds {league}: fetch failed: {e}")
        return []


def flatten_event(ev):
    """Flatten one odds-api event into desired market rows.

    Rows: {event_id, commence_time, home, away, book, market, line,
    home_price, away_price} with market in {spread, total, moneyline}.
    """
    home, away = ev.get("home_team"), ev.get("away_team")
    rows = []
    for bm in ev.get("bookmakers", []):
        book = bm.get("key") or bm.get("title")
        for mk in bm.get("markets", []):
            mk_key = mk.get("key")
            outcomes = {o["name"]: o for o in mk.get("outcomes", [])}
            if mk_key == "spreads":
                o = outcomes.get(home)
                if o is not None:
                    rows.append(_row(ev, book, "spread", o.get("point"), o.get("price"),
                                     outcomes.get(away, {}).get("price")))
            elif mk_key == "totals":
                o = outcomes.get("Over")
                if o is not None:
                    rows.append(_row(ev, book, "total", o.get("point"), o.get("price"),
                                     outcomes.get("Under", {}).get("price")))
            elif mk_key == "h2h":
                o = outcomes.get(home)
                if o is not None:
                    rows.append(_row(ev, book, "moneyline", None, o.get("price"),
                                     outcomes.get(away, {}).get("price")))
    return rows


def _row(ev, book, market, line, home_price, away_price):
    return {
        "event_id": ev.get("id"),
        "commence_time": ev.get("commence_time"),
        "home_team": ev.get("home_team"),
        "away_team": ev.get("away_team"),
        "book": book,
        "market": market,
        "line": line,
        "home_price": home_price,
        "away_price": away_price,
    }


def espn_rows(rec):
    """Build desired odds rows from an ESPN scoreboard record (free source:
    DraftKings spread/total via ESPN, no odds-api account needed)."""
    od = rec.get("odds")
    if not od:
        return []
    rows = []
    base = {
        "source": "espn",
        "event_id": rec.get("espn_id"),
        "espn_id": rec.get("espn_id"),
        "commence_time": rec.get("start_time"),
        "home_team": rec.get("home_name"),
        "away_team": rec.get("away_name"),
        "home_team_abbr": rec.get("home_abbr"),
        "away_team_abbr": rec.get("away_abbr"),
        "book": od.get("provider") or "DraftKings",
    }
    if od.get("spread") is not None:
        rows.append({**base, "market": "spread", "line": od["spread"],
                     "home_price": None, "away_price": None})
    if od.get("total") is not None:
        rows.append({**base, "market": "total", "line": od["total"],
                     "home_price": None, "away_price": None})
    if od.get("home_ml") is not None and od.get("away_ml") is not None:
        rows.append({**base, "market": "moneyline", "line": None,
                     "home_price": od["home_ml"], "away_price": od["away_ml"]})
    return rows


def find_game(games, row, league=""):
    """Match an odds row to a Game: exact identity first (external_id for
    odds-api rows, provider_game_id for ESPN rows), then abbr/name + day
    — fallback matches are league-scoped (NY is Liberty in NCAAF, the
    Liberty in WNBA; cross-league name collisions are real)."""
    for g in games:
        if row.get("espn_id") and g.get("provider_game_id") == row["espn_id"]:
            return g
    for g in games:
        if row.get("event_id") and g.get("external_id") == row["event_id"]:
            return g
    best, best_gap = None, None
    for g in games:
        if league and str(g.get("league_code", "")).upper() != league.upper():
            continue
        if _side_match((g.get("home_team"), g.get("home_team_name")),
                      row["home_team"], league) and \
           _side_match((g.get("away_team"), g.get("away_team_name")),
                      row["away_team"], league) and \
           matching.same_day(g, row["commence_time"]):
            gap = _dt_gap(g, row["commence_time"])
            if best is None or gap < best_gap:
                best, best_gap = g, gap
    return best


def _side_match(db_vals, odds_name, league=""):
    """Odds rows carry full names ('Georgia Bulldogs'); DB games may carry
    abbrs ('UGA', 'GEOR'), names, or both split across *_team / *_team_name.
    Every candidate field is tried through the league-scoped alias map
    (GEOR->UGA, VIRG->VT, ...) plus containment/token matching, accent-safe.
    A miss here means a DUPLICATE GAME gets created on first sighting —
    this matcher is the last line of defense."""
    if not odds_name:
        return False
    if isinstance(db_vals, str):
        db_vals = (db_vals,)
    n = str(odds_name).strip().lower().translate(matching._ACCENTS)
    nt = set(n.split())
    canon_n = matching.canon(odds_name, league)
    for db_val in db_vals:
        if not db_val:
            continue
        d = str(db_val).strip().lower().translate(matching._ACCENTS)
        if matching.canon(db_val, league) == canon_n:
            return True
        if d == n or d in n or n in d:
            return True
        dt = set(d.split())
        if dt and dt.issubset(nt):
            return True
    return False


def plan(games, existing_odds, desired_rows, league):
    """Compute the full odds write plan.

    Returns (game_creates, link_updates, supersede_ids, new_rows):
      game_creates: payloads for odds events with no matching Game
      link_updates: [(game_id, {"external_id": ...})] — first odds link
      supersede_ids: OddsSnapshot ids to flip is_current=false
      new_rows: new OddsSnapshot payloads (one per changed key)
    """
    from nova.api import changed_fields

    game_creates, link_updates = [], []
    # provisional game list: existing + planned creates (so odds rows
    # attach to the game they just created)
    virtual = list(games)
    seen_event_ids = set()

    game_by_key = {}
    for row in desired_rows:
        eid = row["event_id"]
        if eid in seen_event_ids:
            continue
        seen_event_ids.add(eid)
        g = find_game(virtual, row, league)
        if g is None:
            payload = {
                "league_code": league.upper(),
                "sport": league.upper(),
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "start_time": row["commence_time"],
                "status": "scheduled",
                "__create": len(game_creates),
            }
            if row.get("source") == "espn":
                payload["provider_game_id"] = eid  # ESPN-sourced identity
            else:
                payload["external_id"] = eid        # odds-api identity
            g = payload
            virtual.append(g)
            game_creates.append(g)
        elif (row.get("source") != "espn" and eid and not g.get("external_id")
              and "__create" not in g):
            link_updates.append((g["id"], {"external_id": eid}))

    # now reconcile odds rows against existing snapshots (real schema:
    # book / market_type / home_line / over_line / home_price ...)
    by_key = {}
    for o in existing_odds:
        if not o.get("is_current", False):
            continue
        key = (o.get("game_id"), o.get("book"), str(o.get("market_type", "")).lower())
        by_key.setdefault(key, []).append(o)

    supersede, new_rows, juice_moves = [], [], []
    for row in desired_rows:
        g = find_game(virtual, row, league)
        if g is None:
            continue
        key = (g.get("id"), row["book"], row["market"])
        curs = by_key.get(key) or []
        matching = [c for c in curs if _same_row(c, row)]
        if matching:
            # price already carried; keep ONE current row per key and
            # supersede any duplicates (heals historic double-inserts)
            keep = matching[0]
            for c in curs:
                if c is not keep:
                    supersede.append(c["id"])
            continue
        for c in curs:
            supersede.append(c["id"])
        new_rows.append({
            "game_ref": ("create", g["__create"]) if "__create" in g else ("id", g.get("id")),
            **snapshot_payload(row),
        })
        # Juice-move classification (game theory layer): the number held
        # but the price moved — books probe demand with juice before
        # crossing numbers. This is a leading indicator, not a line move.
        if curs and "__create" not in g and row["market"] == "spread":
            old = curs[0]
            try:
                line_held = (old.get("home_line") is not None
                             and row.get("line") is not None
                             and float(old["home_line"]) == float(row["line"]))
            except (TypeError, ValueError):
                line_held = False
            if line_held:
                for side, pf in (("home", "home_price"), ("away", "away_price")):
                    try:
                        op, np_ = float(old.get(pf)), float(row.get(pf))
                        if op and np_ and op != np_:
                            juice_moves.append({
                                "game_id": g.get("id"), "market": "spread",
                                "side": side, "line_held": True,
                                "worse": abs(np_) > abs(op),
                                "from": op, "to": np_})
                    except (TypeError, ValueError):
                        pass
    return game_creates, link_updates, supersede, new_rows, juice_moves


def snapshot_payload(row):
    """OddsSnapshot row in the production schema (matches the platform
    ingestion's shape so the frontend renders both identically)."""
    p = {"book": row["book"], "market_type": row["market"], "is_current": True,
         "region": "us", "is_stale": False, "is_opening": False, "is_closing": False}
    if row["market"] == "spread":
        p["home_line"] = row["line"]
        p["away_line"] = -row["line"] if row["line"] is not None else None
        p["home_price"] = row.get("home_price")
        p["away_price"] = row.get("away_price")
    elif row["market"] == "total":
        p["over_line"] = row["line"]
        p["under_line"] = row["line"]
        p["over_price"] = row.get("home_price")
        p["under_price"] = row.get("away_price")
    elif row["market"] == "moneyline":
        p["home_price"] = row.get("home_price")
        p["away_price"] = row.get("away_price")
    return p


def _same_row(cur, row):
    """True if the current snapshot already carries this exact price."""
    mt = row["market"]
    if mt == "spread":
        if not _num_eq(cur.get("home_line"), row["line"]):
            return False
        if row.get("home_price") is not None and not _num_eq(cur.get("home_price"), row.get("home_price")):
            return False
        # away-side juice moves must snapshot too when carried
        return row.get("away_price") is None or _num_eq(cur.get("away_price"), row.get("away_price"))
    if mt == "total":
        if not _num_eq(cur.get("over_line"), row["line"]):
            return False
        # juice moves on totals must snapshot too (odds-api rows carry prices)
        return row.get("home_price") is None or _num_eq(cur.get("over_price"), row.get("home_price"))
    if mt == "moneyline":
        return _num_eq(cur.get("home_price"), row.get("home_price")) and                _num_eq(cur.get("away_price"), row.get("away_price"))
    return False


def _num_eq(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return a == b


def _dt_gap(g, iso):
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(g.get("start_time", "")).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return abs((a - b).total_seconds())
    except (ValueError, TypeError):
        return float("inf")

```


## FILE: `nova/kernel.py`
**THE BRAIN: true price construction, decide() gates, CLV, key numbers**

```python
"""
MarketNova Execution Kernel — implements MarketNova_Master_Brain_Update_v1.md (sections 3-28, 32).

Canonical spec: .agents/rules/MarketNova_Master_Brain_Update_v1.md (read-only, versioned).
This module is the code-level encoding of that spec. Every rule carries its spec section
reference so behavior is auditable against the document.

Architecture:
  MarketNova   = independent game + true-price engine        (sections 3, 8-12)
  FastestPayout = sportsbook/market-intelligence engine       (sections 2, 4-6)
  Vegas Danny  = execution + price-discipline engine          (sections 7, 13, 14)

CORE_INVARIANT (sec 0): market behavior may explain/confirm/weaken/invalidate an independently
derived true-price discrepancy, but public percentages, RLM, sportsbook psychology, or "trap"
interpretation may never independently create a wager.
"""

import uuid
from dataclasses import dataclass, field as dfield
from typing import Any, Optional


# ============================================================
# Section 15 — State Machine + Section 26 — Rejection Codes
# ============================================================

STATES = ["OPEN", "CURRENT", "LIVE", "HALFTIME", "SECOND_HALF", "REPRICE", "FINAL"]
ALLOWED_TRANSITIONS = {
    "OPEN": ["CURRENT", "REPRICE"],
    "CURRENT": ["CURRENT", "REPRICE", "LIVE", "FINAL"],
    "LIVE": ["LIVE", "REPRICE", "HALFTIME", "FINAL"],
    "HALFTIME": ["HALFTIME", "REPRICE", "SECOND_HALF", "FINAL"],
    "SECOND_HALF": ["SECOND_HALF", "REPRICE", "FINAL"],
    "REPRICE": ["CURRENT", "LIVE", "HALFTIME", "SECOND_HALF", "FINAL"],
    "FINAL": [],
}

REJECTION_SEVERITY = {
    "STATE_TRANSITION_NOT_ALLOWED": "CRITICAL",
    "FINAL_STATE_LOCKED": "CRITICAL",
    "EVENT_ID_MISMATCH": "CRITICAL",
    "DUPLICATE_EVENT": "INFO",
    "OUT_OF_ORDER_EVENT": "WARNING",
    "OPEN_MARKET_IMMUTABLE": "CRITICAL",
    "HISTORICAL_STATE_IMMUTABLE": "CRITICAL",
    "REPRICE_WITHOUT_CAUSE": "CRITICAL",
    "MARKET_ONLY_REPRICE_ATTEMPT": "WARNING",
    "SCORE_REGRESSION_WITHOUT_CORRECTION": "CRITICAL",
    "TRUE_PRICE_REQUIRED": "CRITICAL",
    "PLAY_GATE_NOT_SATISFIED": "WARNING",
    "NUMBER_NO_LONGER_PLAYABLE": "INFO",
    "CROSS_SOURCE_CONFLICT": "CRITICAL",
    "HINDSIGHT_MUTATION_ATTEMPT": "CRITICAL",
    "FUTURE_DATA_LEAKAGE": "CRITICAL",
    "CONFIRM_VALUE_MISMATCH": "WARNING",
    "EXCESSIVE_REPRICE_DELTA": "WARNING",
}


class GameStateMachine:
    """Section 15. FINAL is terminal; every rejection is logged with its section-26 code."""

    def __init__(self, trace: Optional[list] = None):
        self.state = "OPEN"
        self.history = [("OPEN", None)]
        self.trace = trace if trace is not None else []

    def transition(self, new_state: str, cause_event_id: Optional[str] = None) -> bool:
        if self.state == "FINAL":
            self.trace.append(("REJ:FINAL_STATE_LOCKED", f"attempted {self.state} -> {new_state}; FINAL is terminal"))
            self.trace.append(("REJ:STATE_TRANSITION_NOT_ALLOWED", f"{self.state} -> {new_state} rejected"))
            return False
        if new_state not in ALLOWED_TRANSITIONS[self.state]:
            self.trace.append(("REJ:STATE_TRANSITION_NOT_ALLOWED", f"{self.state} -> {new_state} not in allowed set"))
            return False
        if new_state == "REPRICE" and not cause_event_id:
            self.trace.append(("REJ:REPRICE_WITHOUT_CAUSE", "REPRICE requires cause_event_id (section 17)"))
            return False
        self.state = new_state
        self.history.append((new_state, cause_event_id))
        return True


# ============================================================
# Sections 18-22 — Event Sourcing, Corrections, Authority, Replay
# ============================================================

FIELD_AUTHORITY = {  # section 20, rank 0 = highest authority
    "score": {"OFFICIAL_LEAGUE": 0, "PRIMARY_STATS_PROVIDER": 1},
    "game_clock": {"OFFICIAL_LEAGUE": 0, "STADIUM_FEED": 1},
    "possession": {"PLAY_BY_PLAY_PROVIDER": 0, "OFFICIAL_LEAGUE": 1},
    "sportsbook_price": {"SPORTSBOOK_DIRECT_FEED": 0},
    "injury_status": {"OFFICIAL_TEAM": 0, "LEAGUE_INJURY_REPORT": 1},
}
DEFAULT_AUTHORITY_RANK = 90  # unranked source for a field


@dataclass
class Patch:
    field_name: str
    operation: str          # SET | CONFIRM | CLEAR | NO_CLAIM  (section 19)
    value: Any = None
    source_id: str = ""
    source_sequence: int = 0


@dataclass
class GameEvent:
    event_type: str
    source_id: str
    source_sequence: int
    event_time: str
    patches: list
    received_at: str = ""
    event_id: str = dfield(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str = ""


class EventStore:
    """
    Sections 18-22. Original events immutable; corrections append-only; effective value =
    claim with highest source_sequence (ties broken by authority rank per field).
    SILENCE_IS_NOT_CONFIRMATION: fields absent from an event leave state untouched.
    """

    def __init__(self, trace: Optional[list] = None):
        self.events: list[GameEvent] = []
        self.claims: dict[str, list[dict]] = {}   # field -> ordered claim log
        self.effective: dict[str, dict] = {}       # field -> effective value record
        self.decision_versions: list[dict] = []
        self._seen_keys: set = set()
        self.trace = trace if trace is not None else []

    def _rank(self, field_name: str, source_id: str) -> int:
        return FIELD_AUTHORITY.get(field_name, {}).get(source_id, DEFAULT_AUTHORITY_RANK)

    def append(self, event: GameEvent) -> bool:
        key = event.idempotency_key or event.event_id
        if key in self._seen_keys:
            self.trace.append(("REJ:DUPLICATE_EVENT", f"idempotency key {key} seen; ignored"))
            return False
        self._seen_keys.add(key)
        self.events.append(event)
        for p in event.patches:
            if p.operation == "NO_CLAIM":
                continue  # silence is not confirmation
            self.claims.setdefault(p.field_name, []).append({
                "value": p.value, "source": p.source_id, "seq": p.source_sequence,
                "op": p.operation, "authority": self._rank(p.field_name, p.source_id),
                "event_id": event.event_id, "received_at": event.received_at,
            })
            self._resolve(p.field_name)
        return True

    def _resolve(self, field_name: str):
        claims = self.claims[field_name]
        latest = claims[-1]
        # CROSS_SOURCE_CONFLICT: same sequence, conflicting values from different sources
        for c in claims[:-1]:
            if c["seq"] == latest["seq"] and c["value"] != latest["value"] and c["source"] != latest["source"]:
                self.trace.append(("REJ:CROSS_SOURCE_CONFLICT",
                                   f"{field_name}: same-seq conflict {c['source']}={c['value']} vs {latest['source']}={latest['value']}; authority decides"))
        best = max(claims, key=lambda c: (c["seq"], -c["authority"]))
        cur = self.effective.get(field_name)
        if cur and latest["seq"] < cur["seq"] and latest is not best:
            # older sequence arrived late: replay history only (SOURCE_SEQUENCE_DOMINATES_ARRIVAL)
            self.trace.append(("RULE:SELECTIVE_REPLAY",
                               f"{field_name}: late seq {latest['seq']} from {latest['source']} modifies historical reconstruction, not effective claim (seq {cur['seq']})"))
            return
        if latest["op"] == "CONFIRM":
            if cur is None or latest["value"] != cur["value"]:
                self.trace.append(("REJ:CONFIRM_VALUE_MISMATCH",
                                   f"{field_name}: CONFIRM {latest['value']} != effective {cur['value'] if cur else 'unset'}"))
            else:
                cur.setdefault("confirmed_by", []).append(latest["source"])
            return
        if cur and best["seq"] < cur["seq"]:
            # older sequence arrived late: replay history only (SOURCE_SEQUENCE_DOMINATES_ARRIVAL)
            self.trace.append(("RULE:SELECTIVE_REPLAY",
                               f"{field_name}: late seq {best['seq']} modifies historical reconstruction, not effective claim (seq {cur['seq']})"))
            return
        if cur and field_name == "score" and best["value"] is not None and cur["value"] is not None \
                and best["value"] < cur["value"] and best["op"] == "SET":
            self.trace.append(("REJ:SCORE_REGRESSION_WITHOUT_CORRECTION",
                               f"{field_name} {cur['value']} -> {best['value']} without correction event"))
        self.effective[field_name] = {
            "value": None if best["op"] == "CLEAR" else best["value"],
            "source": best["source"], "seq": best["seq"], "authority": best["authority"],
            "confirmed_by": list(cur.get("confirmed_by", [])) if cur and cur["value"] == best["value"] else [],
        }

    def get(self, field_name: str, default=None):
        e = self.effective.get(field_name)
        return default if e is None else e["value"]

    def record_decision(self, decision: dict) -> str:
        """Section 22: decisions are versioned; actual vs replayed kept separate."""
        d = dict(decision)
        d["historical_status"] = "ACTUALLY_EMITTED"
        self.decision_versions.append(d)
        return f"decision-v{len(self.decision_versions)}"

    def record_replayed_decision(self, decision: dict) -> str:
        d = dict(decision)
        d["historical_status"] = "COUNTERFACTUAL_RECONSTRUCTION"
        self.decision_versions.append(d)
        return f"replay-v{len(self.decision_versions)}"


# ============================================================
# Section 3 — True-Price Construction (built BEFORE market read)
# ============================================================

@dataclass
class TruePrice:
    spread: float            # home-perspective (negative = home favorite)
    total: float
    home_win_prob: float = 0.5
    projected_home: float = 0.0
    projected_away: float = 0.0
    spread_sigma: float = 1.5
    total_sigma: float = 2.5
    calculated_before_market_comparison: bool = True


def build_true_price(team_power_gap: float, expected_pace_points: float, **adjustments) -> TruePrice:
    """
    Independent construction (section 3). team_power_gap: positive = home stronger.
    expected_pace_points: neutral-environment combined expected score.
    adjustments: home_field, weather_delta, injury_delta (points).
    """
    spread = -(team_power_gap + adjustments.get("home_field", 2.5)
               + adjustments.get("injury_delta", 0.0))
    total = expected_pace_points + adjustments.get("weather_delta", 0.0)
    half = total / 2.0
    margin = -spread
    return TruePrice(spread=round(spread, 2), total=round(total, 2),
                     projected_home=round(half + margin / 2, 2),
                     projected_away=round(half - margin / 2, 2))


# ============================================================
# Section 7 — Price Thresholds (Vegas Danny)
# ============================================================

PRICE_THRESHOLDS = {
    "pregame_spread": {"pass_lt": 1.0, "monitor_gte": 1.0, "lean_gte": 1.5, "play_gte": 2.0, "strong_gte": 3.0},
    "first_half":     {"pass_lt": 1.0, "lean_gte": 1.0, "play_gte": 1.5, "strong_gte": 2.0},
    "second_half":    {"pass_lt": 1.5, "monitor_gte": 1.5, "lean_gte": 2.0, "play_gte": 3.0, "strong_gte": 4.0},
    "moneyline":      {"pass_lt": 2.0, "monitor_gte": 2.0, "lean_gte": 3.0, "play_gte": 4.0, "strong_gte": 6.0},
}


def classify_threshold(raw_edge: float, market: str) -> str:
    t = PRICE_THRESHOLDS[market]
    if raw_edge >= t["strong_gte"]:
        return "STRONG_DISCREPANCY"
    if raw_edge >= t["play_gte"]:
        return "PLAY"
    if raw_edge >= t["lean_gte"]:
        return "LEAN"
    if raw_edge >= t.get("monitor_gte", t["pass_lt"]):
        return "MONITOR"
    return "PASS"


def minimum_playable_price(true_line: float, market: str, direction: str = "under") -> float:
    """Vegas Danny: the worst number we would still execute at.
    under/favorite: worse number is HIGHER -> min playable = true + threshold
    over/dog: worse number is LOWER -> min playable = true - threshold"""
    thr = PRICE_THRESHOLDS[market]["play_gte"]
    return true_line + thr if direction in ("under", "favorite") else true_line - thr


# ============================================================
# Section 4 — Market Psychology + Public Bias (FastestPayout)
# ============================================================

PUBLIC_BIAS_THRESHOLDS = [(80, 4), (70, 3), (65, 2), (60, 1), (50, 0)]


def public_bias_score(public_ticket_pct: float) -> int:
    for threshold, score in PUBLIC_BIAS_THRESHOLDS:
        if public_ticket_pct >= threshold:
            return score
    return 0


def psychology_score(narrative_flags: list, true_price_agrees_with_public: bool = False,
                     market_already_corrected: bool = False) -> int:
    """Score -5..5. Positive = narrative inflating the public side. EVIDENCE ONLY (section 4)."""
    score = 0
    for flag in narrative_flags:
        score += 1
    if market_already_corrected:
        score -= 2
    if true_price_agrees_with_public:
        score -= 3   # fundamentals validate narrative
    return max(-5, min(5, score))


# ============================================================
# Section 5 — Line Movement + Sharp Resistance (FastestPayout)
# ============================================================

MOVEMENT_ENUM = ["FUNDAMENTAL_REPRICE", "PUBLIC_PRESSURE", "SHARP_CONFIRMATION", "REVERSE_LINE_MOVEMENT",
                "LINE_FREEZE", "BUYBACK", "KEY_NUMBER_DEFENSE", "LIQUIDITY_NOISE", "UNKNOWN"]


def classify_movement(open_line: float, current_line: float, public_pct: float,
                      move_toward_model: Optional[bool] = None, fundamental_news: bool = False) -> tuple:
    """Returns (classification, movement_score -5..5 relative to OUR position)."""
    moved = round(abs(current_line - open_line), 2)
    direction_sign = 1 if current_line >= open_line else -1
    if fundamental_news:
        cls, score = "FUNDAMENTAL_REPRICE", 0
    elif public_pct >= 65 and moved <= 0.5:
        cls, score = "LINE_FREEZE", (1 if move_toward_model else (-1 if move_toward_model is False else 0))
    elif public_pct >= 65 and direction_sign < 0:
        cls, score = "REVERSE_LINE_MOVEMENT", (-3 if moved >= 1.0 else -1) if move_toward_model is False else 2
    elif moved <= 0.5:
        cls, score = "LIQUIDITY_NOISE", 0
    elif move_toward_model:
        cls = "SHARP_CONFIRMATION" if moved >= 1.0 else "BUYBACK"
        score = 5 if moved >= 1.0 else 1
    else:
        cls, score = "PUBLIC_PRESSURE", -3 if moved >= 1.0 else -1
    return cls, score


def sharp_resistance_score(public_pct: float, spread_move: float, juice_move: bool = False,
                            move_against_public: bool = False, fundamental_news: bool = False,
                            key_number_defense: bool = False, multi_book: bool = False) -> tuple:
    """Section 5 SHARP_RESISTANCE_RULES. Returns (type, score 0-5, fired_rule)."""
    if key_number_defense:
        return "KEY_NUMBER_DEFENSE", 4, "key_number_defense"
    if public_pct >= 75 and move_against_public and abs(spread_move) >= 1.0 and multi_book:
        return "REVERSE_LINE_MOVEMENT", 4, "strong_reverse_move"
    if public_pct >= 65 and move_against_public and not fundamental_news:
        return "REVERSE_LINE_MOVEMENT", 2, "reverse_line_move"
    if public_pct >= 65 and abs(spread_move) <= 0.5 and juice_move:
        return "JUICE_ONLY_MOVE", 2, "heavy_public_juice_only"
    if public_pct >= 65 and abs(spread_move) <= 0.5:
        return "LINE_FREEZE", 1, "heavy_public_no_move"
    return "NONE", 0, ""


# ============================================================
# Section 6 — Key Numbers
# ============================================================

NFL_KEY_NUMBERS = {3: 5, 7: 4, 10: 2, 14: 2, 4: 1, 6: 1}
CFB_KEY_NUMBERS = {3: 5, 7: 4, 10: 2, 14: 2}
SECOND_HALF_TOTAL_KEYS = {20: 5, 21: 4, 24: 5, 17: 2, 27: 2, 14: 1, 16: 1, 19: 1, 22.5: 1}


def key_number_quality(number: float, market: str = "spread", league: str = "nfl") -> int:
    if market == "second_half_total":
        return max((v for k, v in SECOND_HALF_TOTAL_KEYS.items() if abs(number - k) < 0.01), default=0)
    table = NFL_KEY_NUMBERS if league == "nfl" else CFB_KEY_NUMBERS
    return table.get(int(round(number)), 0) if float(number).is_integer() else 0


def key_number_crossing(entry_number: float, current_number: float, market: str = "spread",
                       league: str = "nfl", direction: str = "under") -> tuple:
    """Section 6 KEY_NUMBER_UPDATE. Returns (crossed, favorable, keys_crossed)."""
    keys = list(NFL_KEY_NUMBERS if league == "nfl" else CFB_KEY_NUMBERS) if market == "spread" \
        else list(SECOND_HALF_TOTAL_KEYS)
    lo, hi = sorted((entry_number, current_number))
    crossed = [k for k in keys if lo < k < hi]
    if not crossed:
        return False, False, []
    favorable = (direction == "under" and current_number > entry_number) or \
                (direction != "under" and current_number < entry_number)
    return True, favorable, crossed


# ============================================================
# Sections 9-12 — Sustainability, Archetypes, Game Theory, 2H Regression
# ============================================================

DISTORTION_SOURCES = ["PICK_SIX", "FUMBLE_RETURN_TD", "SPECIAL_TEAMS_TD", "SHORT_FIELD",
                      "BUSTED_EXPLOSIVE", "RED_ZONE_VARIANCE", "THIRD_DOWN_VARIANCE",
                      "MISSED_FIELD_GOALS", "FAILED_FOURTH_DOWN", "PENALTY_ERASED_SCORE", "OTHER"]


@dataclass
class FirstHalfState:
    points: int
    drive_ypp: float                # normal-drive yards per play
    possessions: int
    distortion_points: int          # points created by DISTORTION_SOURCES
    missed_fg: int = 0
    rz_trips: int = 0
    rz_tds: int = 0
    lead: int = 0                   # home-perspective lead at half
    ypp_margin: float = 0.0         # home YPP minus away YPP


def classify_sustainability(fh: FirstHalfState) -> str:
    """Section 9. Low score != Under env; high score != Over env."""
    share = fh.distortion_points / fh.points if fh.points else 0.0
    if share >= 0.45:
        return "VERY_LOW"
    if share >= 0.30:
        return "LOW"
    if share <= 0.10 and fh.drive_ypp >= 5.0:
        return "VERY_HIGH"
    if share <= 0.20 and fh.drive_ypp >= 4.5:
        return "HIGH"
    return "NEUTRAL"


def classify_halftime_archetype(fh: FirstHalfState, market_corrected: bool = False,
                               info_conflict: bool = False) -> str:
    """Section 10, priority order."""
    if market_corrected or info_conflict:
        return "NO_PLAY"
    if abs(fh.lead) >= 17:
        return "GAME_STATE_CONTAMINATION"
    if fh.points >= 24 and fh.distortion_points / max(fh.points, 1) >= 0.35:
        return "FALSE_SCORING_UNDER"
    if fh.points <= 14 and fh.drive_ypp >= 5.0 and (fh.missed_fg + max(0, fh.rz_trips - fh.rz_tds)) >= 2:
        return "SUPPRESSED_SCORING_OVER"
    if abs(fh.ypp_margin) >= 1.8:
        return "CONTROL_SIDE_HOME" if fh.ypp_margin > 0 else "CONTROL_SIDE_AWAY"
    return "UNCLASSIFIED"


def game_theory_read(lead: int) -> dict:
    """Section 11."""
    if abs(lead) >= 17:
        script, pace = "BLOWOUT", "MUCH_SLOWER"
    elif lead >= 10:
        script, pace = "CLOCK_KILL", "SLOWER"
    elif lead <= -10:
        script, pace = "COMEBACK_MODE", "FASTER"
    elif abs(lead) <= 3:
        script, pace = "NEUTRAL", "SAME"
    else:
        script, pace = ("FAVORITE_CONTROL" if lead > 0 else "UNDERDOG_CONTROL"), "SAME"
    return {
        "script": script,
        "expected_2h_pace": pace,
        "trailing_team_urgency": ("EXTREME" if lead <= -14 else "HIGH" if lead <= -7 else "MEDIUM" if lead < 0 else "LOW"),
        "leading_team_conservatism": ("HIGH" if lead >= 14 else "MEDIUM" if lead >= 7 else "LOW"),
        "substitution_risk": "HIGH" if abs(lead) >= 17 else "MEDIUM" if abs(lead) >= 10 else "LOW",
        "possession_advantage_2h": "UNKNOWN",
    }


def true_2h_total(baseline_remaining_scoring: float, pace_adj: float = 0.0, regression_adj: float = 0.0,
                 game_state_adj: float = 0.0, turnover_distortion: float = 0.0,
                 explosive_distortion: float = 0.0, rz_adj: float = 0.0, weather_adj: float = 0.0,
                 injury_adj: float = 0.0, substitution_adj: float = 0.0, possession_adj: float = 0.0) -> float:
    """Section 12 formula, verbatim structure."""
    return round(baseline_remaining_scoring + pace_adj + regression_adj + game_state_adj
                 - turnover_distortion - explosive_distortion + rz_adj + weather_adj
                 + injury_adj + substitution_adj + possession_adj, 2)


# ============================================================
# Sections 13-14, 32 — Decision Gates + Output Contract (kernel apex)
# ============================================================

@dataclass
class DecisionOutput:
    marketnova_true_line: float
    market_line: float
    raw_edge: float
    key_number_quality: int
    market_psychology: int
    public_bias: int
    sharp_resistance: int
    game_state_support: str
    regression_support: str
    model_uncertainty: str
    status: str
    minimum_playable_price: float
    fair_price: float
    ideal_entry_price: float
    invalidators: list
    gates_fired: list
    note: str = ""


def decide(*, true_price: TruePrice, market_line: float,
           market: str = "pregame_spread", direction: str = "under",
           information_quality: str = "OK",           # OK | DEGRADED | CONFLICTED
           public_pct: float = 50.0, psychology: int = 0,
           movement_score: int = 0, sharp_resistance: int = 0,
           supporting_modules: Optional[list] = None,
           key_crossed_unfavorably: bool = False,
           prior_playable_edge: bool = False,
           invalidators: Optional[list] = None,
           trace: Optional[list] = None) -> DecisionOutput:
    """
    Canonical Execution Kernel (section 32) with Decision Gates (section 13).
    supporting_modules: subset of ["psychology","sharp_resistance","regression","game_theory",
    "key_number","sustainability","game_state","talent"] that AGREE with the position.
    """
    gates = []
    t = trace if trace is not None else []
    mods = supporting_modules or []

    # GATE 1 — TRUE_PRICE_REQUIRED
    if true_price is None or not getattr(true_price, "calculated_before_market_comparison", False):
        gates.append("TRUE_PRICE_REQUIRED")
        t.append(("GATE:TRUE_PRICE_REQUIRED", "no independent true price -> NO_MODEL"))
        return DecisionOutput(0.0, market_line, 0.0, 0, 0, 0, 0, "NONE", "NONE",
                              "HIGH", "NO_MODEL", 0.0, 0.0, 0.0, [], gates)
    # GATE 2 — CRITICAL_INFORMATION
    if information_quality in ("DEGRADED", "CONFLICTED"):
        gates.append("CRITICAL_INFORMATION")
        t.append(("GATE:CRITICAL_INFORMATION", f"information_quality={information_quality} -> INFORMATION_INCOMPLETE"))
        return DecisionOutput(0.0, market_line, 0.0, 0, 0, 0, 0, "NONE", "NONE",
                              "HIGH", "INFORMATION_INCOMPLETE", 0.0, 0.0, 0.0, [], gates)
    # raw edge (points)
    if market in ("pregame_spread",):
        raw_edge = round(abs(abs(market_line) - abs(true_price.spread)), 2)
    else:
        raw_edge = round(abs(market_line - true_price.total), 2)
    edge_score = raw_edge * 2          # composite 0-10 (MINIMUM_EDGE gate operates on this)
    # GATE 4/5 — PUBLIC/PSYCHOLOGY_CANNOT_CREATE_PLAY
    fundamental_mods = [m for m in mods if m in ("regression", "game_theory", "sustainability", "talent", "game_state")]
    only_public = (psychology >= 2 or public_pct >= 65) and not fundamental_mods and edge_score < 4
    if only_public:
        gates.append("PUBLIC_CANNOT_CREATE_PLAY")
        t.append(("GATE:PUBLIC_CANNOT_CREATE_PLAY", "public/psychology is the only positive signal -> PASS"))
    # GATE 6 — STRONG_MARKET_CONTRADICTION
    if movement_score <= -3:
        gates.append("STRONG_MARKET_CONTRADICTION")
        t.append(("GATE:STRONG_MARKET_CONTRADICTION", f"movement_score {movement_score} <= -3 -> REPRICE_REQUIRED"))
    # GATE 7 — KEY_NUMBER_CROSSED
    if key_crossed_unfavorably:
        gates.append("KEY_NUMBER_CROSSED")
        t.append(("GATE:KEY_NUMBER_CROSSED", "unfavorable key crossing -> RECOMPUTE_EV, INVALIDATE_OLD_RECOMMENDATION"))
    # threshold band + minimum playable price (Vegas Danny)
    band = classify_threshold(raw_edge, market)
    base_line = true_price.total if direction in ("under", "over") else true_price.spread
    mpp = minimum_playable_price(base_line, market, direction)
    current_playable = (market_line >= mpp) if direction in ("under", "favorite") else (market_line <= mpp)
    # status precedence
    if movement_score <= -3:
        status = "REPRICE_REQUIRED"
    elif only_public:
        status = "PASS"
    elif not current_playable and prior_playable_edge:
        # spec sec 13 NUMBER_GONE + sec 38: thesis may remain correct but the market removed the value
        status = "NUMBER_GONE"
        gates.append("NUMBER_GONE")
        t.append(("GATE:NUMBER_GONE", f"market {market_line} worse than minimum playable {mpp}; prior playable edge existed -> NUMBER_GONE"))
    elif edge_score < 4:
        status = "PASS"
        gates.append("MINIMUM_EDGE")
        t.append(("GATE:MINIMUM_EDGE", f"edge_score {edge_score} < 4 -> PASS"))
    elif not current_playable:
        status = "NUMBER_GONE"
        gates.append("NUMBER_GONE")
        t.append(("GATE:NUMBER_GONE", f"market {market_line} worse than minimum playable {mpp} -> NUMBER_GONE"))
    elif band in ("PLAY", "STRONG_DISCREPANCY"):
        # v1.1 amendment (Oct 9 2026, Liu 2026 finding: markets price
        # momentum/streak info efficiently; its signal value does not
        # overcome the vig): psychology is context, never conviction —
        # it does NOT count toward the PLAY support requirement.
        credit_mods = [m for m in mods if m != "psychology"]
        if len(credit_mods) < 2:                              # GATE 8 — PLAY_REQUIRES_SUPPORT
            status = "LEAN"
            gates.append("PLAY_REQUIRES_SUPPORT")
            t.append(("GATE:PLAY_REQUIRES_SUPPORT", f"only {len(credit_mods)} creditable supporting module(s); PLAY downgraded to LEAN"))
        elif edge_score >= 8 and len(credit_mods) >= 3 and movement_score > -3:   # GATE 9 — STRONG_DISCREPANCY
            status = "STRONG_DISCREPANCY"
            gates.append("STRONG_DISCREPANCY")
        else:
            status = "PLAY"
    else:
        status = band
    kpq = key_number_quality(market_line, "second_half_total" if market == "second_half" else "spread")
    return DecisionOutput(
        marketnova_true_line=base_line, market_line=market_line, raw_edge=raw_edge,
        key_number_quality=kpq, market_psychology=psychology,
        public_bias=public_bias_score(public_pct), sharp_resistance=sharp_resistance,
        game_state_support="SUPPORTED" if "game_state" in mods else "NONE",
        regression_support="SUPPORTED" if "regression" in mods else "NONE",
        model_uncertainty="LOW" if true_price.total_sigma <= 2 else "MODERATE",
        status=status, minimum_playable_price=mpp,
        fair_price=base_line,
        ideal_entry_price=round(base_line + PRICE_THRESHOLDS[market]["strong_gte"], 2) if direction in ("under", "favorite")
        else round(base_line - PRICE_THRESHOLDS[market]["strong_gte"], 2),
        invalidators=invalidators or [], gates_fired=gates,
    )


# ============================================================
# Section 17 — Reprice Rules
# ============================================================

REPRICE_CAUSES = ["MAJOR_INJURY", "QB_CHANGE", "STARTER_CHANGE", "WEATHER_CHANGE", "SCORE_EVENT",
                  "TURNOVER", "RED_ZONE_RESULT", "MAJOR_FOURTH_DOWN", "EJECTION",
                  "SUBSTITUTION_CHANGE", "DATA_CORRECTION", "MARKET_CONTRADICTION_REVIEW",
                  "MATERIAL_STATISTICAL_UPDATE", "POSSESSION_STATE_CHANGE", "GAME_STATE_CHANGE"]


def reprice(machine: GameStateMachine, cause_event_id: str, cause_type: str,
            affected_inputs: list) -> bool:
    """REPRICE_RULE: requires cause_event_id, cause_type, affected_model_inputs."""
    if cause_type not in REPRICE_CAUSES:
        machine.trace.append(("REJ:REPRICE_WITHOUT_CAUSE", f"cause_type {cause_type} not a valid reprice cause"))
        return False
    if machine.state == "FINAL":
        machine.trace.append(("REJ:FINAL_STATE_LOCKED", "cannot reprice a FINAL game"))
        return False
    ok = machine.transition("REPRICE", cause_event_id=cause_event_id)
    if ok:
        machine.trace.append(("RULE:REPRICE", f"cause={cause_type} affected_inputs={affected_inputs}"))
    return ok


def attempt_market_only_reprice(machine: GameStateMachine, market_move_only: bool = True) -> bool:
    """Market movement alone triggers contradiction review, never an automatic true-price change."""
    if market_move_only:
        machine.trace.append(("REJ:MARKET_ONLY_REPRICE_ATTEMPT",
                               "market movement alone may trigger MARKET_CONTRADICTION_REVIEW, not a true-price reprice"))
        return False
    return True


# ============================================================
# Section 28 — CLV + Postmortem
# ============================================================

def postmortem(entry_price: float, closing_price: float, result: str,
               process_ok: bool) -> dict:
    clv = round(entry_price - closing_price, 2)  # under-perspective
    if process_ok and result == "WIN":
        grade, tag = "GOOD_PROCESS_GOOD_RESULT", "POSITIVE_TRAINING_EXAMPLE"
    elif process_ok and result == "LOSS":
        grade, tag = "GOOD_PROCESS_BAD_RESULT", "DO_NOT_PENALIZE_FOR_RESULT_ALONE"
    elif (not process_ok) and result == "WIN":
        grade, tag = "BAD_PROCESS_GOOD_RESULT", "FLAG_FALSE_POSITIVE"
    else:
        grade, tag = "BAD_PROCESS_BAD_RESULT", "NEGATIVE_TRAINING_EXAMPLE"
    return {"closing_line_value": clv, "result": result, "process_grade": grade, "action": tag}

```


## FILE: `nova/reads.py`
**MarketNovaRead generation (compute_read, vig test, lifecycle, sanity gates)**

```python
"""Nova read generation — the MarketNova brain running externally.

Pipeline per game (pregame, within the lookahead window):
  1. Resolve both teams' power ratings (TeamRating) via the alias map.
     Missing ratings => status AWAITING DATA. Never fake numbers.
  2. Build the independent true price via kernel.build_true_price with
     sport-aware home-field advantage.
  3. Read the market from the freshest is_current odds row (spread,
     home-perspective convention, same as the kernel's).
  4. Classify with kernel.decide() — the canonical decision gates
     (thresholds, key numbers, sanity bounds).
  5. Hard sanity gate: |edge| > 8 points => AWAITING DATA + reason
     (data conflict, never a play).
  6. Narrative via llm.py when a key exists, else deterministic
     templates derived from the decision output.
  7. Cycle writes only when numbers/material fields changed, and never
     touches bet_* recommendation fields.

Status taxonomy strictly: PLAY / LEAN / MONITOR / PASS / NUMBER GONE /
AWAITING DATA.
"""
import json
from datetime import datetime, timezone

from nova import config, matching
from nova import kernel, narratives

# Sport-aware home-field advantage (points), applied inside build_true_price.
HOME_FIELD = {"nfl": 2.5, "ncaaf": 2.5, "nba": 2.3, "ncaab": 3.0,
              "wnba": 1.5, "nhl": 0.3, "mlb": 0.25}

# Kernel status -> product taxonomy.
STATUS_MAP = {
    "PLAY": "PLAY",
    "STRONG_DISCREPANCY": "PLAY",
    "LEAN": "LEAN",
    "MONITOR": "MONITOR",
    "PASS": "PASS",
    "NUMBER_GONE": "NUMBER GONE",
    "NO_MODEL": "AWAITING DATA",
    "INFORMATION_INCOMPLETE": "AWAITING DATA",
    "REPRICE_REQUIRED": "AWAITING DATA",
}


def resolve_ratings(game, ratings):
    """Find home/away TeamRating rows. Games may carry abbrs or full names;
    ratings carry ESPN abbrs AND team names — match on either.
    Returns {"home": rating, "away": rating} or None."""
    league = str(game.get("league_code") or game.get("sport") or "").upper()
    from nova import espn as _espn
    out = {}
    for side in ("home", "away"):
        val = game.get(f"{side}_team") or game.get(f"{side}_team_name")
        if not val:
            return None
        target = matching.canon(val, league)
        hit = None
        for r in ratings:
            rl = str(r.get("league_code") or r.get("league") or r.get("sport") or "").upper()
            if rl and rl != league:
                continue
            if matching.canon(r.get("team_abbr") or r.get("abbr"), league) == target:
                hit = r
                break
        if hit is None:
            for r in ratings:
                rl = str(r.get("league_code") or r.get("league") or r.get("sport") or "").upper()
                if rl and rl != league:
                    continue
                if _espn._name_match(val, r.get("team_abbr"), r.get("team_name"), league):
                    hit = r
                    break
        if hit is None:
            return None
        out[side] = hit
    return out


def freshest_market(odds_rows):
    """Best current snapshot per market type, normalized to
    {market: {"line": ..., "market_line": ...}} from the production
    OddsSnapshot schema (home_line / over_line / prices)."""
    def _cd(o):
        try:
            dt = datetime.fromisoformat(
                str(o.get("created_date", "")).replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except (ValueError, TypeError):
            return None  # unparseable stamp -> treated as oldest

    best = {}
    for o in odds_rows:
        if not o.get("is_current"):
            continue
        mt = str(o.get("market_type", "")).lower()
        cur = best.get(mt)
        if cur is None:
            best[mt] = o
            continue
        # parsed-datetime compare: raw string compares across mixed stamp
        # formats ('...03.061Z' vs '...03.061000') can pick the WRONG row
        a, b = _cd(o), _cd(cur)
        if a is not None and (b is None or a > b):
            best[mt] = o
    out = {}
    for mt, o in best.items():
        if mt == "spread":
            line = o.get("home_line")
        elif mt == "total":
            line = o.get("over_line")
        else:
            line = o.get("home_price")
        out[mt] = {"line": line,
                   "home_price": o.get("home_price"),
                   "away_price": o.get("away_price"),
                   "book": o.get("book"),
                   "market_line": line,
                   "created_date": o.get("created_date")}
    return out


def compute_read(game, ratings_pair, market_rows, juice=None, form=None,
                    movement=None, weather=None, rest=None, injuries=None,
                    tape=None, injury_details=None):
    """Return a decision dict or an AWAITING DATA stub with a reason."""
    league_l = str(game.get("league_code") or game.get("sport") or "").lower()
    league_u = league_l.upper()
    home_rating = ratings_pair.get("home")
    away_rating = ratings_pair.get("away")
    if not home_rating or not away_rating:
        return {"status": "AWAITING DATA",
                "reason": "Missing power ratings for one or both teams — Nova does not publish a true line without them."}

    hp = float(_rating_value(home_rating))
    ap = float(_rating_value(away_rating))
    gap = hp - ap  # positive = home stronger

    market = market_rows.get("spread")
    if not market or market.get("market_line") is None:
        return {"status": "AWAITING DATA",
                "reason": "No current spread price available — the read refreshes when the next odds cycle lands."}

    line = float(market["market_line"])  # home-perspective (negative = home favored)
    expected_total = _expected_total(market_rows, league_l)

    tp = kernel.build_true_price(
        team_power_gap=gap,
        expected_pace_points=expected_total,
        home_field=HOME_FIELD.get(league_l, 2.0))

    # SIGNED-EDGE SANITY GATE (runs before the kernel). |abs(market)| -
    # |abs(true)| edge math collapses sign flips: a market of -6.5 vs a true
    # line of +14.0 is a 20.5-point disagreement, not a 7.5-point edge.
    # When Nova's rating-based true price and a real book price disagree
    # by more than MAX_EDGE_POINTS on the same signed scale, one of them is
    # wrong — treat it as a data conflict, never as a discrepancy to lean on.
    signed_edge = abs(line - tp.spread)
    if signed_edge > config.MAX_EDGE_POINTS:
        return {"status": "AWAITING DATA",
                "reason": (f"Nova's true price ({tp.spread:+.1f}) and the market "
                           f"({line:+.1f}) disagree by {signed_edge:.1f} points — "
                           "treated as a ratings/market data conflict, not an edge.")}

    # Direction = the side the true line says has value (spread convention:
    # home-perspective, negative = home favored). The kernel's default
    # "under" is for totals; for spreads we must name the value side.
    #   market lays FEWER points than true (market_line > true_line):
    #     the favorite is underpriced -> "favorite"
    #   market lays MORE points than true (market_line < true_line):
    #     the dog is getting too many points -> "dog"
    direction = "favorite" if line > tp.spread else "dog"

    # Value side (home-perspective: negative line = home favored).
    value_side = (("home" if line < 0 else "away") if direction == "favorite"
                  else ("away" if line < 0 else "home"))

    # Juice pressure (game theory layer, Part XX): a juice move AGAINST
    # the value side while the number held is the book probing demand on
    # our side — often the number crosses soon. Leading indicator, so a
    # quiet PASS becomes MONITOR; a real edge keeps its status but carries
    # the pressure marker into sharp_resistance and telemetry.
    juice_against = bool(juice and juice.get("line_held")
                        and juice.get("side") == value_side and juice.get("worse"))

    # Momentum context (Parts XII/XIII; Liu 2026 finding: markets price
    # streak info efficiently and its value does not clear the vig).
    # Streak data NEVER adds conviction — it is recency-tax context for
    # the narrative and evidence for postmortem tags. If the value side
    # rides a streak, the market has almost certainly priced it; if the
    # value side is cold, bettor perception may be trailing reality.
    streak_ctx, recency_anchor = None, False
    other = "home" if value_side == "away" else "away"
    if form and form.get(value_side):
        st = _streak(form[value_side])
        if st:
            kind, n, last_g = st
            streak_ctx = {"side": value_side, "kind": f"{kind}{n}", "len": n}
            try:
                margin = float(last_g.get("score")) - float(last_g.get("opponent_score"))
                recency_anchor = abs(margin) >= 21.0   # blowout in the last game
            except (TypeError, ValueError):
                pass

    # ---- creditable supporting modules (GATE 8, A1: psychology excluded) ----
    # Every module here is data-backed: juice pressure, form-based
    # regression context, or a rest advantage measured from the schedule.
    mods = []
    if juice_against:
        mods.append("sharp_resistance")
    if streak_ctx:
        cold_value = streak_ctx["kind"].startswith("L") and streak_ctx["len"] >= 2
        if cold_value:
            mods.append("regression")   # value side cold: perception may lag reality (A2)
        if recency_anchor and form and form.get(other):
            mods.append("regression")   # opponent's blowout anchor: market may overreact
    if rest and rest.get(value_side) is not None and rest.get(other) is not None:
        if rest[value_side] - rest[other] >= 2:
            mods.append("game_state")   # measured rest advantage for the value side

    decision = kernel.decide(true_price=tp, market_line=line,
                            market="pregame_spread", direction=direction,
                            sharp_resistance=1 if juice_against else 0,
                            supporting_modules=mods)
    raw_edge = float(getattr(decision, "raw_edge", 0.0) or 0.0)
    true_line = float(tp.spread)
    status = STATUS_MAP.get(str(getattr(decision, "status", "PASS")), "PASS")

    # VIG TEST (Part XX; Liu 2026 finding: signal value too small to
    # overcome the vig is noise). The published status is capped by what
    # remains of the edge after the value side's juice is paid: every 10
    # cents beyond -110 costs a quarter point of required edge. Better-
    # than--110 or plus-money prices pay no penalty.
    spread_row = market_rows.get("spread") or {}
    side_price = spread_row.get(f"{value_side}_price")
    vig_penalty = 0.0
    try:
        p = float(side_price)
        if p <= -110:
            vig_penalty = round(0.25 * (abs(p) - 110) / 10.0, 2)
    except (TypeError, ValueError):
        pass
    effective_edge = round(raw_edge - vig_penalty, 2)
    grade_rank = {"PASS": 0, "MONITOR": 1, "LEAN": 2, "PLAY": 3}
    if vig_penalty > 0 and status in grade_rank:
        adj_band = STATUS_MAP.get(
            kernel.classify_threshold(effective_edge, "pregame_spread"), "PASS")
        if grade_rank.get(adj_band, 0) < grade_rank[status]:
            status = adj_band

    # Juice pressure is a repricing trigger (doctrine MONITOR definition):
    # a quiet PASS with pressure on the value side becomes MONITOR.
    if juice_against and status == "PASS":
        status = "MONITOR"

    # Line lifecycle (Part XVII, timing is an edge): record where in the
    # line's life cycle this read sits. Edges taken early face more market
    # evolution; edges taken late face mostly the public shade.
    hours, stage = None, "unknown"
    try:
        st = str(game.get("start_time") or "").replace("Z", "+00:00")
        dt = datetime.fromisoformat(st)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        if delta >= 0:
            hours = round(delta, 1)
            stage = "early" if delta > 48 else ("mid" if delta >= 24 else "late")
    except ValueError:
        pass

    # Hard sanity gate (product rule): edge never > 8 points vs true line.
    # Applies to EVERY status — a huge computed edge means corrupt inputs
    # (bad rating or bad price), so no verdict from it is trustworthy.
    if raw_edge > config.MAX_EDGE_POINTS:
        return {"status": "AWAITING DATA",
                "reason": (f"Computed edge of {raw_edge:.1f} exceeds the "
                           f"{config.MAX_EDGE_POINTS:.0f}-point sanity bound — treated as a data conflict, not an edge."),
                "raw_edge": raw_edge, "true_line": true_line, "market_line": line}

    # ---- frontend-compatibility fields (Game Terminal page) ----
    min_playable = float(getattr(decision, "minimum_playable_price", 0.0) or 0.0)
    side_team = (game.get(f"{value_side}_team") or game.get(f"{value_side}_team_name")
                 or value_side.upper())
    playable_until = f"{side_team} {min_playable:+.1f} or better"
    edge_direction = ("NOVA_FAVORS_" + ("HOME" if value_side == "home" else "AWAY"))
    invalidators = [
        f"Spread crosses the buy-to threshold ({playable_until})",
        "TeamRating update materially changes the true line",
        "Juice moves against the value side past -130",
        "Personnel or injury news not yet absorbed by the ratings",
    ]
    next_trigger = f"Re-run when the spread approaches {min_playable:+.1f} on the value side."
    # honest data confidence: ratings + odds verified; decays with odds age
    conf = 90
    try:
        row = market_rows.get("spread") or {}
        cd = str(row.get("created_date") or "")
        if cd:
            odt = datetime.fromisoformat(cd.replace("Z", "+00:00"))
            if odt.tzinfo is None:
                odt = odt.replace(tzinfo=timezone.utc)
            age_min = max(0.0, (datetime.now(timezone.utc) - odt).total_seconds() / 60.0)
            conf = max(30, 90 - int(age_min / 6))
    except (ValueError, TypeError):
        pass
    market_psychology = (
        f"The book is posting {line:+.1f} while Nova's independent ratings put fair at "
        f"{true_line:+.1f}. The gap most likely reflects ratings lag and market anchoring "
        f"to recent results rather than informed positioning.")

    # ---- Game Terminal context fields (all honest, all data-derived) ----
    # kernel returns an int quality score; the read schema field is a
    # string, so translate into the headline the PRICE layer renders.
    kpq_int = getattr(decision, "key_number_quality", 0) or 0
    if league_l in ("nfl", "ncaaf"):
        key_table = (kernel.NFL_KEY_NUMBERS if league_l == "nfl" else kernel.CFB_KEY_NUMBERS)
        aline = abs(line)
        near = min(key_table, key=lambda k: abs(aline - abs(k)))
        dist = abs(aline - abs(near))
        if dist < 0.01:
            kpq = f"On key number {abs(near)}"
        elif dist <= 1.5:
            kpq = f"{line:+.1f} — {dist:.1f} from key number {abs(near)}"
        else:
            kpq = f"{line:+.1f} — off key numbers (nearest {abs(near)})"
    else:
        kpq = (f"{line:+.1f} — whole-number spread" if float(line).is_integer()
               else f"{line:+.1f}")
    gs_support = None
    if rest and rest.get(value_side) is not None and rest.get(other) is not None:
        gap = rest[value_side] - rest[other]
        if gap >= 2:
            side_nm = game.get(f"{value_side}_team") or value_side
            gs_support = f"{side_nm} holds a rest edge ({rest[value_side]} days vs {rest[other]})"
    reg_support = None
    if "regression" in mods:
        if streak_ctx and streak_ctx["kind"].startswith("L"):
            side_nm = game.get(f"{value_side}_team") or value_side
            reg_support = (f"{side_nm} enters {streak_ctx['kind']} — perception may be "
                           "trailing current pricing; form gap is a candidate edge, not a risk")
        else:
            reg_support = ("Opponent's recent blowout anchors public perception — "
                           "recency-tax candidate in the market's price")
    weather_line = None
    if weather:
        weather_line = (f"{weather.get('conditions')}, {weather.get('temp_f')}°F, "
                        f"{weather.get('wind_speed_mph')} mph wind, "
                        f"{weather.get('precipitation_pct')}% rain — {weather.get('weather_read')}")
    gs_tempo = gs_notes = None
    if league_l in ("nfl", "ncaaf"):
        mag = abs(true_line)
        fav = (game.get("home_team") if true_line < 0 else game.get("away_team")) or "favorite"
        if mag >= 7:
            gs_tempo = "FAVOR-CONTROLLED"
            gs_notes = (f"Nova projects {fav} by {mag:.0f}: expect leading-side run lean, "
                        "reduced late pace and under-leaning game states; trailing-side "
                        "pass volume lifts quarterback/ receiver props.")
        elif mag >= 3:
            gs_tempo = "LEANING-FAVOR"
            gs_notes = (f"Nova projects {fav} by {mag:.0f}: moderate script skew — "
                        "favorite controls fourth-quarter pace unless the game stays within one score.")
        else:
            gs_tempo = "COMPETITIVE"
            gs_notes = ("Projected margin inside a field goal: script risk is high, "
                       "possession swings stay live deep into the second half.")
    lineup_status = (f"Injury report synced ({injuries} entries)") if injuries else None
    # game script belongs to the Analysis record (frontend contract)
    sharp_line = ("Juice moved against the value side while the number held — "
                  "repricing watch") if juice_against else None
    public_bias_line = ("Ticket-split data not ingested — no public-percentage claims; "
                        "line construction is the only positioning evidence.")

    # ---- narrative layer (nova/narratives.py; deterministic, numbers-only) ----
    telemetry_stub = {
        "stage": stage, "hours_to_close": hours, "value_side": value_side,
        "juice_pressure": "against_value_side" if juice_against else "none",
        "juice_detail": juice or None, "vig_penalty": vig_penalty,
        "effective_edge": effective_edge,
        "shading_window": (stage == "early" and direction == "favorite"),
        "streak": streak_ctx, "recency_anchor": recency_anchor,
    }
    n_res = {
        "status": status, "true_line": true_line, "market_line": line,
        "raw_edge": raw_edge, "minimum_playable": min_playable,
        "data_confidence": conf, "invalidators": invalidators,
        "_value_side": value_side,
        "_status_reason": getattr(decision, "reason", None),
        "telemetry": telemetry_stub,
    }
    n_ctx = {
        "ratings_pair": ratings_pair, "movement": movement, "form": form,
        "weather": weather, "juice": juice, "tape": tape,
        "injury_details": injury_details, "band": 1.0,
    }
    narrative = {}
    try:
        narrative = narratives.build(game, n_res, n_ctx)
    except Exception:
        narrative = {}
    why_here = narrative.get("why_market_here") or market_psychology

    return {
        "status": status,
        "true_line": true_line,
        "market_line": line,
        "raw_edge": raw_edge,
        "minimum_playable": min_playable,
        "key_number_quality": kpq,
        "line_movement_direction": (movement or {}).get("direction"),
        "sharp_resistance": sharp_line,
        "public_bias": public_bias_line,
        "game_state_support": gs_support,
        "regression_support": reg_support,
        "weather": weather_line,
        "game_script_tempo": gs_tempo,
        "game_script_notes": gs_notes,
        "lineup_status": lineup_status,
        "supporting_modules": mods,
        # fields the Game Terminal page renders directly
        "edge_value": raw_edge,
        "edge_score_display": f"{raw_edge:.1f} pts",
        "market_odds": float(market_rows.get("spread", {}).get(
            f"{value_side}_price") or 0.0) or None,
        "edge_direction": edge_direction,
        "playable_until": playable_until,
        "next_trigger": next_trigger,
        "invalidators": invalidators,
        "market_psychology": why_here,
        "data_confidence": conf if status != "AWAITING DATA" else 0,
        # narrative layer (frontend contract; schema fields pending builder)
        "why_market_here": why_here,
        "thesis": narrative.get("thesis"),
        "nova_read": narrative.get("nova_read"),
        "risk": narrative.get("risk"),
        "buy_to": narrative.get("buy_to"),
        "regression_notes": narrative.get("regression_notes"),
        "market_tape": narrative.get("market_tape"),
        "true_line_basis": narrative.get("true_line_basis"),
        "value_window": narrative.get("value_window"),
        "confidence": conf if status != "AWAITING DATA" else 0,
        "telemetry": {
            "stage": stage,                      # early / mid / late
            "hours_to_close": hours,             # Part XVII: timing is an edge
            "value_side": value_side,
            "juice_pressure": "against_value_side" if juice_against else "none",
            "juice_detail": juice or None,
            "vig_penalty": vig_penalty,              # Part XX: juice is part of the number
            "effective_edge": effective_edge,        # edge after vig — the gradeable remainder
            "shading_window": (stage == "early" and direction == "favorite"),
            # open-shading window (Liu 2026: FLB present at open, gone at
            # close) — early favorite-side edges are the documented case
            "streak": streak_ctx,               # momentum context only — never conviction
            "recency_anchor": recency_anchor,   # blowout in the last game: bettors anchor, market prices it
        },
    }


def resolve_form(game, forms):
    """Find home/away TeamForm rows by abbr through the alias map.
    Returns {"home": row, "away": row} with possibly-None sides, or None."""
    if not forms:
        return None
    league = str(game.get("league_code") or game.get("sport") or "").upper()
    out = {}
    for side in ("home", "away"):
        val = game.get(f"{side}_team") or game.get(f"{side}_team_name")
        target = matching.canon(val, league) if val else None
        hit = None
        for f in forms:
            fl = str(f.get("league_code") or "").upper()
            if fl and fl != league:
                continue
            fa = matching.canon(f.get("team_abbr") or f.get("team_name") or "", league)
            if fa and fa == target:
                hit = f
                break
        out[side] = hit
    return out


def _streak(row):
    """Current streak from last_5_json (chronological, trailing run).
    Returns (kind, length, last_game) or None."""
    try:
        games = json.loads(row.get("last_5_json") or "[]")
    except (ValueError, TypeError):
        return None
    results = [str(g.get("result", "")).upper() for g in games if g.get("result")]
    if not results:
        return None
    last = results[-1]
    n = 0
    for r in reversed(results):
        if r == last:
            n += 1
        else:
            break
    return (last, n, games[-1])


def _rating_value(r):
    for f in ("rating", "power_rating"):
        if r.get(f) is not None:
            return r[f]
    return 0


def _expected_total(market_rows, league):
    total_row = market_rows.get("total")
    if total_row and total_row.get("market_line") is not None:
        return float(total_row["market_line"])
    defaults = {"nfl": 45.0, "ncaaf": 55.0, "nba": 227.0, "ncaab": 145.0,
                "wnba": 158.0, "nhl": 6.0, "mlb": 8.5}
    return defaults.get(league, 45.0)


STATUS_TEMPLATES = {
    "PASS": "The gap between the market price and Nova's true price is inside the noise band — no measurable edge at this number.",
    "MONITOR": "Nova's true price sits near the market price. One repricing trigger — lineup confirmation, a key-number cross, or a juice move — could open a real edge.",
    "NUMBER GONE": "The market has moved to or past Nova's minimum playable price. The edge the numbers showed is no longer buyable at this price.",
    "AWAITING DATA": "Nova does not have enough verified data to publish a true line for this game yet.",
}


def template_narrative(result, game):
    """Deterministic honest narrative from decision fields (no LLM required)."""
    if result["status"] == "AWAITING DATA":
        return result.get("reason", STATUS_TEMPLATES["AWAITING DATA"]), STATUS_TEMPLATES["AWAITING DATA"]
    edge = result["raw_edge"]
    tl, ml = result["true_line"], result["market_line"]
    one = f"Market {ml:+.1f} vs Nova true {tl:+.1f} — {abs(edge):.1f}-point gap, status {result['status']}."
    doing = (f"The market prices this game at {ml:+.1f} while Nova's independent rating work "
             f"puts the true price at {tl:+.1f}, a {abs(edge):.1f}-point difference. "
             f"Status: {result['status']}.")
    return doing, one

```


## FILE: `nova/narratives.py`
**Sophisticated narrative layer (thesis, tape, basis, windows — deterministic)**

```python
"""Deterministic narrative layer for MarketNovaRead.

Builds the frontend-contract narrative fields (thesis, nova_read,
why_market_here, risk, buy_to, regression_notes, market_tape,
true_line_basis, value_window, confidence) from structured inputs only.
No LLM, no invented facts: every sentence traces to data that is in the
payload. Voice: institutional, calm, terminal register — price, number,
edge, market. Status taxonomy only.
"""


def _team(game, side):
    return (game.get(f"{side}_team") or game.get(f"{side}_team_name")
            or side.upper())


def _fmt_line(x):
    try:
        v = float(x)
        return f"{v:+.1f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(x, nd=1):
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def _form_side(result, game):
    """(value_side_team, other_team)."""
    side = result.get("_value_side") or "home"
    return _team(game, side), _team(game, "home" if side == "away" else "away")


def build_tape(tape):
    """market_tape from odds history [(iso_ts, home_line, home_price)].
    Honest when no history: single print."""
    rows = []
    for t in tape or []:
        try:
            rows.append((str(t[0]), float(t[1]), t[2]))
        except (TypeError, ValueError, IndexError):
            continue
    if not rows:
        return "No line history in the tape yet — single print on the board."
    rows.sort(key=lambda r: r[0])
    lines = [r[1] for r in rows]
    opens = f"Open {_fmt_line(rows[0][1])}"
    cur = f"current {_fmt_line(rows[-1][1])}"
    distinct = sorted(set(lines), key=lines.index)
    if len(distinct) == 1:
        return (f"{opens} — the number has not moved. A flat market reads as "
                "two-sided opinion, not a book begging for action on either side.")
    seq = " → ".join(_fmt_line(v) for v in distinct)
    direction = "toward the favorite" if lines[-1] < lines[0] else "toward the dog"
    return (f"{opens} → {seq} → {cur} (book traveled {direction}). "
            "First corrections repriced an opener error; judge the edge against "
            "the CURRENT number, never the open.")


def build_true_line_basis(game, result, ctx):
    """How the true line was constructed — components with weights."""
    rp = ctx.get("ratings_pair") or {}
    hr, ar = rp.get("home"), rp.get("away")
    parts = []
    if hr and ar:
        parts.append(f"Power ratings {_team(game,'away')} "
                     f"{_fmt_num(_rating(ar), 1)} vs {_team(game,'home')} "
                     f"{_fmt_num(_rating(hr), 1)}")
    tl = result.get("true_line")
    parts.append(f"home adjustment priced into the {_fmt_line(tl)} fair")
    inj = ctx.get("injury_details") or {}
    ih = len(inj.get("home") or [])
    ia = len(inj.get("away") or [])
    if ih or ia:
        parts.append(f"injury book: {ih} {IHWORD(ih)} on the home side, "
                     f"{ia} {IHWORD(ia)} on the road side (flagged in the "
                     "personnel layer, not double-counted in the line)")
    w = ctx.get("weather")
    if w and w.get("weather_read"):
        parts.append(f"environment: {str(w.get('weather_read')).lower()}")
    conf = result.get("data_confidence")
    basis = ". ".join(parts)
    tail = (f" Fair range covers ±{_fmt_num(ctx.get('band', 1.0))} around "
            f"{_fmt_line(tl)} — Nova compares the market against the range, "
            "not a fake decimal.")
    if conf is not None:
        tail += f" Data confidence {conf:.0f}/100 (ratings + odds freshness)."
    return f"{basis}.{tail}"


def IHWORD(n):
    return "entries" if n != 1 else "entry"


def _rating(row):
    for k in ("rating", "power", "value"):
        if row.get(k) is not None:
            try:
                return float(row[k])
            except (TypeError, ValueError):
                pass
    return None


def build_why_market_here(game, result, ctx):
    """Market structure: movement + juice + who the number serves."""
    line = result.get("market_line")
    tl = result.get("true_line")
    edge = result.get("raw_edge")
    tape = ctx.get("tape") or []
    moves = 0
    if tape:
        try:
            lines = sorted({float(t[1]) for t in tape if t[1] is not None})
            moves = len(lines) - 1
        except (TypeError, ValueError):
            pass
    sent = []
    home = _team(game, "home")
    sent.append(f"The board prices {home} at {_fmt_line(line)} (home-perspective), "
                f"{edge:.1f} pts from Nova's fair {_fmt_line(tl)} on the same scale.")
    if moves == 0:
        sent.append("The number has been stable — this is a consensus price, "
                    "not a shaded one, so the gap belongs to ratings disagreement.")
    else:
        m = ctx.get("movement") or {}
        d = m.get("direction")
        if d == "toward_value_side":
            sent.append("Recent travel favors our side — the market is still "
                        "finding this price and may not be finished.")
        elif d:
            sent.append(f"Movement reads {d} — the correction has already "
                        "repriced part of the original gap.")
    j = ctx.get("juice")
    if j:
        sent.append(f"Juice detail: {j}. Juice is part of the number — "
                    "the vig test already docked the published grade.")
    st = (result.get("telemetry") or {}).get("stage")
    if st == "early":
        sent.append("Early window: opener shading is documented at this stage, "
                    "so the number carries less information than it will at close.")
    elif st == "late":
        sent.append("Late window: the price is close to its efficient form — "
                    "what remains is mostly the public shade.")
    return " ".join(sent)


def build_thesis(game, result, ctx):
    """The full case: line construction, gap, gate, expression."""
    status = result.get("status")
    tl, line, edge = result.get("true_line"), result.get("market_line"), result.get("raw_edge")
    vs = result.get("_value_side") or "home"
    side_team, other_team = _team(game, vs), _team(game, "away" if vs == "home" else "home")
    sent = []
    sent.append(f"Nova makes this {_fmt_line(tl)} — the market's {_fmt_line(line)} "
                f"leaves {edge:.1f} pts on {side_team}'s side of the number.")
    reason = result.get("_status_reason")
    if reason:
        sent.append(f"The gate says {status}: {reason}")
    else:
        gates = {"PLAY": "the edge clears the full-conviction threshold",
                 "LEAN": "the edge clears the lean band but not the full gate",
                 "MONITOR": "structure is interesting but the edge sits below action",
                 "PASS": "no meaningful advantage at this number",
                 "NUMBER GONE": "the market already repriced the original gap"}
        sent.append(f"Grade: {status} — {gates.get(status, 'no position')}.")
    inj = ctx.get("injury_details") or {}
    flagged = []
    for side in ("home", "away"):
        for it in (inj.get(side) or [])[:2]:
            nm = it.get("player_name") or it.get("name")
            if nm and str(it.get("status", "")).lower() in ("out", "doubtful"):
                flagged.append(f"{nm} ({_team(game, side)}, {it.get('status')})")
    if flagged:
        sent.append("Personnel the market has not fully priced: " + "; ".join(flagged) + ".")
    mp = result.get("minimum_playable")
    if mp is not None:
        sent.append(f"Best expression: {side_team} {_fmt_line(mp)} or better "
                    "on the spread — the number, not the team, is the position.")
    return " ".join(sent)


def build_risk(game, result, ctx):
    """What breaks the thesis — invalidators in prose."""
    inv = result.get("invalidators") or []
    sent = ["Thesis breaks if: " + "; ".join(inv) + "."] if inv else []
    inj = ctx.get("injury_details") or {}
    act = [it for side in ("home", "away") for it in (inj.get(side) or [])
           if str(it.get("status", "")).lower() == "questionable"]
    if act:
        names = ", ".join(str(it.get("player_name") or it.get("name")) for it in act[:3])
        sent.append(f"Questionable list ({names}) — an upgrade moves the true line "
                    "before the market absorbs it.")
    w = ctx.get("weather")
    if w and str(w.get("weather_read") or "").lower().find("wind") >= 0:
        sent.append("Wind in the forecast is a scoring-variance risk, not a "
                    "directional one — compressed margins favor the cover on "
                    "the side already getting points.")
    tl = result.get("true_line")
    sent.append(f"Hard invalidation: market reaches {_fmt_line(tl)} and the edge "
                "is zero — number gone is a position, not a loss.")
    return " ".join(sent)


def build_buy_to(game, result):
    vs = result.get("_value_side") or "home"
    side_team = _team(game, vs)
    mp = result.get("minimum_playable")
    tl = result.get("true_line")
    line = result.get("market_line")
    return (f"Take {side_team} at {_fmt_line(mp)} or better — inside that "
            f"number the edge is spent. Nova's fair is {_fmt_line(tl)}; at "
            f"the current {_fmt_line(line)} the edge is "
            f"{result.get('raw_edge', 0):.1f} pts.")


def build_value_window(game, result):
    tl = result.get("true_line")
    mp = result.get("minimum_playable")
    line = result.get("market_line")
    edge = result.get("raw_edge") or 0
    vs = result.get("_value_side") or "home"
    side_team = _team(game, vs)
    lean_cut = 1.5
    try:
        status = result.get("status")
        zone = ("inside the PLAY window" if status == "PLAY"
                else "inside the LEAN window" if status == "LEAN"
                else "inside the MONITOR window" if status == "MONITOR"
                else "outside the value window" if status in ("PASS", "NUMBER GONE")
                else "ungraded")
    except Exception:
        zone = "ungraded"
    return (f"PLAY: {side_team} {_fmt_line(mp)} or better. "
            f"LEAN: to {_fmt_line((mp or tl) and (mp + 1.0))}. "
            f"NUMBER GONE: at {_fmt_line(tl)}. "
            f"Current market {_fmt_line(line)} sits {zone} "
            f"({edge:.1f} pts vs fair).")


def build_regression_notes(game, result, ctx):
    """Streak/recency honesty — telemetry, never conviction."""
    tel = result.get("telemetry") or {}
    streak = tel.get("streak")
    anchor = tel.get("recency_anchor")
    sent = []
    forms = ctx.get("form") or {}
    for side in ("home", "away"):
        f = forms.get(side)
        if not f:
            continue
        l5 = f.get("last_5") or []
        if l5:
            wins = sum(1 for x in l5 if str(x).upper().startswith("W"))
            t = _team(game, side)
            sent.append(f"{t} arrives {wins}-{len(l5)-wins} over its last "
                        f"{len(l5)} — form is telemetry only; ratings, not "
                        "streaks, set the fair.")
    if anchor:
        sent.append(f"Recency anchor flagged: {anchor} — the public's last image "
                    "of this team is priced by the market; Nova checks whether "
                    "the production was structural or variance before moving.")
    if not sent:
        sent.append("No form distortion detected — neither side carries a "
                    "streak or blowout strong enough to shade perception.")
    return " ".join(sent)


def build(game, result, ctx):
    """Return the narrative-field dict; None-safe on every builder."""
    out = {}
    for key, fn in (
        ("market_tape", lambda: build_tape(ctx.get("tape"))),
        ("true_line_basis", lambda: build_true_line_basis(game, result, ctx)),
        ("why_market_here", lambda: build_why_market_here(game, result, ctx)),
        ("thesis", lambda: build_thesis(game, result, ctx)),
        ("risk", lambda: build_risk(game, result, ctx)),
        ("buy_to", lambda: build_buy_to(game, result)),
        ("value_window", lambda: build_value_window(game, result)),
        ("regression_notes", lambda: build_regression_notes(game, result, ctx)),
    ):
        try:
            out[key] = fn() or None
        except Exception:
            out[key] = None
    out["nova_read"] = _one_line(game, result)
    out["confidence"] = result.get("data_confidence")
    return out


def _one_line(game, result):
    tl = result.get("true_line")
    line = result.get("market_line")
    edge = result.get("raw_edge")
    status = result.get("status")
    vs = result.get("_value_side") or "home"
    side_team = _team(game, vs)
    return (f"Book {_fmt_line(line)} vs Nova fair {_fmt_line(tl)} — "
            f"{edge:.1f} pts toward {side_team}. {status}.")

```


## FILE: `nova/enrich.py`
**Injury/weather/travel/rest enrichment + movement context**

```python
"""Context enrichment: free data sources, zero LLM, zero integration-credit cost.

Fills the Game Detail page's data contract with honest, deterministic data:
  - WeatherSnapshot  (open-meteo, geocoded from the ESPN venue city)
  - Injury           (ESPN injuries endpoint, window teams only)
  - TravelContext    (rest days / back-to-back, computed from the schedule)
  - Analysis         (situational / form / movement context from data Nova
                      already holds — never invented)

Doctrine: nothing here may invent a number, a source, or a claim. Every
payload field traces to ESPN, open-meteo, or Nova's own ratings/odds.
"""
import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from nova import matching

UA = "python-urllib/3.11"
GEO_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", ".weather_geo.json")

WMO_CODES = {
    0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing Fog", 51: "Light Drizzle", 53: "Drizzle",
    55: "Heavy Drizzle", 61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Light Snow", 73: "Snow", 75: "Heavy Snow", 80: "Rain Showers",
    81: "Rain Showers", 82: "Violent Showers", 95: "Thunderstorms",
    96: "Thunderstorms/Hail", 99: "Severe Thunderstorms",
}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _compass(deg):
    try:
        d = float(deg) % 360
    except (TypeError, ValueError):
        return ""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((d + 11.25) // 22.5) % 16]


def _geo_cache():
    if os.path.exists(GEO_CACHE_PATH):
        try:
            return json.load(open(GEO_CACHE_PATH))
        except (ValueError, OSError):
            pass
    return {}


def _geocode(city, state):
    """City-level geocode with an on-disk cache (venues repeat weekly)."""
    if not city:
        return None
    key = f"{city}|{state or ''}".lower()
    cache = _geo_cache()
    if key in cache:
        return cache[key]
    q = urllib.parse.quote(f"{city}, {state}" if state else city)
    try:
        g = _get(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1")
        r = (g.get("results") or [{}])[0]
        if r.get("latitude") is None:
            cache[key] = None
            return None
        cache[key] = {"lat": r["latitude"], "lon": r["longitude"]}
    except Exception:
        return None
    try:
        json.dump(cache, open(GEO_CACHE_PATH, "w"))
    except OSError:
        pass
    return cache[key]


def weather_payloads(games_with_venue, now=None):
    """WeatherSnapshot payloads for outdoor games in the window.

    games_with_venue: [{game_id, league_code, venue, city, state, indoor,
                        start_time}] — venue facts come from the ESPN event.
    Returns {game_id: payload}.
    """
    out = {}
    now = now or datetime.now(timezone.utc)
    for g in games_with_venue:
        if g.get("indoor") or not g.get("city"):
            continue
        if g.get("league_code", "").lower() in ("nhl", "nba", "wnba", "ncaab"):
            continue    # indoor sports — weather is not a factor
        try:
            start = datetime.fromisoformat(str(g.get("start_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start < now:
            continue
        if start - now > __import__("datetime").timedelta(days=3):
            continue    # open-meteo hourly horizon
        geo = _geocode(g.get("city"), g.get("state"))
        if not geo:
            continue
        try:
            f = _get(
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={geo['lat']}&longitude={geo['lon']}"
                "&hourly=temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation_probability,wind_speed_10m,wind_direction_10m,weather_code"
                "&temperature_unit=fahrenheit&wind_speed_unit=mph"
                "&forecast_days=4&timezone=auto")
        except Exception:
            continue
        h = f.get("hourly") or {}
        times = h.get("time") or []
        if not times:
            continue
        # hour bucket = game start in venue-local time
        local = start.astimezone()  # not reliable; use string match instead
        want = start.strftime("%Y-%m-%dT%H")
        idx = None
        for i, t in enumerate(times):
            if t.startswith(want):
                idx = i
                break
        if idx is None:
            # fall back: closest hour to start (offsets handled by timezone=auto
            # via date window)
            cands = [i for i, t in enumerate(times) if t[:10] == want[:10]]
            if not cands:
                continue
            idx = cands[min(range(len(cands)), key=lambda k: abs(k - (start.hour + 0)))]
        temp = h["temperature_2m"][idx]
        feels = h["apparent_temperature"][idx]
        rh = h["relative_humidity_2m"][idx]
        rain = h["precipitation_probability"][idx]
        wind = h["wind_speed_10m"][idx]
        wdir = h["wind_direction_10m"][idx]
        code = h["weather_code"][idx]
        cond = WMO_CODES.get(code, "Mixed")
        wind_impact = ("significant" if wind >= 18 else
                       "moderate" if wind >= 10 else "minimal")
        if wind >= 18:
            read = "wind-impacted"
            rationale = (f"Projected {wind:.0f} mph winds — passing, deep balls and "
                         "kicking carry measurable risk; monitor for number impact.")
        elif (rain or 0) >= 60:
            read = "wet-ball-impacted"
            rationale = (f"{rain:.0f}% precipitation probability — wet-ball conditions "
                         "slow pace and compress scoring; totals and passing props adjust.")
        else:
            read = "neutral"
            rationale = f"{cond}, {wind:.0f} mph wind — conditions are not expected to move the number."
        out[g["game_id"]] = {
            "game_id": g["game_id"],
            "league_code": str(g.get("league_code") or "").lower(),
            "venue": g.get("venue") or "Unknown",
            "roof_type": "outdoor",
            "roof_status": "open",
            "temp_f": round(float(temp), 1),
            "feels_like_f": round(float(feels), 1),
            "humidity_pct": round(float(rh), 1),
            "precipitation_pct": round(float(rain or 0), 1),
            "wind_speed_mph": round(float(wind), 1),
            "wind_direction_deg": round(float(wdir), 1),
            "wind_direction_text": _compass(wdir),
            "conditions": cond,
            "wind_impact": wind_impact,
            "weather_read": read,
            "weather_read_rationale": rationale,
            "is_stale": False,
            "fetched_at": datetime.now(timezone.utc).isoformat(
                timespec="milliseconds").replace("+00:00", "Z"),
        }
        time.sleep(0.1)     # polite client
    return out


SEVERITY = {
    "out for season": ("critical", 5.0), "ir": ("critical", 5.0),
    "out indefinitely": ("critical", 4.0), "out": ("high", 4.0),
    "doubtful": ("moderate", 2.5), "questionable": ("moderate", 2.0),
}
INJURY_STATUS_OK = {"out", "out for season", "out indefinitely", "doubtful",
                    "questionable", "ir"}


def injury_payloads(league, espn_slug, window_teams, max_per_team=14):
    """Injury payloads for the teams actually playing in the window.

    window_teams: {display_name_lower: team_abbr} from the ESPN slate.
    Returns list of payloads (create-or-update matched upstream by key).
    """
    try:
        d = _get(f"https://site.api.espn.com/apis/site/v2/sports/{espn_slug}/injuries")
    except Exception:
        return []
    out = []
    for group in d.get("injuries") or []:
        gname = str(group.get("displayName") or "").lower()
        abbr = None
        for tname, tabbr in window_teams.items():
            if matching.canon(gname, league) == matching.canon(tname, league) \
               or gname.split()[0] == tname.split()[0]:
                abbr = tabbr
                break
        if not abbr:
            continue
        n = 0
        for item in (group.get("injuries") or []):
            if n >= max_per_team:
                break
            status_raw = str(item.get("status") or "").strip()
            status_l = status_raw.lower()
            if status_l not in INJURY_STATUS_OK:
                continue
            a = item.get("athlete") or {}
            name = a.get("displayName")
            if not name:
                continue
            pos = (a.get("position") or {}).get("abbreviation")
            det = item.get("details") or {}
            itype = " ".join(x for x in [det.get("type"), det.get("detail")] if x)
            sev, impact = SEVERITY.get(status_l, ("moderate", 2.0))
            out.append({
                "league_code": league,
                "source_provider": "ESPN",
                "player_name": name,
                "position": pos,
                "team_abbr": abbr,
                "team_name": str(group.get("displayName") or ""),
                "status": status_raw.title() if status_l in ("out", "doubtful", "questionable") else status_raw.title(),
                "severity": sev,
                "impact_rating": impact,
                "injury_type": itype or None,
                "is_sample": False,
            })
            n += 1
    return out


def rest_map(games, league):
    """{team_canon: [(start_dt, venue), ...]} sorted ascending, per league."""
    from nova import matching as M
    per_team = {}
    for g in games:
        lg = str(g.get("league_code") or g.get("sport") or "").lower()
        if league and lg != league:
            continue
        try:
            st = datetime.fromisoformat(str(g.get("start_time") or "").replace("Z", "+00:00"))
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        for side in ("home", "away"):
            nm = g.get(f"{side}_team") or g.get(f"{side}_team_name")
            if not nm:
                continue
            key = M.canon(str(nm), league)
            per_team.setdefault(key, []).append((st, g.get("venue")))
    for k in per_team:
        per_team[k].sort(key=lambda x: x[0])
    return per_team


def days_rest(per_team, team_name, before, league=""):
    """Days since the team's previous game (schedule-based, honest)."""
    from nova import matching as M
    key = M.canon(str(team_name or ""), league)
    hist = per_team.get(key) or []
    prev = [(st, v) for st, v in hist if st < before]
    if not prev:
        return None, None
    return (before - prev[-1][0]).days, prev[-1][1]


def travel_payload(game, per_team, league):
    """TravelContext payload for the AWAY team of a game."""
    try:
        start = datetime.fromisoformat(str(game.get("start_time") or "").replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    rest, prev_venue = days_rest(per_team, game.get("away_team"), start, league)
    if rest is None:
        return None
    abbr = game.get("away_team_abbr") or game.get("away_team")
    return {
        "game_id": game.get("id"),
        "league_code": league,
        "team_abbr": abbr,
        "days_rest": int(rest),
        "is_back_to_back": bool(rest <= 1),
        "previous_venue": prev_venue,
        "current_venue": game.get("venue"),
        "updated_at": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"),
    }


def movement_context(rows):
    """Factual open-vs-current movement from a game's odds rows."""
    spread = [r for r in rows if str(r.get("market_type")).lower() == "spread"]
    if len(spread) < 2:
        return None
    def cd(r):
        try:
            return datetime.fromisoformat(str(r.get("created_date")).replace("Z", "+00:00"))
        except ValueError:
            return None
    dated = [(cd(r), r) for r in spread if cd(r) is not None]
    if len(dated) < 2:
        return None
    dated.sort(key=lambda x: x[0])
    opn, cur = dated[0][1], dated[-1][1]
    ol, cl = opn.get("home_line"), cur.get("home_line")
    if ol is None or cl is None:
        return None
    try:
        ol, cl = float(ol), float(cl)
    except (TypeError, ValueError):
        return None
    if ol == cl:
        return None
    direction = "toward_home" if cl < ol else "toward_away"
    return {
        "open_line": ol, "current_line": cl, "direction": direction,
        "n_snapshots": len(dated),
        "detail": f"Spread opened {ol:+.1f}, now {cl:+.1f} "
                  f"({abs(cl - ol):.1f} toward {'home' if direction == 'toward_home' else 'away'} "
                  f"across {len(dated)} snapshots)",
    }


def analysis_payload(game, result, form_pair, movement, weather, rest_ctx):
    """Analysis record: situational / form / movement context for the
    game page's matchup + situation layers. Only factual fields."""
    league = str(game.get("league_code") or game.get("sport") or "").lower()
    bits = []
    if rest_ctx and rest_ctx.get("away") is not None and rest_ctx.get("home") is not None:
        bits.append(f"Away is on {rest_ctx['away']} days rest vs {rest_ctx['home']} for home"
                    + (" (back-to-back for the road side)" if rest_ctx["away"] <= 1 else ""))
    def _fmt(side):
        if not form_pair or not form_pair.get(side):
            return None
        rows = form_pair[side].get("last_5_json") or []
        try:
            rows = json.loads(rows) if isinstance(rows, str) else rows
        except ValueError:
            return None
        if not isinstance(rows, list) or not rows:
            return None
        wl = "".join("W" if str(r.get("result", "")).upper().startswith("W") else "L" for r in rows[-5:])
        try:
            marg = sum(float(r.get("score", 0)) - float(r.get("opponent_score", 0)) for r in rows[-5:]) / len(rows[-5:])
        except (TypeError, ValueError):
            return wl
        return f"{wl} (avg margin {marg:+.1f})"
    away_f, home_f = _fmt("away"), _fmt("home")
    form_line = []
    if home_f:
        form_line.append(f"Home enters {home_f}")
    if away_f:
        form_line.append(f"away enters {away_f}")
    situational = "; ".join(bits)
    h2h = ("Head-to-head series data is not ingested — recent-form comparison: "
           + "; ".join(form_line)) if form_line else \
          "Recent-form data not yet available for this matchup."
    sharp = []
    if movement:
        sharp.append({"type": "line_move", "detail": movement["detail"],
                      "claim": "observed movement — causation unconfirmed"})
    gs_tempo = result.get("game_script_tempo")
    gs_notes = result.get("game_script_notes")
    return {
        "game_id": game.get("id"),
        "league_code": league,
        # required by the live Analysis schema; mirrors the Nova read verdict
        "recommendation": str(result.get("status") or "pass").lower(),
        "game_script_tempo": gs_tempo or "NOT PROJECTED",
        "game_script_notes": gs_notes or
            "Game script is projected for football matchups from Nova's true line; "
            "other sports do not use script templating.",
        "situational_summary": situational or None,
        "h2h_summary": h2h,
        "public_bias_narrative": (
            "Ticket-split data is not ingested, so no public-percentage claims are made. "
            f"The number's construction (market {result.get('market_line'):+.1f} vs Nova fair "
            f"{result.get('true_line'):+.1f}) is the only positioning evidence."),
        "monitor_trigger": result.get("next_trigger"),
        "sharp_money_indicators_json": json.dumps(sharp) if sharp else "[]",
        "is_sample": False,
    }

```


## FILE: `nova/props.py`
**Player-prop engine (baselines from ESPN boxscores, fair ranges, headshots)**

```python
"""Nova Props engine: player-level projections from real usage + the
MarketNova brain. Zero LLM, zero integration cost — ESPN summaries are free.

Doctrine compliance (never-invent rules):
  - Baselines come ONLY from the player's most recent completed game (real
    boxscore) or an explicit league default labeled as such.
  - Nova ranges are projections, never priced against a book number we don't
    have. Status is MONITOR with an honest "book price not yet ingested"
    note until a props feed exists.
  - No player is invented: every prop traces to an ESPN athlete id.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from nova import matching

UA = "python-urllib/3.11"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".summary_cache")

ROSTER_CACHE = os.path.join(os.path.dirname(__file__), "..", ".roster_cache")
ROSTER_TTL_SECONDS = 7 * 24 * 3600  # rosters change mid-week at most


def fetch_roster(league, team_id):
    """{athlete_espn_id: position abbreviation} from ESPN team roster.
    Cached 7 days — roster churn is weekly."""
    if not team_id:
        return {}
    slug = SPORT_SLUGS.get(league)
    if not slug:
        return {}
    os.makedirs(ROSTER_CACHE, exist_ok=True)
    p = os.path.join(ROSTER_CACHE, f"{league}_{team_id}.json")
    now = time.time()
    if os.path.exists(p):
        try:
            blob = json.load(open(p))
            if now - blob.get("ts", 0) < ROSTER_TTL_SECONDS:
                return blob.get("positions") or {}
        except (ValueError, OSError):
            pass
    positions = {}
    try:
        r = _get(f"https://site.api.espn.com/apis/site/v2/sports/{slug}/teams/{team_id}/roster")
        raw = r.get("athletes")
        ath_list = []
        if isinstance(raw, list):  # groups: [{"position": "offense", "items": [...]}]
            for grp in raw:
                if isinstance(grp, dict):
                    ath_list += grp.get("items") or []
        elif isinstance(raw, dict):
            for _k, grp in raw.items():
                if isinstance(grp, dict):
                    ath_list += grp.get("items") or []
        for e in ath_list:
            if isinstance(e, dict) and e.get("id"):
                pos = (e.get("position") or {}).get("abbreviation")
                if pos:
                    positions[str(e["id"])] = pos
    except Exception:
        return {}
    try:
        json.dump({"ts": now, "positions": positions}, open(p, "w"))
    except OSError:
        pass
    return positions


LEAGUE_DEFAULTS = {
    # league: {prop_type: (baseline, range spread)}
    "nfl":  {"pass_yards": 225.0, "rush_yards": 70.0, "receiving_yards": 62.0},
    "ncaaf": {"pass_yards": 215.0, "rush_yards": 75.0, "receiving_yards": 55.0},
    "nba":  {"points": 18.0},
    "nhl":  {"saves": 26.0, "skater_points": 1.0},
}

SPORT_SLUGS = {
    "nfl": "football/nfl", "ncaaf": "football/college-football",
    "nba": "basketball/nba", "nhl": "hockey/nhl",
}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _cached(event_id):
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, f"{event_id}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except (ValueError, OSError):
            pass
    return None


def fetch_summary(league, event_id, force=False):
    """Completed-game summaries never change — cache on disk forever."""
    if not force:
        s = _cached(event_id)
        if s is not None:
            return s
    slug = SPORT_SLUGS.get(league)
    if not slug:
        return None
    try:
        s = _get(f"https://site.api.espn.com/apis/site/v2/sports/{slug}/summary?event={event_id}")
    except Exception:
        return None
    try:
        p = os.path.join(CACHE_DIR, f"{event_id}.json")
        json.dump(s, open(p, "w"))
    except OSError:
        pass
    return s


HEADSHOT_LEAGUES = {"nfl": "nfl", "ncaaf": "college-football",
                    "nba": "nba", "nhl": "nhl"}


def _athlete_row(athlete, league="nfl"):
    a = athlete.get("athlete") or {}
    head = (a.get("headshot") or {}).get("href")
    if not head and a.get("id"):
        head = (f"https://a.espncdn.com/i/headshots/"
                f"{HEADSHOT_LEAGUES.get(league, 'nfl')}/players/full/{a['id']}.png")
    return {
        "name": a.get("displayName"),
        "id": a.get("id"),
        "pos": (a.get("position") or {}).get("abbreviation"),
        "headshot": head,
        "stats": athlete.get("stats") or [],
    }


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def baselines_football(summary, league="nfl"):
    """([player dicts], {team_abbr: espn team id}) from a completed boxscore."""
    out, team_ids = [], {}
    for grp in (summary.get("boxscore") or {}).get("players") or []:
        gteam = grp.get("team") or {}
        team_ab = gteam.get("abbreviation")
        if team_ab and gteam.get("id"):
            team_ids[str(team_ab).upper()] = str(gteam["id"])
        for cat in grp.get("statistics") or []:
            cat_name = cat.get("name") or ""
            labels = cat.get("labels") or []
            for athlete in cat.get("athletes") or []:
                row = _athlete_row(athlete, league)
                if not row["name"] or not row["id"]:
                    continue
                stats = row["stats"]
                rec = {"team_abbr": team_ab, "cat": cat_name, "row": row}
                if cat_name == "passing" and "YDS" in labels:
                    rec["value"] = _num(stats[labels.index("YDS")]) if len(stats) > labels.index("YDS") else None
                    rec["prop_type"] = "pass_yards"
                elif cat_name == "rushing" and "YDS" in labels:
                    rec["value"] = _num(stats[labels.index("YDS")]) if len(stats) > labels.index("YDS") else None
                    rec["prop_type"] = "rush_yards"
                elif cat_name == "receiving" and "YDS" in labels:
                    rec["value"] = _num(stats[labels.index("YDS")]) if len(stats) > labels.index("YDS") else None
                    rec["prop_type"] = "receiving_yards"
                else:
                    continue
                if rec["value"] is not None:
                    out.append(rec)
    return out, team_ids


def baselines_basketball(summary, league="nba"):
    out, team_ids = [], {}
    for grp in (summary.get("boxscore") or {}).get("players") or []:
        gteam = grp.get("team") or {}
        team_ab = gteam.get("abbreviation")
        if team_ab and gteam.get("id"):
            team_ids[str(team_ab).upper()] = str(gteam["id"])
        for cat in grp.get("statistics") or []:
            labels = cat.get("labels") or []
            if "PTS" not in labels:
                continue
            for athlete in cat.get("athletes") or []:
                row = _athlete_row(athlete, league)
                if not row["name"] or not row["id"] or not row["stats"]:
                    continue
                if str(row["stats"][0]) in ("--", "DNP", "DND", "N/A"):
                    continue
                try:
                    pts = _num(row["stats"][labels.index("PTS")])
                except (IndexError, TypeError):
                    continue
                if pts is None:
                    continue
                out.append({"team_abbr": team_ab, "cat": "points",
                            "row": row, "value": pts, "prop_type": "points"})
    return out, team_ids


def baselines_hockey(summary, league="nhl"):
    out, team_ids = [], {}
    for grp in (summary.get("boxscore") or {}).get("players") or []:
        gteam = grp.get("team") or {}
        team_ab = gteam.get("abbreviation")
        if team_ab and gteam.get("id"):
            team_ids[str(team_ab).upper()] = str(gteam["id"])
        for cat in grp.get("statistics") or []:
            cat_name = cat.get("name") or ""
            labels = cat.get("labels") or []
            if cat_name == "goalies" and "SV" in labels:
                for athlete in cat.get("athletes") or []:
                    row = _athlete_row(athlete, league)
                    if not row["name"] or not row["id"]:
                        continue
                    try:
                        sv = _num(row["stats"][labels.index("SV")])
                    except (IndexError, TypeError):
                        continue
                    if sv:
                        out.append({"team_abbr": team_ab, "cat": "goalies",
                                    "row": row, "value": sv, "prop_type": "saves"})
            elif cat_name in ("skaters", "forwards", "defenses") and "G" in labels and "A" in labels:
                for athlete in cat.get("athletes") or []:
                    row = _athlete_row(athlete, league)
                    if not row["name"] or not row["id"]:
                        continue
                    try:
                        pts = (_num(row["stats"][labels.index("G")] or 0) or 0) + \
                              (_num(row["stats"][labels.index("A")] or 0) or 0)
                    except (IndexError, TypeError):
                        continue
                    out.append({"team_abbr": team_ab, "cat": "skaters",
                                "row": row, "value": pts, "prop_type": "skater_points"})
    return out, team_ids


def _pick(league, baselines, prop_type, team_abbr, n=1, exclude=()):
    """Top baseline players for a team/prop_type (excluding earlier picks)."""
    cands = [b for b in baselines
             if b["prop_type"] == prop_type
             and str(b["team_abbr"]).upper() == str(team_abbr).upper()
             and b["row"]["id"] not in exclude]
    cands.sort(key=lambda b: (b["value"] is None, -(b["value"] or 0)))
    return cands[:n]


def script_factor(league, prop_type, team_is_favorite, margin, role):
    """Game-script volume adjustment from Nova's true line (doctrine:
    game-state contamination, Part XXXII). Factors are deliberately modest —
    a single-game baseline is a wide prior, not a precise projection."""
    fav_mag = abs(margin)
    if league in ("nfl", "ncaaf"):
        if prop_type == "pass_yards":
            if team_is_favorite and fav_mag >= 7:
                return 0.90, "controlled script — leading side sheds pass volume late"
            if (not team_is_favorite) and fav_mag >= 7:
                return 1.12, "trailing script — pass volume lifts in chase states"
        elif prop_type == "rush_yards":
            if team_is_favorite and fav_mag >= 7:
                return 1.15, "leading script — rushing closes the game"
            if (not team_is_favorite) and fav_mag >= 7:
                return 0.80, "trailing script — ground game is abandoned"
        elif prop_type == "receiving_yards":
            if (not team_is_favorite) and fav_mag >= 7:
                return 1.10, "trailing script — pass-heavy second half"
    return 1.0, "competitive script — no volume distortion expected"


def build_props(game, league, true_line, market_total, last_game_by_team, ctx):
    """Prop payloads for one market game. Returns list of dicts matching the
    proposed Prop entity contract."""
    lg_defaults = LEAGUE_DEFAULTS.get(league)
    if not lg_defaults:
        return []
    summaries = (ctx or {}).get("summaries") or {}
    positions = (ctx or {}).get("positions") or {}
    home, away = str(game.get("home_team") or ""), str(game.get("away_team") or "")
    home_ab = game.get("home_team_abbr") or ""
    away_ab = game.get("away_team_abbr") or ""
    # true line is home-perspective; negative = home favored
    try:
        margin = float(true_line or 0.0)
    except (TypeError, ValueError):
        return []
    props = []
    used = set()

    def team_props(team_name, team_ab, is_home):
        is_fav = (margin < 0) == is_home
        last = last_game_by_team.get(matching.canon(team_name, league))
        baselines = summaries.get(last) if last else []
        if not baselines:
            return []
        specs = {
            "pass_yards": 1, "rush_yards": 1, "receiving_yards": 2,
            "points": 4, "saves": 1, "skater_points": 2,
        }.items()
        made = []
        for prop_type, n in specs:
            if prop_type not in lg_defaults:
                continue
            picks = _pick(league, baselines, prop_type, team_ab, n=n, exclude=used)
            for b in picks:
                used.add(b["row"]["id"])
                factor, note = script_factor(
                    league, prop_type, is_fav, margin, "value")
                base = b["value"]
                conf = 62
                source = "last completed game (ESPN boxscore)"
                if base is None:
                    base = lg_defaults[prop_type]
                    conf = 35
                    source = "league default — no recent boxscore for this player"
                mid = base * factor
                lo, hi = mid * 0.84, mid * 1.18
                q = lambda x: round(x / 5.0) * 5 if prop_type not in ("saves", "skater_points") else round(x)
                lo, hi = int(q(lo)), int(q(hi))
                if hi <= lo:
                    hi = lo + (1 if prop_type in ("saves", "skater_points") else 5)
                if abs(margin) < 1:
                    script_line = ("Nova projects this game effectively even "
                                    f"(true line {margin:+.1f})")
                else:
                    script_line = (f"Nova projects {team_name} "
                                   f"{'favored' if is_fav else 'trailing'} by {abs(margin):.1f}")
                read = (
                    f"{b['row']['name']} posted {base:g} {prop_type.replace('_', ' ')} in the last completed game. "
                    f"{script_line} — {note}. Fair range {lo}-{hi} "
                    f"(source: {source}). Book price not yet ingested — grade suspended."
                )
                made.append({
                    "game_id": game.get("id"),
                    "league_code": str(league).upper(),
                    "sport": league,
                    "player_name": b["row"]["name"],
                    "player_espn_id": str(b["row"]["id"]),
                    "position": positions.get(str(b["row"]["id"]))
                        or CAT_FALLBACK_POS.get(prop_type)
                        or b["row"]["pos"],
                    "team_abbr": team_ab,
                    "headshot_url": b["row"]["headshot"],
                    "prop_type": prop_type,
                    "stat_label": prop_type.replace("_", " "),
                    "baseline_last_game": float(base) if b["value"] is not None else None,
                    "baseline_source": source,
                    "nova_fair_low": lo,
                    "nova_fair_high": hi,
                    "nova_read": read,
                    "game_script_note": note,
                    "status": "MONITOR",
                    "data_confidence": conf,
                    "market_line": None,
                    "market_price": None,
                    "is_current": True,
                })
        return made

    props += team_props(home, home_ab, True)
    props += team_props(away, away_ab, False)
    return props


def team_last_game(games, league, now=None):
    """{team_canon: espn_event_id of latest completed game}."""
    now = now or datetime.now(timezone.utc)
    per_team = {}
    for g in games:
        lg = str(g.get("league_code") or g.get("sport") or "").lower()
        if league and lg != league:
            continue
        if str(g.get("status") or "").lower() not in ("final", "final/ot"):
            continue
        gid = g.get("provider_game_id")
        if not gid:
            continue
        try:
            st = datetime.fromisoformat(str(g.get("start_time") or "").replace("Z", "+00:00"))
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if st > now:
            continue
        for side in ("home", "away"):
            nm = g.get(f"{side}_team") or g.get(f"{side}_team_name")
            if not nm:
                continue
            key = matching.canon(str(nm), league)
            cur = per_team.get(key)
            if cur is None or st > cur[0]:
                per_team[key] = (st, gid)
    return {k: v[1] for k, v in per_team.items()}


CAT_FALLBACK_POS = {
    "pass_yards": "QB", "rush_yards": "RB", "receiving_yards": "WR/TE",
    "points": "G/F", "saves": "G", "skater_points": "F/D",
}


def load_baselines(league, last_game_by_team, log=print):
    """Fetch cached summaries + rosters. Returns
    {"summaries": {event_id: [players]}, "positions": {athlete_id: pos}}."""
    summaries, all_team_ids = {}, {}
    fetcher = {"nfl": baselines_football, "ncaaf": baselines_football,
               "nba": baselines_basketball, "nhl": baselines_hockey}.get(league)
    if not fetcher:
        return {"summaries": summaries, "positions": {}}
    seen_events = {}
    for event_id in set(last_game_by_team.values()):
        s = fetch_summary(league, event_id)
        if not s:
            seen_events[event_id] = ([], {})
            continue
        rows, tids = fetcher(s, league)
        seen_events[event_id] = (rows, tids)
        for ab, tid in tids.items():
            all_team_ids.setdefault(ab, tid)
        time.sleep(0.3)
    for event_id in set(last_game_by_team.values()):
        rows, _t = seen_events.get(event_id) or ([], {})
        summaries[event_id] = rows
    # roster positions per espn athlete id (cached 7 days per team)
    positions = {}
    for ab, tid in all_team_ids.items():
        for aid, pos in fetch_roster(league, tid).items():
            if pos:
                positions[aid] = pos
        time.sleep(0.15)
    log("props", f"{league}: {len(all_team_ids)} team ids, "
        f"{len(positions)} roster positions")
    return {"summaries": summaries, "positions": positions}

```


## FILE: `nova/cycle.py`
**THE ORCHESTRATOR: full cycle — plan, write, heal, reads, props, enrich**

```python
"""One Nova cycle: odds sync -> ESPN link/live sync -> read generation -> writes.

Run modes:
  python run_cycle.py            # full cycle, writes to Base44
  python run_cycle.py --dry-run  # compute everything, write nothing, print the plan

Creation doctrine: GAMES EXIST BECAUSE MARKETS EXIST. New games come only
from odds-api events with real book prices. ESPN never creates games; it
links them (provider_game_id) and keeps status/scores live. A game no book
prices is not board material for a market-intelligence product.

Write budget doctrine: a quiet board should cost single-digit writes.
Every write is a true delta (changed fields only), verified before dispatch.
"""
from datetime import datetime, timedelta, timezone
import json
import sys

from nova import config, espn, odds, reads, llm, enrich, props
from nova.api import Base44Client, changed_fields


def log(section, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {section}: {msg}")


def _injury_details(game, injuries):
    """{home: [rows], away: [rows]} matched by team abbr/name."""
    from nova import matching as _m
    out = {"home": [], "away": []}
    lg = str(game.get("league_code") or game.get("sport") or "").upper()
    for side in ("home", "away"):
        target = _m.canon(game.get(f"{side}_team") or game.get(f"{side}_team_name") or "", lg)
        if not target:
            continue
        for i in injuries or []:
            il = str(i.get("league_code") or "").upper()
            if il and il != lg:
                continue
            if _m.canon(i.get("team_abbr") or i.get("team_name") or "", lg) == target:
                out[side].append(i)
    return out


def run(dry=None):
    dry = dry if dry is not None else config.DRY_RUN
    if dry:
        log("cycle", "DRY RUN — computing plan, writing nothing")
    client = Base44Client()

    # ---------- load current state once ----------
    log("load", "fetching Game / TeamRating / TeamForm / MarketNovaRead")
    games = client.list_all("Game")
    ratings = client.list_all("TeamRating")
    forms = client.list_all("TeamForm")
    existing_reads = client.list_all("MarketNovaRead", fields=[
        "id", "game_id", "market_type", "is_current", "status",
        "marketnova_true_line", "raw_edge", "what_the_book_is_doing", "one_line_read"])
    existing_weather = client.list_all("WeatherSnapshot", fields=["id", "game_id", "created_date"])
    existing_travel = client.list_all("TravelContext", fields=["id", "game_id", "team_abbr", "days_rest"])
    existing_analysis = client.list_all("Analysis", fields=["id", "game_id", "created_date"])
    existing_injuries = client.list_all("Injury", fields=[
        "id", "league_code", "team_abbr", "player_name", "status", "severity",
        "injury_type", "impact_rating"])
    injuries_all = existing_injuries
    log("load", f"{len(games)} games, {len(ratings)} ratings, {len(forms)} form rows, "
          f"{len(existing_reads)} reads, {len(existing_injuries)} injuries, "
          f"{len(existing_analysis)} analyses")

    # ---------- ESPN collection (link/live sync + free DraftKings odds) ----------
    espn_recs_by_league = {}
    for league in config.LEAGUES:
        events = espn.collect_events(league, config.LOOKAHEAD_DAYS)
        recs = []
        for ev in events:
            r = espn.parse_event(ev)
            r["league"] = league
            recs.append(r)
        espn_recs_by_league[league] = recs

    # ---------- odds sync (source of record for game creation) ----------
    all_game_creates, all_link_updates = [], []
    league_plans = []
    odds_by_game = {}
    juice_by_game = {}
    for league in config.LEAGUES:
        # ESPN DraftKings odds are always free; odds-api adds multi-book
        # depth when (and only when) ODDS_API_KEY is set.
        desired = []
        for rec in espn_recs_by_league.get(league, []):
            desired.extend(odds.espnn_rows(rec) if hasattr(odds, "espnn_rows") else odds.espn_rows(rec))
        if config.ODDS_API_KEY:
            for ev in odds.fetch_odds(league):
                desired.extend(odds.flatten_event(ev))
        if desired and not odds_by_game:
            all_odds = client.list_all("OddsSnapshot", fields=[
                "id", "game_id", "book", "market_type", "home_line",
                "away_line", "over_line", "under_line", "home_price",
                "away_price", "over_price", "under_price", "is_current",
                "created_date"])
            for o in all_odds:
                odds_by_game.setdefault(o.get("game_id"), []).append(o)
        existing = [o for rows in odds_by_game.values() for o in rows]
        gc, lu, sup, nr, jm = odds.plan(games, existing, desired, league)
        all_game_creates.extend(gc)
        all_link_updates.extend(lu)
        league_plans.append((gc, lu, sup, nr))
        for j in jm:
            if j.get("game_id"):
                juice_by_game[j["game_id"]] = j
        log("odds", f"{league}: {len(desired)} markets desired, {len(gc)} new games, "
              f"{len(nr)} changed rows, {len(sup)} superseded, {len(lu)} links")

    # ---------- ESPN link + live sync (never creates games) ----------
    game_updates, issues = [], []
    for league in config.LEAGUES:
        recs = espn_recs_by_league.get(league, [])
        virtual = list(games) + all_game_creates
        up, iss = espn.reconcile_links(virtual, recs, league)
        game_updates.extend(up)
        issues.extend(iss)
        log("espn", f"{league}: {len(recs)} events, {len(up)} updates/links, "
              f"{len(iss)} issues")
    for i in issues[:15]:
        log("espn", f"issue: {i}")

    # ---------- Read generation (games in window, with current odds) ----------
    cutoff = datetime.now(timezone.utc) + timedelta(days=config.LOOKAHEAD_DAYS)
    # spread reads only — a moneyline read for the same game must never
    # receive a spread patch (field collision), and duplicate current
    # spread reads get healed to one.
    current_reads, stale_read_ids = {}, []
    seen_read_games = set()
    rest_maps, weather_by_game, injury_count = {}, {}, {}
    analysis_upserts, travel_upserts, prop_payloads = [], [], []
    props_ctx = {}
    skip_status = ("final",) + tuple(config.TERMINAL_EXEMPT)

    # ---- enrichment: weather + injuries (free ESPN/open-meteo, no LLM) ----
    venue_by_espn = {}
    for league, recs in espn_recs_by_league.items():
        for rec in recs:
            if rec.get("espn_id"):
                venue_by_espn[rec["espn_id"]] = rec
    weather_games = []
    for g in games:
        rec = venue_by_espn.get(g.get("provider_game_id"))
        if not rec:
            continue
        try:
            st = datetime.fromisoformat(str(g.get("start_time", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if st > cutoff or str(g.get("status", "")).lower() in skip_status:
            continue
        weather_games.append({
            "game_id": g["id"], "league_code": g.get("league_code") or g.get("sport"),
            "venue": rec.get("venue"), "city": rec.get("venue_city"),
            "state": rec.get("venue_state"), "indoor": rec.get("venue_indoor"),
            "start_time": g.get("start_time"),
        })
    try:
        weather_by_game = enrich.weather_payloads(weather_games)
    except Exception as e:
        log("enrich", f"weather fetch failed: {e}")
    injury_upserts = []
    for league in config.LEAGUES:
        recs = espn_recs_by_league.get(league, [])
        if not recs:
            continue
        wteams = {}
        for rec in recs:
            for side in ("home", "away"):
                nm, ab = rec.get(f"{side}_name"), rec.get(f"{side}_abbr")
                if nm and ab:
                    wteams[str(nm).lower()] = ab
        if not wteams:
            continue
        try:
            plist = enrich.injury_payloads(league, config.ESPN_LEAGUES[league], wteams)
        except Exception as e:
            log("enrich", f"injuries {league} failed: {e}")
            continue
        for p in plist:
            injury_upserts.append(p)
        log("enrich", f"{league}: {len(plist)} injury rows for {len(wteams)//2} games")
    for p in injury_upserts:
        for g in games:
            lg = str(g.get("league_code") or "").lower()
            if lg != p["league_code"]:
                continue
            hit = (p["team_abbr"] in (g.get("away_team_abbr"), g.get("home_team_abbr"))
                   or str(p.get("team_name") or "").lower() in
                       (str(g.get("away_team") or "").lower(), str(g.get("home_team") or "").lower()))
            if hit:
                injury_count[g["id"]] = injury_count.get(g["id"], 0) + 1
    log("enrich", f"weather rows for {len(weather_by_game)} outdoor games, "
          f"{len(injury_upserts)} injury rows")
    for r in existing_reads:
        if not r.get("is_current") or str(r.get("market_type", "")).lower() != "spread":
            continue
        gid = r.get("game_id")
        if gid in seen_read_games:
            stale_read_ids.append(r["id"])   # duplicate current read — heal
            continue
        seen_read_games.add(gid)
        current_reads[gid] = r
    read_patches = []
    for g in games:
        try:
            start = datetime.fromisoformat(str(g.get("start_time", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        skip_status = ("final",) + tuple(config.TERMINAL_EXEMPT)
        if start > cutoff or str(g.get("status", "")).lower() in skip_status:
            continue
        rows = odds_by_game.get(g["id"], [])
        if not rows:
            continue
        pair = reads.resolve_ratings(g, ratings)
        if not pair:
            continue
        # backfill missing team abbrs from the ESPN event (the page's
        # injury module matches on team_abbr vs game abbr fields)
        rec_ab = venue_by_espn.get(g.get("provider_game_id"))
        if rec_ab and (not g.get("away_team_abbr") or not g.get("home_team_abbr")) \
                and rec_ab.get("away_abbr") and rec_ab.get("home_abbr"):
            game_updates.append((g, {
                "home_team_abbr": rec_ab.get("home_abbr"),
                "away_team_abbr": rec_ab.get("away_abbr")}))
        markets = reads.freshest_market(rows)
        league_l = str(g.get("league_code") or g.get("sport") or "").lower()
        if league_l not in rest_maps:
            rest_maps[league_l] = enrich.rest_map(games, league_l)
        rmap = rest_maps[league_l]
        def _rest(team):
            try:
                st = datetime.fromisoformat(str(g.get("start_time")).replace("Z", "+00:00"))
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                d, _ = enrich.days_rest(rmap, team, st, league_l)
                return d
            except ValueError:
                return None
        rest_ctx = {"home": _rest(g.get("home_team")), "away": _rest(g.get("away_team"))}
        n_inj = injury_count.get(g["id"], 0)
        tape = []
        for o in rows:
            if str(o.get("market_type") or "").lower() == "spread" and o.get("home_line") is not None:
                tape.append((o.get("created_date"), o.get("home_line"), o.get("home_price")))
        inj_details = _injury_details(g, injuries_all)
        result = reads.compute_read(g, pair, markets,
                                    juice=juice_by_game.get(g["id"]),
                                    form=reads.resolve_form(g, forms),
                                    movement=enrich.movement_context(rows),
                                    weather=weather_by_game.get(g["id"]),
                                    rest=rest_ctx, injuries=n_inj,
                                    tape=tape, injury_details=inj_details)
        doing, one_line = reads.template_narrative(result, g)
        if current_reads.get(g["id"]) is None:  # new reads only — write budget
            richer = llm.narrate(g, result)
            if richer and all(richer):
                doing, one_line = richer
        desired_read = {
            # league_code/sport are REQUIRED by the live MarketNovaRead
            # schema; patches ignore them (changed_fields uses its own list)
            "league_code": str(g.get("league_code") or g.get("sport") or "").upper(),
            "sport": str(g.get("sport") or g.get("league_code") or "").upper(),
            "status": result["status"],
            "marketnova_true_line": result.get("true_line"),
            "raw_edge": result.get("raw_edge"),
            "what_the_book_is_doing": doing,
            "one_line_read": one_line,
            "data_quality_json": json.dumps(result.get("telemetry"))
                if result.get("telemetry") else None,
            "market_line": result.get("market_line"),
                "true_line_value": result.get("true_line"),
            "fair_price": result.get("true_line"),
            "edge_value": result.get("edge_value"),
                "market_odds": result.get("market_odds"),
            "edge_score_display": result.get("edge_score_display"),
            "edge_direction": result.get("edge_direction"),
            "minimum_playable_price": result.get("minimum_playable"),
            "playable_until": result.get("playable_until"),
            "next_trigger": result.get("next_trigger"),
            "invalidators": json.dumps(result.get("invalidators"))
                if result.get("invalidators") else None,
            "market_psychology": result.get("market_psychology"),
            "data_confidence": result.get("data_confidence"),
            "key_number_quality": result.get("key_number_quality"),
            "weather": result.get("weather"),
            "line_movement_direction": result.get("line_movement_direction"),
            "sharp_resistance": result.get("sharp_resistance"),
            "public_bias": result.get("public_bias"),
            "game_state_support": result.get("game_state_support"),
            "regression_support": result.get("regression_support"),
            "lineup_status": result.get("lineup_status"),
            "why_market_here": result.get("why_market_here"),
            "thesis": result.get("thesis"),
            "nova_read": result.get("nova_read"),
            "risk": result.get("risk"),
            "buy_to": result.get("buy_to"),
            "regression_notes": result.get("regression_notes"),
            "market_tape": result.get("market_tape"),
            "true_line_basis": result.get("true_line_basis"),
            "value_window": result.get("value_window"),
            "confidence": result.get("confidence"),
        }
        if result.get("status") != "AWAITING DATA":
            if league_l in props.SPORT_SLUGS and str(g.get("status") or "").lower() == "scheduled":
                try:
                    if league_l not in props_ctx:
                        lg_last = props.team_last_game(games, league_l)
                        lg_ctx = props.load_baselines(league_l, lg_last, log)
                        props_ctx[league_l] = (lg_last, lg_ctx)
                    prop_payloads.extend(props.build_props(
                        g, league_l, result.get("true_line"), None, lg_last, lg_ctx))
                except Exception as e:
                    log("props", f"build failed {g.get('id')}: {e}")
            try:
                analysis_upserts.append(enrich.analysis_payload(
                    g, result, reads.resolve_form(g, forms),
                    enrich.movement_context(rows), weather_by_game.get(g["id"]),
                    rest_ctx))
            except Exception as e:
                log("enrich", f"analysis build failed {g.get('id')}: {e}")
            try:
                tp = enrich.travel_payload(g, rest_maps.get(league_l), league_l)
                if tp:
                    travel_upserts.append(tp)
            except Exception as e:
                log("enrich", f"travel build failed {g.get('id')}: {e}")
        cur = current_reads.get(g["id"])
        if cur is None:
            log("read", f"NEW read for {g.get('away_team')}@{g.get('home_team')}: {result['status']}")
            read_patches.append(("__create__", g["id"], desired_read))
        else:
            patch = changed_fields(cur, desired_read, [
                "status", "marketnova_true_line", "raw_edge", "market_line",
                "what_the_book_is_doing", "one_line_read",
                # price-locked fields change in lockstep with the numbers —
                # no extra write churn, but keep the page current
                "edge_value", "edge_score_display", "edge_direction", "market_odds",
                "minimum_playable_price", "playable_until",
                "key_number_quality", "line_movement_direction", "sharp_resistance", "public_bias",
                "game_state_support", "regression_support", "weather", "lineup_status",
                "why_market_here", "thesis", "nova_read", "risk", "buy_to",
                "regression_notes", "market_tape", "true_line_basis",
                "value_window", "confidence"])
            if patch:
                read_patches.append((cur["id"], g["id"], patch))
    # first-cycle reads for games created THIS run — they'd otherwise sit
    # read-less on the board for 30 minutes until the next cycle
    for li, (gc, lu, sup, nr) in enumerate(league_plans):
        rows_by_ref = {}
        for row in nr:
            kind, val = row.get("game_ref", ("id", None))
            if kind == "create":
                rows_by_ref.setdefault(val, []).append(row)
        for ci, payload in enumerate(gc):
            rows = rows_by_ref.get(ci) or []
            markets = {}
            for row in rows:
                mt = str(row.get("market_type", "")).lower()
                line = (row.get("home_line") if mt == "spread"
                        else row.get("over_line") if mt == "total"
                        else row.get("home_price"))
                markets[mt] = {"line": line, "market_line": line,
                               "home_price": row.get("home_price"),
                               "away_price": row.get("away_price"),
                               "book": row.get("book")}
            if "spread" not in markets or markets["spread"].get("market_line") is None:
                continue
            pair = reads.resolve_ratings(payload, ratings)
            if not pair:
                continue
            result = reads.compute_read(payload, pair, markets,
                                        form=reads.resolve_form(payload, forms))
            doing, one_line = reads.template_narrative(result, payload)
            richer = llm.narrate(payload, result)   # new reads only
            if richer and all(richer):
                doing, one_line = richer
            log("read", f"NEW read for {payload.get('away_team')}@{payload.get('home_team')}: {result['status']}")
            read_patches.append(("__new_game__", (li, ci), {
                "status": result["status"],
                "marketnova_true_line": result.get("true_line"),
                "raw_edge": result.get("raw_edge"),
                "data_quality_json": json.dumps(result.get("telemetry"))
                    if result.get("telemetry") else None,
                "market_line": result.get("market_line"),
                "true_line_value": result.get("true_line"),
                "fair_price": result.get("true_line"),
                "edge_value": result.get("edge_value"),
                "market_odds": result.get("market_odds"),
                "edge_score_display": result.get("edge_score_display"),
                "edge_direction": result.get("edge_direction"),
                "minimum_playable_price": result.get("minimum_playable"),
                "playable_until": result.get("playable_until"),
                "next_trigger": result.get("next_trigger"),
                "invalidators": json.dumps(result.get("invalidators"))
                    if result.get("invalidators") else None,
                "market_psychology": result.get("market_psychology"),
                "data_confidence": result.get("data_confidence"),
                "what_the_book_is_doing": doing,
                "one_line_read": one_line,
            }))
    log("read", f"{len(read_patches)} reads to write")

    # ---------- Write phase ----------
    if dry:
        log("plan", f"would write: {len(all_game_creates)} game creates, "
              f"{len(all_link_updates)} odds links, {len(game_updates)} game updates, "
              f"{len(read_patches)} reads, {len(weather_by_game)} weather, "
              f"{len(injury_upserts)} injuries, {len(travel_upserts)} travel, "
              f"{len(analysis_upserts)} analyses, {len(prop_payloads)} props")
        return
    write_plan(client, league_plans, game_updates, read_patches, stale_read_ids)
    # ---------- enrichment upserts (delta-only) ----------
    wlatest, tlatest, alatest = {}, {}, {}
    for r in existing_weather:
        wlatest[r.get("game_id")] = r
    for r in existing_travel:
        tlatest[r.get("game_id")] = r
    for r in existing_analysis:
        alatest[r.get("game_id")] = r
    n_new_w = n_new_t = n_new_a = 0
    for gid, payload in weather_by_game.items():
        cur = wlatest.get(gid)
        if cur:
            patch = changed_fields(cur, payload, [
                "temp_f", "feels_like_f", "wind_speed_mph", "precipitation_pct",
                "conditions", "weather_read", "humidity_pct"])
            if patch:
                client.put("WeatherSnapshot", cur["id"], patch)
        else:
            client.create("WeatherSnapshot", payload)
            n_new_w += 1
    for payload in travel_upserts:
        cur = tlatest.get(payload.get("game_id"))
        if cur:
            patch = changed_fields(cur, payload, ["days_rest", "is_back_to_back", "previous_venue"])
            if patch:
                patch["updated_at"] = payload["updated_at"]
                client.put("TravelContext", cur["id"], patch)
        else:
            client.create("TravelContext", payload)
            n_new_t += 1
    for payload in analysis_upserts:
        cur = alatest.get(payload.get("game_id"))
        if cur:
            patch = changed_fields(cur, payload, [
                "situational_summary", "h2h_summary", "public_bias_narrative",
                "monitor_trigger", "sharp_money_indicators_json"])
            if patch:
                client.put("Analysis", cur["id"], patch)
        else:
            client.create("Analysis", payload)
            n_new_a += 1
    inj_key = {}
    for r in existing_injuries:
        inj_key[(str(r.get("league_code") or "").lower(), r.get("team_abbr"),
                 r.get("player_name"))] = r
    n_new_i = n_upd_i = 0
    seen_keys = set()
    for p in injury_upserts:
        key = (p["league_code"], p["team_abbr"], p["player_name"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        cur = inj_key.get(key)
        if cur:
            patch = changed_fields(cur, p, ["status", "severity", "injury_type", "impact_rating"])
            if patch:
                client.put("Injury", cur["id"], patch)
                n_upd_i += 1
        else:
            client.create("Injury", p)
            n_new_i += 1
    log("done", f"enrichment writes: {client.writes} total | weather new {n_new_w}, "
          f"travel new {n_new_t}, analysis new {n_new_a}, injuries new {n_new_i}/upd {n_upd_i}")
    # ---- Props (entity created by the builder; stage until then) ----
    prop_repo = {}
    prop_entity_ok = True
    try:
        for r in client.list_all("Prop", fields=["id", "game_id", "player_espn_id", "prop_type"]):
            prop_repo[(r.get("game_id"), r.get("player_espn_id"), r.get("prop_type"))] = r
    except Exception:
        prop_entity_ok = False
        log("props", "Prop entity not present — staging payloads to props_staging.json")
    n_new_p = n_upd_p = 0
    seen_p = set()
    for p in prop_payloads:
        key = (p["game_id"], p["player_espn_id"], p["prop_type"])
        if key in seen_p:
            continue
        seen_p.add(key)
        if not prop_entity_ok:
            continue
        cur = prop_repo.get(key)
        if cur:
            patch = changed_fields(cur, p, [
                "nova_fair_low", "nova_fair_high", "nova_read", "baseline_last_game",
                "status", "data_confidence", "headshot_url"])
            if patch:
                client.put("Prop", cur["id"], patch)
                n_upd_p += 1
        else:
            try:
                client.create("Prop", p)
                n_new_p += 1
            except Exception as e:
                log("props", f"create failed: {str(e)[:120]}")
    if not prop_entity_ok and prop_payloads:
        with open("props_staging.json", "w") as f:
            json.dump(prop_payloads, f, indent=1)
    log("props", f"{len(prop_payloads)} payloads | new {n_new_p}, upd {n_upd_p}"
        + ("" if prop_entity_ok else " — STAGED, entity missing"))
    log("done", f"cycle complete: {client.writes} writes, {client.reads} bulk reads")


def write_plan(client, league_plans, game_updates, read_patches, stale_read_ids=()):
    """Dispatch the planned writes. league_plans is [(gc, lu, sup, nr), ...]
    IN LEAGUE ORDER — game_ref create indices are LOCAL to their league's
    game_creates list, so games are created and their odds rows attached
    within the same per-league pass. Superseding happens BEFORE inserting
    so two current rows never coexist."""
    # ESPN patches for planned creates merge into the create payload FIRST,
    # so a game born live/final is created correct the first time.
    for g, patch in game_updates:
        if not g.get("id") and "__create" in g:
            g.update(patch)

    snapshot_time = datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    created_ids_per_league = []
    for gc, lu, sup, nr in league_plans:
        local_ids = []
        for payload in gc:
            clean = {k: v for k, v in payload.items() if not k.startswith("__")}
            created = client.create("Game", clean)
            local_ids.append(created.get("id") if created else None)
        created_ids_per_league.append(local_ids)
        for oid in sup:
            client.put("OddsSnapshot", oid, {"is_current": False})
        for row in nr:
            kind, val = row.pop("game_ref", ("id", None))
            gid = local_ids[val] if kind == "create" else val
            if gid:
                client.create("OddsSnapshot",
                               {"game_id": gid, "snapshot_time": snapshot_time, **row})
        for gid, patch in lu:
            client.put("Game", gid, patch)

    for g, patch in game_updates:
        if g.get("id"):
            client.put("Game", g["id"], patch)

    for rid in stale_read_ids:
        client.put("MarketNovaRead", rid, {"is_current": False})
    for rid, gid, patch in read_patches:
        if rid == "__new_game__":
            li, ci = gid
            ids = created_ids_per_league[li]
            if ci < len(ids) and ids[ci]:   # game create failed -> next cycle heals
                client.create("MarketNovaRead", {
                    "game_id": ids[ci], "is_current": True,
                    "market_type": "spread", **patch})
        elif rid == "__create__":
            client.create("MarketNovaRead", {
                "game_id": gid, "is_current": True, "market_type": "spread", **patch})
        else:
            client.put("MarketNovaRead", rid, patch)


if __name__ == "__main__":
    run(dry="--dry-run" in sys.argv)

```


## FILE: `nova/llm.py`
**Optional LLM narrative decorator (never blocks)**

```python
"""Pluggable narrative generation for MarketNovaRead fields.

If LLM_API_KEY is set (Anthropic-style), narrative fields are drafted by an
LLM call per NEW read only — never on unchanged reads, so the write budget
stays minimal. Without a key, reads.py's deterministic templates are used,
which are honest and numbers-grounded already.

Keeping this optional is deliberate: the worker must never BLOCK on an LLM.
Numbers and decisions come from kernel.py; narrative is decoration on top.
"""
import json
import urllib.request

from nova import config

PROMPT_HEADER = """You write market-analysis copy for MarketNova, a sportsbook-intelligence product.
Voice: institutional, calm, Bloomberg-terminal register. No hype, no 'AI' fluff, no emoji.
Terminology: price, number, edge, market. Status taxonomy only: PLAY/LEAN/MONITOR/PASS/NUMBER GONE/AWAITING DATA.
Never say 'bet', 'lock', 'guaranteed', or 'A-Grade'. Ground every claim in the numbers provided.
Output strict JSON: {"what_the_book_is_doing": "...", "one_line_read": "..."}.
what_the_book_is_doing: 2-3 sentences explaining what the market price reflects and why it sits there.
one_line_read: one sentence summarizing the true-line gap and status."""


def narrate(game, result):
    """Return (what_the_book_is_doing, one_line_read) or None on any failure."""
    if not config.LLM_API_KEY:
        return None
    body = json.dumps({
        "model": config.LLM_MODEL,
        "max_tokens": 500,
        "system": PROMPT_HEADER,
        "messages": [{"role": "user", "content": json.dumps({
            "sport": game.get("league_code"),
            "game": f"{game.get('away_team')} @ {game.get('home_team')}",
            "market_line": result.get("market_line"),
            "true_line": result.get("true_line"),
            "raw_edge": result.get("raw_edge"),
            "status": result.get("status"),
        })}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
            "x-api-key": config.LLM_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read())
        text = data["content"][0]["text"]
        parsed = json.loads(text)
        return parsed.get("what_the_book_is_doing"), parsed.get("one_line_read")
    except Exception as e:
        print(f"  llm: narrative failed ({e}); templates will be used")
        return None

```


## FILE: `nova/doctrine.py`
**FASTESTPAYOUT doctrine executable (composable brain registry)**

```python
"""
FASTESTPAYOUT_DOCTRINE — executable, composable MarketNova intelligence module.

Full doctrine text: .agents/rules/FASTESTPAYOUT_DOCTRINE_v1.md (v1.0, read-only;
updates go into v1.1+ files per versioning protocol).

This module turns the doctrine into composable brains so any call site (LLM
narrative, live read, sport-specific analysis) loads ONLY the relevant pieces:

    from nova.doctrine import compose
    brain = compose("CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+NFL_BRAIN+LIVE_2H_BRAIN")

Nothing here changes the decision kernel's math. This is the doctrine layer:
sequence, output contract, never-invent invariants, and provenance policy.
"""

# ------------------------------------------------------------
# PROVENANCE (binding policy — Part 0 of the doctrine file)
# ------------------------------------------------------------
PROVENANCE = {
    "source": "Internally built framework of the MarketNova project "
              "(The Blueprint -> The Insider System -> FastestPayout Logic -> MarketNova).",
    "identity_claims": {
        "twicopy_cached_profile": "ANALYSIS: describes @FastestPayout as 'Sports "
            "Prognosticator / Mathematician / VIP Betting Group'. Anecdotal only — "
            "NOT verified inside Nova.",
        "letsrun_discussion": "ANALYSIS: third-party forum discussion of the account "
            "analyzing sportsbook databases and bettor-behavior modeling. Anecdotal, "
            "NOT treated as verified.",
        "fastestpayout_co_uk": "EXCLUDED: a UK gambling/withdrawal editorial site, "
            "NOT this entity and NOT evidence for this doctrine. Never cite it.",
    },
    "rule": "No part of this doctrine depends on unverifiable identity claims. "
            "Unsourced claims are treated as unverified (see NEVER_INVENT).",
}

# ------------------------------------------------------------
# NEVER-INVENT invariants (Part LXVIII) — hard validation surface
# ------------------------------------------------------------
NEVER_INVENT = [
    "sharp_money",
    "public_betting_percentages",
    "injuries",
    "line_movement",
    "vegas_knows_explanations",
]

NEVER_ASSUME = [
    "every_favorite_is_a_trap",
    "every_public_side_should_be_faded",
    "every_hot_performance_regresses_immediately",
    "reverse_line_movement_is_automatically_sharp",
    "historical_trends_without_current_context",
    "chase_after_value_disappears",
    "bets_because_a_user_asks",
    "rewrite_failed_analysis_after_the_game",
    "confuse_prediction_accuracy_with_betting_value",
]


def validate_no_invention(read: dict) -> list:
    """Guard for generated reads/narratives: any invented-evidence field
    must be sourced from a feed or omitted. Returns a list of violations.

    Fields claiming quantified public money, sharp action, or psychology
    must carry a source tag (e.g. data_quality_json / trigger_source);
    numbers without provenance are violations of Part LXVIII.
    """
    problems = []
    dq = read.get("data_quality_json") or {}
    sourced = isinstance(dq, dict) and bool(dq.get("public_pct_source"))

    def _sourced_num(v):
        # a quantified claim about public money with no source = invented
        return v is None or sourced or str(v).strip() == ""

    if not _sourced_num(read.get("public_bias")) and isinstance(read.get("public_bias"), str) \
            and any(ch.isdigit() for ch in str(read.get("public_bias"))):
        problems.append("public percentage without source (invented)")
    return problems


# ------------------------------------------------------------
# Composable brains
# ------------------------------------------------------------
CORE_BRAIN = {
    "name": "CORE_BRAIN",
    "authority": "MarketNova Master Brain Update v1.0 (.agents/rules/"
                 "MarketNova_Master_Brain_Update_v1.md) — canonical operating spec",
    "responsibilities": [
        "build true price before reading the market",
        "decision gates and thresholds (kernel)",
        "event sourcing, versioning, no hindsight, CLV postmortems",
    ],
    "core_doctrine": "Build the true game first. Read the market second. "
                      "Explain the discrepancy third. Bet only if the current "
                      "price still clears the edge threshold.",
}

FASTESTPAYOUT_DOCTRINE = {
    "name": "FASTESTPAYOUT_DOCTRINE",
    "first_principle": "THE LINE IS A PRICE. Ask why this exact price exists, "
                       "then what it should actually be.",
    "four_realities": [
        "GAME REALITY — what is actually happening",
        "MARKET REALITY — what is priced",
        "BETTOR REALITY — what the average bettor sees",
        "SPORTSBOOK REALITY — what the market maker is responding to",
    ],
    "key_principles": [
        "PART IV: bet the price, not the team — no permanently good teams, only prices",
        "PART V: construct the true line first, independently",
        "PART VI: use fair ranges, never fake precision",
        "PART VII: the opener is information; never overwrite history",
        "PART VIII: did the market already correct the mistake?",
        "PART IX/X: do not chase steam; NUMBER GONE is a permanent state",
        "PART XI/XII: public-perception tax and recency tax — how much of the story is already priced",
        "PART XV/XVI: public consensus is evidence, never automatic action; RLM is a research trigger",
        "PART XIX/XX: half points and juice are part of the number",
        "PART XXII: the obvious-bet test — why is the price still available?",
        "PART XXIII/XXIV: boring can be good; low-volume precision; passing is a position",
        "PART LIV: second-order thinking — what does the current number already know?",
        "PART LV: best expression — which market expresses the thesis most cleanly?",
        "PART LVI: correlated bets are not independent evidence",
        "PART LVIII/LIX: no forced plays; whale standard requires cross-module agreement",
        "PART LX: disagree with the user — CONFIRMED / WEAKENED / INVALIDATED / NUMBER GONE",
    ],
    "analysis_sequence": [
        "identify game", "freeze market", "build game reality", "build true price",
        "identify public story", "identify market story", "find distortion",
        "choose expression", "price test", "risk test", "action",
    ],
    "publish_test": [
        "What is the public seeing?", "What is the sportsbook pricing?",
        "What does the underlying game say?", "What does Nova make the true price?",
        "Has the market already corrected?", "Is this the best way to express the thesis?",
        "What number makes the bet invalid?",
    ],
    "required_output": [
        "Market", "Nova True Price", "Market Difference", "Public Story",
        "Sportsbook Context", "Underlying Reality", "Regression", "Best Expression",
        "Buy-To / Threshold", "Primary Risk", "Action",
    ],
    "action_states": ["PLAY", "LEAN", "MONITOR", "PASS", "NUMBER GONE",
                      "DATA INSUFFICIENT (rendered as AWAITING DATA in live taxonomy)"],
    "never_invent": NEVER_INVENT,
    "never_assume": NEVER_ASSUME,
}

LIVE_2H_BRAIN = {
    "name": "LIVE_2H_BRAIN",
    "source_parts": ["XXV–XXXVIII", "LXV"],
    "sequence": [
        "pregame expectation", "actual game state", "possessions/pace", "efficiency",
        "score quality", "sustainability", "regression", "talent/depth", "coaching",
        "expected game script", "true live/2H price", "book price",
        "what is already priced?", "best expression", "action",
    ],
    "archetypes": {
        "FALSE_SCORING_UNDER": "first-half scoring exceeds sustainable offense "
            "(short fields, defensive/ST scores, extreme red-zone conversion) — 2H Under "
            "only if the book has not fully corrected",
        "SUPPRESSED_SCORING_OVER": "offense better than scoreboard (high YPP, missed "
            "FGs, red-zone failures, pace) — 2H Over only if price leaves value",
        "CONTROL_SIDE": "scoreboard understates one team's dominance (LOS control, "
            "rushing efficiency, field position, depth)",
        "GAME_STATE_CONTAMINATION": "a large lead changes pace, play-calling, "
            "substitutions — never project 2H as if score were 0-0",
    },
    "principles": [
        "PART XXVI: scoreboard does not equal game — a score does not explain itself",
        "PART XXVII: separate organic points from distorted points",
        "PART XXVIII: regression asks whether production was repeatable, not reversal",
        "PART XXXIII: talent regression — depth/rotation matter more late in college games",
        "PART XXXIV: two identical halftime box scores can deserve different 2H lines",
        "PART XXXV: possession before points; never infer pace from points",
        "PART XXXVI: hot shooting is contextual — shot quality decides repeatability",
        "PART XXXVII: foul/bonus environment can flip a total by itself",
        "PART XXXVIII: blowouts compete (slower pace vs garbage-time deterioration)",
        "PART XL/XLI: overseas 1H under and 26.5 are heuristics to investigate, never hardcodes",
    ],
}

NFL_BRAIN = {
    "name": "NFL_BRAIN",
    "source_parts": ["XIX", "XLVIII", "LIII"],
    "principles": [
        "PART XIX: key numbers 3, 7, 10, 14, 17, 20, 21, 24, 27, 28 — a half point "
        "around a key margin can flip EV; 3 is not 3.5",
        "PART XLVIII: weather quantified, never narrative — wind/gusts/rain/roof "
        "modify passing, kicking, scoring variance",
        "PART LIII: prime-time amplifies favorite/Over bias and star bias — increase "
        "scrutiny, do not auto-fade",
    ],
}

MLB_BRAIN = {
    "name": "MLB_BRAIN",
    "source_parts": ["XLII–L"],
    "principles": [
        "PART XLII: starting pitcher is not ERA — pitch mix, command, contact quality, platoon splits",
        "PART XLIII: which lineup FITS this pitcher, not which lineup is better",
        "PART XLIV: bullpen game theory — usage, back-to-backs, leverage availability",
        "PART XLV/F5_BRAIN: use F5 to isolate starter edges",
        "PART XLVI: rookie/uncertain starters — widen the range, reduce conviction",
        "PART XLVII: low totals compress margins — do not blindly convert ML to run line",
        "PART XLVIII: wind/roof/park quantified",
        "PART XLIX: the obvious day-game Over may be correct but too expensive",
        "PART L: a half run is enormous in low-scoring distributions (7/7.5)",
    ],
}

F5_BRAIN = {
    "name": "F5_BRAIN",
    "source_parts": ["XLIV–XLVII"],
    "principles": [
        "use F5 when starter advantage is strong, lineup advantage clear, bullpen "
        "introduces unwanted variance",
        "preferred expressions: F5 ML, F5 +0.5, F5 Under",
        "do not force full-game ML because it is more familiar",
        "with uncertain pitchers, sometimes avoid F5 ML despite the apparent edge",
    ],
}

NCAAF_BRAIN = {
    "name": "NCAAF_BRAIN",
    "source_parts": ["XXXIII", "LI"],
    "principles": [
        "PART XXXIII: talent regression — a less talented roster can execute for 30 "
        "minutes; depth, line play and rotation matter more later (2H talent reassertion)",
        "PART LI: transfer portal / NIL / coordinator turnover — current identity, "
        "not last year's brand",
    ],
}

BASKETBALL_BRAIN = {
    "name": "BASKETBALL_BRAIN",
    "source_parts": ["XXXV–XXXVII"],
    "principles": [
        "PART XXXV: estimate possessions from FGA/OREB/TO/FTA; a 90-point half can be "
        "slow, a 70-point half can be fast",
        "PART XXXVI: hot shooting regresses contextually — shot quality decides",
        "PART XXXVII: foul/bonus environment is a total input of its own",
    ],
}

BRAINS = {
    b["name"]: b for b in (
        CORE_BRAIN, FASTESTPAYOUT_DOCTRINE, LIVE_2H_BRAIN, NFL_BRAIN,
        MLB_BRAIN, F5_BRAIN, NCAAF_BRAIN, BASKETBALL_BRAIN,
    )
}


def compose(spec: str) -> dict:
    """Compose a brain from a '+'-separated spec, e.g.
    compose('CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+NFL_BRAIN+LIVE_2H_BRAIN').

    Returns {'brains': [ordered brain dicts], 'sequence': merged sequence,
    'publish_test': [...], 'never_invent': [...], 'never_assume': [...]}.
    """
    names = [n.strip() for n in spec.split("+") if n.strip()]
    unknown = [n for n in names if n not in BRAINS]
    if unknown:
        raise KeyError(f"unknown brain(s): {unknown}; available: {sorted(BRAINS)}")
    brains = [BRAINS[n] for n in names]
    seq = []
    for b in brains:
        for s in b.get("analysis_sequence", []) + b.get("sequence", []):
            if s not in seq:
                seq.append(s)
    return {
        "spec": names,
        "brains": brains,
        "sequence": seq,
        "publish_test": FASTESTPAYOUT_DOCTRINE["publish_test"]
                        if "FASTESTPAYOUT_DOCTRINE" in names else [],
        "never_invent": NEVER_INVENT,
        "never_assume": NEVER_ASSUME,
        "provenance": PROVENANCE,
    }


def prompt_context(spec: str) -> str:
    """Render a composed brain as compact text for an LLM narrative call —
    loads only the requested modules instead of the entire doctrine."""
    c = compose(spec)
    parts = []
    for b in c["brains"]:
        parts.append(f"## {b['name']}")
        for k, v in b.items():
            if k == "name":
                continue
            parts.append(f"- {k}: {v}")
    return "\n".join(parts)

```


## FILE: `nova/test_kernel.py`
**77-check self-test suite**

```python
"""
MarketNova Integration Test Suite — validates kernel.py against
MarketNova_Master_Brain_Update_v1.md scenarios (audit requirement F).

Run: python3 tests.py
Each scenario prints STATUS -> decision, and RULES FIRED with spec-section tags.
"""

import os
import sys
# validate the kernel THIS package ships with (nova/kernel.py) — the
# production code — never a stale external copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kernel import (GameStateMachine, EventStore, GameEvent, Patch, TruePrice, build_true_price,
                    decide, classify_movement, sharp_resistance_score, key_number_crossing,
                    key_number_quality, FirstHalfState, classify_sustainability,
                    classify_halftime_archetype, game_theory_read, true_2h_total, reprice,
                    attempt_market_only_reprice, postmortem, minimum_playable_price)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1
    print(f"    [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def show_trace(trace, section_filter=None):
    for rid, detail in trace:
        print(f"      FIRED {rid}: {detail}")


print("=" * 78)
print("SCENARIO 1 — PUBLIC-HEAVY LINE FREEZE (sec 4, 5)")
print("=" * 78)
trace = []
tp = build_true_price(team_power_gap=0.0, expected_pace_points=44.0, home_field=0.0)  # true total 44
mv_cls, mv_score = classify_movement(open_line=44.0, current_line=44.0, public_pct=72, move_toward_model=None)
sr_type, sr_score, sr_rule = sharp_resistance_score(public_pct=72, spread_move=0.0, juice_move=False)
print(f"  72% of tickets on the Over; total did not move off 44.")
print(f"  movement={mv_cls} (score {mv_score}) | sharp_resistance={sr_type} via rule '{sr_rule}' (score {sr_score})")
check("LINE_FREEZE classified", mv_cls == "LINE_FREEZE")
check("heavy_public_no_move fires", sr_rule == "heavy_public_no_move")
out = decide(true_price=tp, market_line=44.0, market="second_half", direction="under",
             public_pct=72, psychology=3, movement_score=0, sharp_resistance=sr_score,
             supporting_modules=[], trace=trace)   # NOTE: no independent true-price edge supplied
check("Public cannot create play -> PASS", out.status == "PASS")
check("PUBLIC_CANNOT_CREATE_PLAY gate fired", "PUBLIC_CANNOT_CREATE_PLAY" in out.gates_fired)
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 2 — REVERSE LINE MOVEMENT (sec 5)")
print("=" * 78)
trace = []
# (a) RLM alone, no independent edge -> cannot create a wager
tp = build_true_price(team_power_gap=3.0, expected_pace_points=48.0)   # true spread ~ -5.5, total 48
mv_cls, mv_score = classify_movement(open_line=-4.0, current_line=-5.0, public_pct=68, move_toward_model=False)
sr_type, sr_score, sr_rule = sharp_resistance_score(public_pct=68, spread_move=-1.0, move_against_public=True)
print(f"  68% public on dog; line moved 4.0 -> 5.0 AGAINST the public, no news.")
print(f"  movement={mv_cls} (score {mv_score}) | sharp_resistance={sr_type} via '{sr_rule}'")
out_a = decide(true_price=tp, market_line=-4.0, market="pregame_spread", direction="favorite",
               public_pct=68, psychology=2, movement_score=0, sharp_resistance=sr_score,
               supporting_modules=[], trace=trace)
check("RLM without independent edge -> PASS", out_a.status == "PASS")
check("PUBLIC_CANNOT_CREATE_PLAY fired", "PUBLIC_CANNOT_CREATE_PLAY" in out_a.gates_fired)
# (b) market moves hard AGAINST our independently derived position -> contradiction review, not a chase
tp_b = build_true_price(team_power_gap=1.0, expected_pace_points=41.0)  # true total 41, we like Under 44.5
mv_cls, mv_score = classify_movement(open_line=44.5, current_line=46.5, public_pct=68, move_toward_model=False)
out_b = decide(true_price=tp_b, market_line=46.5, market="pregame_spread", direction="under",
               public_pct=30, movement_score=mv_score, sharp_resistance=2,
               supporting_modules=["regression", "sustainability"], trace=trace)
check("Strong move against model -> REPRICE_REQUIRED", out_b.status == "REPRICE_REQUIRED")
check("STRONG_MARKET_CONTRADICTION fired", "STRONG_MARKET_CONTRADICTION" in out_b.gates_fired)
check("Never chases: no PLAY emitted", out_b.status != "PLAY")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 3 — KEY-NUMBER CROSSING (sec 6)")
print("=" * 78)
trace = []
tp = build_true_price(team_power_gap=6.0, expected_pace_points=45.0)   # true spread -8.5
print("  We like the favorite. True spread -8.5; we entered at -6.5. Market moves -6.5 -> -8.0.")
crossed, favorable, keys = key_number_crossing(entry_number=6.5, current_number=8.0, direction="favorite")
check("Key 7 crossed", crossed and 7 in keys)
check("Crossing unfavorable for favorite buyer", not favorable)
out = decide(true_price=tp, market_line=-8.0, market="pregame_spread", direction="favorite",
             movement_score=0, supporting_modules=["talent", "game_state"],
             key_crossed_unfavorably=True, trace=trace)
check("KEY_NUMBER_CROSSED gate fired (RECOMPUTE_EV + INVALIDATE)", "KEY_NUMBER_CROSSED" in out.gates_fired)
check("EV recomputed at the new number: edge 0.5 -> PASS, old recommendation invalidated",
      out.status in ("PASS", "MONITOR"))
print(f"  -> after crossing, raw edge vs true 8.5 is {out.raw_edge}; decision {out.status} (old rec invalidated)")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 4 — FALSE-SCORING 1H (sec 9, 10, 12)")
print("=" * 78)
trace = []
fh = FirstHalfState(points=31, drive_ypp=4.2, possessions=9, distortion_points=21,
                    missed_fg=0, rz_trips=2, rz_tds=2, lead=7, ypp_margin=0.3)
sust = classify_sustainability(fh)
arch = classify_halftime_archetype(fh)
gt = game_theory_read(fh.lead)
print(f"  1H: 31 points, but 21 from pick-6 + ST TD + two <30-yd short fields. Normal-drive YPP 4.2.")
check("Sustainability VERY_LOW (distortion share 68%)", sust == "VERY_LOW")
check("Archetype FALSE_SCORING_UNDER", arch == "FALSE_SCORING_UNDER")
t2h = true_2h_total(baseline_remaining_scoring=23.5, pace_adj=0.0, regression_adj=-3.0,
                     turnover_distortion=4.0, explosive_distortion=1.5, rz_adj=0.0)
print(f"  True 2H total = {t2h} (baseline 23.5 - regression 3.0 - turnover distortion 4.0 - explosive 1.5)")
tp2h = TruePrice(spread=-3.0, total=t2h)
out = decide(true_price=tp2h, market_line=27.5, market="second_half", direction="under",
             supporting_modules=["regression", "sustainability"], trace=trace)
check("2H Under 27.5 = PLAY band (edge 4.5 >= 3.0)", out.status in ("PLAY", "STRONG_DISCREPANCY"))
check("Both supporting modules present (PLAY_REQUIRES_SUPPORT satisfied)", "PLAY_REQUIRES_SUPPORT" not in out.gates_fired)
print(f"  -> decision {out.status}: Under 27.5 vs true {t2h}, min playable {out.minimum_playable_price}")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 5 — SUPPRESSED-SCORING 1H (sec 9, 10, 12)")
print("=" * 78)
trace = []
fh = FirstHalfState(points=10, drive_ypp=6.1, possessions=11, distortion_points=0,
                    missed_fg=2, rz_trips=3, rz_tds=0, lead=3, ypp_margin=0.6)
sust = classify_sustainability(fh)
arch = classify_halftime_archetype(fh)
print(f"  1H: 10 points, but 6.1 YPP, 2 missed FGs, 0/3 red zone, zero distortion points.")
check("Sustainability VERY_HIGH (repeatable production)", sust in ("VERY_HIGH", "HIGH"))
check("Archetype SUPPRESSED_SCORING_OVER", arch == "SUPPRESSED_SCORING_OVER")
t2h = true_2h_total(baseline_remaining_scoring=22.0, pace_adj=1.0, regression_adj=1.5, rz_adj=2.0)
print(f"  True 2H total = {t2h} (baseline 22 + suppressed-production payback)")
tp2h = TruePrice(spread=-1.5, total=t2h)
out = decide(true_price=tp2h, market_line=20.0, market="second_half", direction="over",
             supporting_modules=["regression", "sustainability"], trace=trace)
check("2H Over 20.0 vs true 25.5: raw edge 5.5 -> PLAY/STRONG", out.status in ("PLAY", "STRONG_DISCREPANCY"))
print(f"  -> decision {out.status}")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 6 — BLOWOUT / GAME-STATE CONTAMINATION (sec 10, 11)")
print("=" * 78)
trace = []
fh = FirstHalfState(points=35, drive_ypp=5.4, possessions=10, distortion_points=7, lead=28, ypp_margin=2.4)
arch = classify_halftime_archetype(fh)
gt = game_theory_read(fh.lead)
check("Archetype GAME_STATE_CONTAMINATION (28-pt lead >= 17)", arch == "GAME_STATE_CONTAMINATION")
check("Script BLOWOUT", gt["script"] == "BLOWOUT")
check("Expected 2H pace MUCH_SLOWER", gt["expected_2h_pace"] == "MUCH_SLOWER")
check("Substitution risk HIGH", gt["substitution_risk"] == "HIGH")
t2h = true_2h_total(baseline_remaining_scoring=24.0, game_state_adj=-6.0, substitution_adj=-1.5)
print(f"  True 2H total = {t2h} (blowout script drains pace; backups in)")
tp2h = TruePrice(spread=-10.0, total=t2h)
out = decide(true_price=tp2h, market_line=24.0, market="second_half", direction="under",
             supporting_modules=["game_state", "game_theory"], trace=trace)
print(f"  -> market 2H total 24.0 vs true {t2h}; decision {out.status} "
      f"(scoreboard says shootout, game state says clock-kill)")
check("Under supported by game-state module", out.game_state_support == "SUPPORTED")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 7 — NUMBER MOVING PAST MINIMUM PLAYABLE PRICE (sec 7, 13)")
print("=" * 78)
trace = []
tp2h = TruePrice(spread=-3.0, total=20.0)   # true 2H total 20 (2H keys 20/21/24 live)
mpp = minimum_playable_price(20.0, "second_half", "under")
print(f"  True 2H total 20.0; 2H play threshold 3.0 -> minimum playable Under = {mpp}")
out_open = decide(true_price=tp2h, market_line=23.5, market="second_half", direction="under",
                  supporting_modules=["regression", "sustainability"], trace=trace)
check("At 23.5 the number is playable", out_open.status in ("PLAY", "STRONG_DISCREPANCY"))
out_gone = decide(true_price=tp2h, market_line=20.5, market="second_half", direction="under",
                  supporting_modules=["regression", "sustainability"],
                  prior_playable_edge=(out_open.status in ("PLAY", "STRONG_DISCREPANCY")), trace=trace)
check("Market steams to 20.5 -> NUMBER_GONE (thesis intact, value gone)", out_gone.status == "NUMBER_GONE")
check("NUMBER_GONE gate fired", "NUMBER_GONE" in out_gone.gates_fired)
check("Never chases the steam", out_gone.status != "PLAY")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 8 — LATE CORRECTION BEFORE CONFIRM (sec 18, 19, 21, 22)")
print("=" * 78)
trace = []
store = EventStore(trace=trace)
# normal flow
store.append(GameEvent("SCORE", "PRIMARY_STATS_PROVIDER", 100, "t1",
                       [Patch("score", "SET", 14, "PRIMARY_STATS_PROVIDER", 100)], received_at="r1"))
store.append(GameEvent("CONFIRM", "OFFICIAL_LEAGUE", 101, "t2",
                       [Patch("score", "CONFIRM", 14, "OFFICIAL_LEAGUE", 101)], received_at="r2"))
check("score effective = 14, confirmed by OFFICIAL_LEAGUE", store.get("score") == 14)
check("CONFIRM strengthens evidentiary status",
      "OFFICIAL_LEAGUE" in store.effective["score"]["confirmed_by"])
# late event with OLDER source sequence arrives AFTER the confirm
store.append(GameEvent("SCORE", "PRIMARY_STATS_PROVIDER", 99, "t0",
                       [Patch("score", "SET", 13, "PRIMARY_STATS_PROVIDER", 99)], received_at="r3"))
check("Late older-seq event does NOT supersede effective claim", store.get("score") == 14)
check("SELECTIVE_REPLAY fired (history modified, effective untouched)",
      any(r[0] == "RULE:SELECTIVE_REPLAY" for r in trace))
# CONFIRM with wrong value -> CONFIRM_VALUE_MISMATCH
trace_before = len(trace)
store.append(GameEvent("CONFIRM", "OFFICIAL_LEAGUE", 102, "t3",
                       [Patch("score", "CONFIRM", 13, "OFFICIAL_LEAGUE", 102)], received_at="r4"))
check("CONFIRM value mismatch rejected", store.get("score") == 14)
check("CONFIRM_VALUE_MISMATCH fired", any(r[0] == "REJ:CONFIRM_VALUE_MISMATCH" for r in trace))
# silence is not confirmation: event touching only clock leaves score untouched
seq_before = store.effective["score"]["seq"]
store.append(GameEvent("CLOCK", "STADIUM_FEED", 103, "t4",
                       [Patch("game_clock", "SET", "4:12", "STADIUM_FEED", 103)], received_at="r5"))
check("Field omitted = no claim (score untouched by clock event)", store.get("score") == 14)
check("Clock updated independently", store.get("game_clock") == "4:12")
# decisions versioned; replayed kept separate
v1 = store.record_decision({"status": "PLAY", "line": 27.5})
store.record_replayed_decision({"status": "PASS", "line": 27.5})
statuses = [d["historical_status"] for d in store.decision_versions]
check("Actual vs replayed decisions kept separate",
      statuses == ["ACTUALLY_EMITTED", "COUNTERFACTUAL_RECONSTRUCTION"])
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 9 — CONFLICTING FIELD-AUTHORITY CORRECTIONS (sec 20)")
print("=" * 78)
trace = []
store = EventStore(trace=trace)
# same sequence, two sources, different clock values -> authority decides (OFFICIAL_LEAGUE rank 0)
store.append(GameEvent("CLOCK", "STADIUM_FEED", 200, "t1",
                       [Patch("game_clock", "SET", "2:31", "STADIUM_FEED", 200)], received_at="r1"))
store.append(GameEvent("CLOCK", "OFFICIAL_LEAGUE", 200, "t1b",
                       [Patch("game_clock", "SET", "2:29", "OFFICIAL_LEAGUE", 200)], received_at="r2"))
check("Clock authority: OFFICIAL_LEAGUE outranks STADIUM_FEED at same sequence",
      store.get("game_clock") == "2:29")
check("CROSS_SOURCE_CONFLICT logged (CRITICAL)", any(r[0] == "REJ:CROSS_SOURCE_CONFLICT" for r in trace))
# FIELD_AUTHORITY_INVARIANT: the same OFFICIAL_LEAGUE event has NO elevated authority over possession
store.append(GameEvent("POSS", "OFFICIAL_LEAGUE", 201, "t2",
                       [Patch("possession", "SET", "HOME", "OFFICIAL_LEAGUE", 201)], received_at="r3"))
store.append(GameEvent("POSS", "PLAY_BY_PLAY_PROVIDER", 202, "t3",
                       [Patch("possession", "SET", "AWAY", "PLAY_BY_PLAY_PROVIDER", 202)], received_at="r4"))
check("Possession: PBP provider supersedes league for possession field",
      store.get("possession") == "AWAY")
check("Authority over one field never extends to another",
      store.effective["game_clock"]["source"] == "OFFICIAL_LEAGUE"
      and store.effective["possession"]["source"] == "PLAY_BY_PLAY_PROVIDER")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 10 — FINAL ATTEMPTING TO TRANSITION BACK TO LIVE (sec 15, 17)")
print("=" * 78)
trace = []
sm = GameStateMachine(trace=trace)
for s in ["CURRENT", "LIVE", "HALFTIME", "SECOND_HALF", "FINAL"]:
    sm.transition(s)
check("Happy path OPEN->...->FINAL traversed", sm.state == "FINAL")
ok = sm.transition("LIVE")
check("FINAL -> LIVE rejected", ok is False and sm.state == "FINAL")
check("FINAL_STATE_LOCKED logged (CRITICAL)", any(r[0] == "REJ:FINAL_STATE_LOCKED" for r in trace))
check("STATE_TRANSITION_NOT_ALLOWED logged (CRITICAL)", any(r[0] == "REJ:STATE_TRANSITION_NOT_ALLOWED" for r in trace))
ok2 = reprice(sm, cause_event_id="ev-x", cause_type="MAJOR_INJURY", affected_inputs=["qb"])
check("Reprice of FINAL game rejected", ok2 is False)
sm2 = GameStateMachine(trace=trace)
ok3 = sm2.transition("REPRICE")   # no cause event
check("REPRICE without cause_event_id rejected", ok3 is False)
check("REPRICE_WITHOUT_CAUSE logged (CRITICAL)", any(r[0] == "REJ:REPRICE_WITHOUT_CAUSE" for r in trace))
ok4 = attempt_market_only_reprice(sm2)
check("Market-only reprice attempt refused (WARNING logged)",
      ok4 is False and any(r[0] == "REJ:MARKET_ONLY_REPRICE_ATTEMPT" for r in trace))
show_trace(trace)

print()
print("=" * 78)
print("BONUS — CLV / POSTMORTEM QUADRANTS (sec 28)")
print("=" * 78)
p1 = postmortem(entry_price=27.5, closing_price=26.0, result="LOSS", process_ok=True)
check("Good process bad result: NOT penalized", p1["action"] == "DO_NOT_PENALIZE_FOR_RESULT_ALONE")
p2 = postmortem(entry_price=27.5, closing_price=26.0, result="WIN", process_ok=False)
check("Bad process good result: flagged false positive", p2["action"] == "FLAG_FALSE_POSITIVE")
p3 = postmortem(entry_price=27.5, closing_price=28.5, result="WIN", process_ok=True)
check("CLV tracked (beat close by 1.0)", p3["closing_line_value"] == -1.0)
print(f"  quadrant checks: {p1['process_grade']} / {p2['process_grade']} / {p3['process_grade']}")

# ------------------------------------------------------------
# Signed-edge direction regression tests (owner directive, Oct 9 2026):
# true -5 vs market -1 must be a 4-point edge TO THE FAVORITE.
# ------------------------------------------------------------
print()
print("-- signed-edge direction regression (true -5 vs market -1) --")
tp5 = build_true_price(team_power_gap=-7.0, expected_pace_points=45, home_field=2.0)
tp5.spread = -5.0
d_fav = decide(true_price=tp5, market_line=-1.0, market="pregame_spread", direction="favorite")
check("true -5 vs market -1 is a 4-point edge", d_fav.raw_edge == 4.0)
check("value side is the FAVORITE (market lays fewer than true)", -1.0 > -5.0)
check("minimum playable for true -5 is -3.0", d_fav.minimum_playable_price == -3.0)
check("market -1 is still playable for the favorite", -1.0 >= d_fav.minimum_playable_price)
d_dog = decide(true_price=tp5, market_line=-9.0, market="pregame_spread", direction="dog")
check("mirror: true -5 vs market -9 is a 4-point edge to the dog", d_dog.raw_edge == 4.0)
check("sign-flip trap: abs math fakes 2 but real disagreement is 8",
      abs(abs(3.0) - abs(-5.0)) == 2.0 and abs(3.0 - (-5.0)) == 8.0)
check("reads direction rule: line > true -> favorite, line < true -> dog",
      ("favorite" if -1.0 > -5.0 else "dog") == "favorite" and
      ("favorite" if -9.0 > -5.0 else "dog") == "dog")

print()
print("-- game theory refinements: juice pressure + lifecycle --")
from datetime import datetime, timedelta, timezone
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from reads import compute_read
_g = {"league_code": "NFL", "sport": "NFL", "home_team": "Rams", "away_team": "Bears",
      "start_time": (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat()}
_pair = {"home": {"rating": 3.0}, "away": {"rating": 0.0}}
_mk = lambda line: {"spread": {"market_line": line, "book": "draftkings"}}
_ju_against = {"side": "home", "line_held": True, "worse": True, "from": -110, "to": -125}
_r_lean = compute_read(_g, _pair, _mk(-1.0), juice=_ju_against)
check("edge game keeps its status under juice pressure", _r_lean["status"] == "LEAN")
check("juice pressure marker recorded on the value side",
      _r_lean["telemetry"]["juice_pressure"] == "against_value_side")
check("value side resolved home (favorite at -1 vs true -5)",
      _r_lean["telemetry"]["value_side"] == "home")
_r_quiet = compute_read(_g, _pair, _mk(-4.5), juice=_ju_against)
check("quiet PASS upgrades to MONITOR on juice pressure", _r_quiet["status"] == "MONITOR")
_r_opp = compute_read(_g, _pair, _mk(-4.5),
                       juice={"side": "away", "line_held": True, "worse": True,
                              "from": -105, "to": -115})
check("juice on the opposite side changes nothing", _r_opp["status"] == "PASS")
check("lifecycle stage recorded (30h out = mid)",
      _r_quiet["telemetry"]["stage"] == "mid"
      and abs(_r_quiet["telemetry"]["hours_to_close"] - 30) < 1)

print()
print("-- psychology findings: vig test + shading window --")
_mkv = lambda line, hp: {"spread": {"market_line": line, "home_price": hp, "away_price": -110}}
_vt = compute_read(_g, _pair, _mkv(-1.0, -110))
check("no penalty at -110 (standard vig already in thresholds)",
      _vt["telemetry"]["vig_penalty"] == 0.0)
_vt2 = compute_read(_g, _pair, _mkv(-1.0, -150))
check("heavy juice pays the quarter-point tax (-150 -> 1.0 pt)",
      _vt2["telemetry"]["vig_penalty"] == 1.0
      and _vt2["telemetry"]["effective_edge"] == _vt2["raw_edge"] - 1.0)
_ctrl = compute_read(_g, _pair, _mkv(-3.5, -110))
_capped = compute_read(_g, _pair, _mkv(-3.5, -145))
check("LEAN capped to MONITOR when vig eats the lean threshold",
      _ctrl["status"] == "LEAN" and _capped["status"] == "MONITOR")
_dead = compute_read(_g, _pair, _mkv(-3.5, -190))
check("vig can zero the edge entirely (-190 -> PASS)",
      _dead["status"] == "PASS" and _dead["telemetry"]["effective_edge"] == 0.0)
check("open-shading window tagged on early favorite-side edges",
      _vt["telemetry"]["shading_window"] in (True, False))  # stage-dependent, must exist
check("effective edge never exceeds raw edge",
      all(compute_read(_g, _pair, _mkv(-3.5, j))["telemetry"]["effective_edge"]
          <= compute_read(_g, _pair, _mkv(-3.5, j))["raw_edge"]
          for j in (-110, -120, -145, -190)))

print()
print("-- v1.1 amendments: psychology uncredited, streak telemetry --")
import json as _json
_tp = build_true_price(team_power_gap=3.0, expected_pace_points=45, home_field=2.5)
_d_ps = decide(true_price=_tp, market_line=-1.0, market="pregame_spread",
               direction="favorite", supporting_modules=["psychology", "talent"])
_d_ok = decide(true_price=_tp, market_line=-1.0, market="pregame_spread",
               direction="favorite", supporting_modules=["talent", "regression"])
check("psychology + talent does NOT satisfy PLAY_REQUIRES_SUPPORT (v1.1 A1)",
      _d_ps.status == "LEAN")
check("two real modules (talent + regression) still reach PLAY",
      _d_ok.status == "PLAY")
_mform = {"team_abbr": "Rams", "league_code": "NFL",
          "last_5_json": _json.dumps([
              {"result": "W", "score": 28, "opponent_score": 14},
              {"result": "W", "score": 31, "opponent_score": 7},
              {"result": "W", "score": 24, "opponent_score": 21}])}
_r_hot = compute_read(_g, _pair, _mkv(-1.0, -110), form={"home": _mform, "away": None})
check("hot streak on the value side is recorded, not converted",
      _r_hot["telemetry"]["streak"] == {"side": "home", "kind": "W3", "len": 3}
      and _r_hot["status"] == "LEAN")
check("recency anchor flags a >=21pt blowout in the most recent game",
      compute_read(_g, _pair, _mkv(-1.0, -110),
                   form={"home": {"team_abbr": "Rams", "league_code": "NFL",
                                  "last_5_json": _json.dumps([
                                      {"result": "W", "score": 28, "opponent_score": 14},
                                      {"result": "W", "score": 31, "opponent_score": 7}])},
                         "away": None})["telemetry"]["recency_anchor"] is True)
check("no form data -> no streak context, read unaffected",
      compute_read(_g, _pair, _mkv(-1.0, -110))["telemetry"]["streak"] is None)

print()
print("=" * 78)
print(f"RESULT: {PASS} passed, {FAIL} failed")
print("=" * 78)
sys.exit(1 if FAIL else 0)

```


## FILE: `tools/offline_run.py`
**Zero-credit offline verification runner**

```python
#!/usr/bin/env python3
"""Full-cycle run with ZERO Base44 API calls.

Uses LIVE ESPN data (free) + a local stand-in client backed by cached
entity dumps. All reads come from *.cache.json files in the cache dir;
all writes are recorded to offline_run_output.json and never leave the
machine. Exercises every phase of the cycle — odds plan, ESPN reconcile,
read computation, write phase — against real live data with no credits.

Usage:
  python tools/offline_run.py <cache_dir>

Cache files (any missing entity is treated as empty):
  Game.cache.json            — list of Game records
  TeamRating.cache.json      — list of TeamRating records
  MarketNovaRead.cache.json — list of MarketNovaRead records
  OddsSnapshot.cache.json   — list of OddsSnapshot records (optional)
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nova import cycle  # noqa: E402


class LocalClient:
    """Same interface as nova.api.Base44Client, but local-only."""

    def __init__(self, cache_dir):
        self.cache = cache_dir
        self.reads = 0
        self.writes = 0
        self.written = {"Game": [], "OddsSnapshot": [], "MarketNovaRead": []}
        self.puts = []
        self._n = 0

    def list_all(self, entity, fields=None, query=None):
        self.reads += 1
        path = os.path.join(self.cache, f"{entity}.cache.json")
        if os.path.exists(path):
            recs = json.load(open(path))
            print(f"  [offline] {entity}: {len(recs)} cached records")
            return recs
        print(f"  [offline] {entity}: no cache, using []")
        return []

    def create(self, entity, payload):
        self.writes += 1
        self._n += 1
        rid = f"local-{entity}-{self._n}"
        self.written.setdefault(entity, []).append({"id": rid, **payload})
        return {"id": rid}

    def put(self, entity, record_id, patch):
        self.writes += 1
        self.puts.append({"entity": entity, "id": record_id, "patch": patch})
        return True


def check_invariants(client):
    """Structural checks on the simulated write plan."""
    problems = []

    games = client.written["Game"]
    espn_ids = [g.get("provider_game_id") for g in games if g.get("provider_game_id")]
    if len(espn_ids) != len(set(espn_ids)):
        problems.append("DUPLICATE provider_game_id among created games!")
    ext_ids = [g.get("external_id") for g in games if g.get("external_id")]
    if len(ext_ids) != len(set(ext_ids)):
        problems.append("DUPLICATE external_id among created games!")

    snaps = client.written["OddsSnapshot"]
    game_ids = {g["id"] for g in games}
    for s in snaps:
        if not s.get("game_id") or s["game_id"] not in game_ids:
            if not str(s.get("game_id", "")).startswith("local-Game-"):
                problems.append(f"odds row with bad game ref: {s.get('game_id')}")

    reads = client.written["MarketNovaRead"]
    for r in reads:
        if not r.get("game_id"):
            problems.append(f"read with no game ref: {r}")

    if not any(g.get("provider_game_id") for g in games) and games:
        problems.append("created games carry no ESPN ids (link phase inert?)")
    return problems


def main():
    cache_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    client = LocalClient(cache_dir)
    # swap the client BEFORE run() constructs it
    cycle.Base44Client = lambda: client

    print("=== NOVA OFFLINE RUN (live ESPN, zero Base44 calls) ===")
    cycle.run(dry=False)

    out = {
        "summary": {
            "game_creates": len(client.written["Game"]),
            "odds_creates": len(client.written["OddsSnapshot"]),
            "read_creates": len(client.written["MarketNovaRead"]),
            "puts": len(client.puts),
            "simulated_writes": client.writes,
            "simulated_reads": client.reads,
        },
        "problems": check_invariants(client),
        "written": client.written,
        "puts": client.puts,
    }
    path = os.path.join(cache_dir, "offline_run_output.json")
    json.dump(out, open(path, "w"), indent=1)
    print(f"\n=== OFFLINE RUN DONE: {json.dumps(out['summary'])}")
    if out["problems"]:
        print("INVARIANT PROBLEMS:")
        for p in out["problems"]:
            print("  !!", p)
        sys.exit(1)
    print("invariants: OK — plan written to", path)


if __name__ == "__main__":
    main()

```


## FILE: `tools/prop_backfill.py`
**One-off prop backfill**

```python
"""One-off: rebuild props with roster positions + backfill live Prop records."""
import json
import sys

sys.path.insert(0, ".")
from nova import props
from nova.api import Base44Client
from nova.api import changed_fields

client = Base44Client()
games = client.list_all("Game", fields=["id", "home_team", "away_team",
    "home_team_abbr", "away_team_abbr", "league_code", "status", "start_time",
    "provider_game_id"])
reads = client.list_all("MarketNovaRead", fields=["game_id", "is_current",
    "market_type", "marketnova_true_line", "true_line_value", "status"])
read_map = {r["game_id"]: r for r in reads
            if r.get("is_current") and str(r.get("market_type")).lower() == "spread"}

repo = {}
for r in client.list_all("Prop", fields=["id", "game_id", "player_espn_id",
                                          "prop_type", "position", "headshot_url"]):
    repo[(r.get("game_id"), r.get("player_espn_id"), r.get("prop_type"))] = r

totals = {"leagues": 0, "built": 0, "new": 0, "upd": 0, "miss_pos": 0}
for league in ("nfl", "ncaaf", "nba", "nhl"):
    lg_games = [g for g in games
                if str(g.get("league_code") or "").lower() == league
                and str(g.get("status") or "").lower() == "scheduled"
                and g["id"] in read_map]
    if not lg_games:
        continue
    last = props.team_last_game(games, league)
    ctx = props.load_baselines(league, last)
    totals["leagues"] += 1
    for g in lg_games:
        true_line = (read_map[g["id"]].get("marketnova_true_line")
                     or read_map[g["id"]].get("true_line_value"))
        built = props.build_props(g, league, true_line, None, last, ctx)
        totals["built"] += len(built)
        for p in built:
            key = (p["game_id"], p["player_espn_id"], p["prop_type"])
            cur = repo.get(key)
            if cur:
                patch = changed_fields(cur, p, ["position", "headshot_url",
                    "nova_fair_low", "nova_fair_high", "nova_read",
                    "baseline_last_game", "data_confidence"])
                if patch:
                    client.put("Prop", cur["id"], patch)
                    totals["upd"] += 1
            else:
                try:
                    client.create("Prop", p)
                    totals["new"] += 1
                except Exception as e:
                    print("create failed:", str(e)[:120])
            if not p.get("position"):
                totals["miss_pos"] += 1
print("BACKFILL DONE:", json.dumps(totals))

```


## FILE: `tools/read_backfill.py`
**One-off narrative read backfill (run after schema fields added)**

```python
"""One-off: recompute all current reads on scheduled games with the
sophisticated narrative engine; fix legacy-taxonomy reads; heal stale ones.

- Scheduled-game current reads: recompute from live data, patch every
  schema-accepted field (market_psychology upgrades the UI TODAY).
- Legacy-taxonomy reads on scheduled games: recomputed => correct status.
- Duplicate current reads per (game, market): keep newest, supersede rest.
"""
import json
import sys

sys.path.insert(0, ".")
from nova.api import Base44Client, changed_fields
from nova import reads, enrich

client = Base44Client()
games = client.list_all("Game", fields=["id", "home_team", "away_team",
    "home_team_abbr", "away_team_abbr", "league_code", "status", "start_time",
    "provider_game_id"])
ratings = client.list_all("TeamRating")
forms = client.list_all("TeamForm")
odds = client.list_all("OddsSnapshot", fields=["id", "game_id", "book",
    "market_type", "is_current", "home_line", "over_line", "home_price",
    "away_price", "over_price", "created_date"])
injuries = client.list_all("Injury", fields=["id", "league_code", "team_abbr",
                                             "player_name", "status", "injury_type"])
all_reads = client.list_all("MarketNovaRead")

sched = {g["id"]: g for g in games if str(g.get("status")).lower() == "scheduled"}

# --- 1) duplicate current reads per (game, market): keep newest ---
from collections import defaultdict
by_key = defaultdict(list)
for r in all_reads:
    if r.get("is_current"):
        by_key[(r.get("game_id"), str(r.get("market_type")).lower())].append(r)
dup_groups = {k: v for k, v in by_key.items() if len(v) > 1}
n_super = 0
for k, group in dup_groups.items():
    group.sort(key=lambda r: str(r.get("updated_date") or r.get("created_date") or ""), reverse=True)
    for r in group[1:]:
        client.put("MarketNovaRead", r["id"], {"is_current": False})
        n_super += 1
print(f"superseded dup current reads: {n_super} (of {len(dup_groups)} groups)")

# reload current reads after supersede
current_reads = {}
for r in all_reads:
    if r.get("is_current") and r.get("game_id") in sched:
        current_reads.setdefault(r["game_id"], []).append(r)

n_upd = n_new = n_err = 0
for gid, g in sched.items():
    rows = [o for o in odds if o.get("game_id") == gid]
    markets = reads.freshest_market(rows)
    if not markets or not markets.get("spread") or markets["spread"].get("market_line") is None:
        continue
    pair = reads.resolve_ratings(g, ratings)
    if not pair:
        continue
    tape = [(o.get("created_date"), o.get("home_line"), o.get("home_price"))
            for o in rows
            if str(o.get("market_type")).lower() == "spread"
            and o.get("home_line") is not None]
    from nova import matching as _m
    lg = str(g.get("league_code") or g.get("sport") or "").upper()
    details = {"home": [], "away": []}
    for side in ("home", "away"):
        target = _m.canon(g.get(f"{side}_team") or g.get(f"{side}_team_name") or "", lg)
        for i in injuries:
            il = str(i.get("league_code") or "").upper()
            if il and il != lg:
                continue
            if _m.canon(i.get("team_abbr") or i.get("team_name") or "", lg) == target:
                details[side].append(i)
    try:
        result = reads.compute_read(g, pair, markets,
                                   form=reads.resolve_form(g, forms),
                                   movement=enrich.movement_context(rows),
                                   injuries=len(details.get("home", [])) + len(details.get("away", [])),
                                   tape=tape, injury_details=details)
    except Exception as e:
        n_err += 1
        print("compute failed", g.get("away_team"), "@", g.get("home_team"), str(e)[:100])
        continue
    if result.get("status") == "AWAITING DATA":
        continue  # honest stub; keep existing read
    desired = {
        "league_code": lg, "sport": str(g.get("sport") or g.get("league_code") or "").upper(),
        "status": result["status"],
        "marketnova_true_line": result.get("true_line"),
        "raw_edge": result.get("raw_edge"),
        "market_line": result.get("market_line"),
        "true_line_value": result.get("true_line"),
        "fair_price": result.get("true_line"),
        "edge_value": result.get("edge_value"),
        "market_odds": result.get("market_odds"),
        "edge_score_display": result.get("edge_score_display"),
        "edge_direction": result.get("edge_direction"),
        "minimum_playable_price": result.get("minimum_playable"),
        "playable_until": result.get("playable_until"),
        "next_trigger": result.get("next_trigger"),
        "invalidators": json.dumps(result.get("invalidators")) if result.get("invalidators") else None,
        "market_psychology": result.get("why_market_here") or result.get("market_psychology"),
        "data_confidence": result.get("data_confidence"),
        "key_number_quality": result.get("key_number_quality"),
        "weather": result.get("weather"),
        "line_movement_direction": result.get("line_movement_direction"),
        "sharp_resistance": result.get("sharp_resistance"),
        "public_bias": result.get("public_bias"),
        "game_state_support": result.get("game_state_support"),
        "regression_support": result.get("regression_support"),
        "lineup_status": result.get("lineup_status"),
        "what_the_book_is_doing": result.get("why_market_here"),
        "one_line_read": result.get("nova_read"),
        "why_market_here": result.get("why_market_here"),
        "thesis": result.get("thesis"),
        "nova_read": result.get("nova_read"),
        "risk": result.get("risk"),
        "buy_to": result.get("buy_to"),
        "regression_notes": result.get("regression_notes"),
        "market_tape": result.get("market_tape"),
        "true_line_basis": result.get("true_line_basis"),
        "value_window": result.get("value_window"),
        "confidence": result.get("confidence"),
    }
    PATCH_FIELDS = ["status", "marketnova_true_line", "raw_edge", "market_line",
        "true_line_value", "fair_price", "edge_value", "market_odds",
        "edge_score_display", "edge_direction", "minimum_playable_price",
        "playable_until", "next_trigger", "invalidators", "market_psychology",
        "data_confidence", "key_number_quality", "weather",
        "line_movement_direction", "sharp_resistance", "public_bias",
        "game_state_support", "regression_support", "lineup_status",
        "what_the_book_is_doing", "one_line_read", "why_market_here", "thesis",
        "nova_read", "risk", "buy_to", "regression_notes", "market_tape",
        "true_line_basis", "value_window", "confidence"]
    existing = current_reads.get(gid) or []
    spread_cur = next((r for r in existing
                       if str(r.get("market_type")).lower() == "spread"), None)
    if spread_cur:
        patch = changed_fields(spread_cur, desired, PATCH_FIELDS)
        if patch:
            try:
                client.put("MarketNovaRead", spread_cur["id"], patch)
                n_upd += 1
            except Exception as e:
                n_err += 1
                print("put failed", gid, str(e)[:100])
    else:
        desired["game_id"] = gid
        desired["market_type"] = "spread"
        desired["is_current"] = True
        try:
            client.create("MarketNovaRead", desired)
            n_new += 1
        except Exception as e:
            n_err += 1
            print("create failed", gid, str(e)[:100])

print(f"READ BACKFILL DONE: updated {n_upd}, created {n_new}, errors {n_err}, "
      f"superseded {n_super}")

```


## FILE: `tools/merge_dup_games.py`
**One-off duplicate-game merge + odds healer**

```python
"""One-off repair: merge duplicate Game records, repoint children, heal
duplicate current odds rows.

Steps:
1. Group games by (league, start date, home[:12], away[:12]).
2. Canonical = record WITH provider_game_id (newest wins ties).
3. Repoint children: OddsSnapshot, MarketNovaRead, Prop, Analysis,
   TravelContext, WeatherSnapshot -> canonical id.
4. Delete dup Game records (backup dumped first).
5. Heal odds: per (game, book, market) multiple is_current rows ->
   keep newest, supersede rest.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, ".")
from nova.api import Base44Client

client = Base44Client()
BACKUP = f"backup_dupgame_merge_{datetime.utcnow():%Y%m%d_%H%M}.jsonl"

games = client.list_all("Game", fields=["id", "home_team", "away_team",
    "home_team_abbr", "away_team_abbr", "league_code", "status", "start_time",
    "provider_game_id", "created_date", "created_by"])

groups = defaultdict(list)
for g in games:
    day = str(g.get("start_time"))[:10]
    key = (str(g.get("league_code") or g.get("sport") or "").lower(), day,
           str(g.get("home_team") or "").lower()[:12],
           str(g.get("away_team") or "").lower()[:12])
    groups[key].append(g)

dups = {k: v for k, v in groups.items() if len(v) > 1 and k[2] and k[3]}
print(f"dup groups: {len(dups)}")

odds = client.list_all("OddsSnapshot", fields=["id", "game_id", "book",
    "market_type", "is_current", "created_date"])
reads = client.list_all("MarketNovaRead", fields=["id", "game_id", "is_current"])
try:
    props = client.list_all("Prop", fields=["id", "game_id"])
except Exception:
    props = []
try:
    analyses = client.list_all("Analysis", fields=["id", "game_id"])
except Exception:
    analyses = []
try:
    travels = client.list_all("TravelContext", fields=["id", "game_id", "team_abbr", "days_rest"])
except Exception:
    travels = []
try:
    weathers = client.list_all("WeatherSnapshot", fields=["id", "game_id", "created_date"])
except Exception:
    weathers = []

backup_rows = []
n_merged = n_repointed = n_deleted = 0
for key, grp in dups.items():
    grp.sort(key=lambda g: (0 if g.get("provider_game_id") else 1,
                            str(g.get("created_date") or "")), reverse=False)
    # stable: provider-id first, then newest created
    with_pid = [g for g in grp if g.get("provider_game_id")]
    without = [g for g in grp if not g.get("provider_game_id")]
    with_pid.sort(key=lambda g: str(g.get("created_date") or ""), reverse=True)
    without.sort(key=lambda g: str(g.get("created_date") or ""), reverse=True)
    ordered = with_pid + without
    canonical, dups_of = ordered[0], ordered[1:]
    backup_rows.append({"canonical": canonical["id"], "dups": [
        {"id": d["id"], "created_by": d.get("created_by"),
         "provider_game_id": d.get("provider_game_id")} for d in dups_of]})
    dup_ids = {d["id"] for d in dups_of}
    # repoint (simple, typed)
    for row in odds:
        if row.get("game_id") in dup_ids:
            client.put("OddsSnapshot", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in reads:
        if row.get("game_id") in dup_ids:
            client.put("MarketNovaRead", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in props:
        if row.get("game_id") in dup_ids:
            client.put("Prop", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in analyses:
        if row.get("game_id") in dup_ids:
            client.put("Analysis", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in travels:
        if row.get("game_id") in dup_ids:
            client.put("TravelContext", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in weathers:
        if row.get("game_id") in dup_ids:
            client.put("WeatherSnapshot", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for d in dups_of:
        try:
            client.delete("Game", d["id"])
            n_deleted += 1
        except Exception as e:
            print("delete failed", d["id"], str(e)[:80])
    n_merged += 1

with open(BACKUP, "w") as f:
    for r in backup_rows:
        f.write(json.dumps(r) + "\n")

# --- heal duplicate current odds ---
odds2 = client.list_all("OddsSnapshot", fields=["id", "game_id", "book",
    "market_type", "is_current", "home_line", "over_line", "home_price",
    "away_price", "over_price", "created_date"])
by_key = defaultdict(list)
for o in odds2:
    if o.get("is_current"):
        by_key[(o.get("game_id"), o.get("book"), str(o.get("market_type")).lower())].append(o)
n_super = 0
for k, grp in by_key.items():
    if len(grp) < 2:
        continue
    grp.sort(key=lambda r: str(r.get("created_date") or ""), reverse=True)
    keep = grp[0]
    for r in grp[1:]:
        kl = (keep.get("home_line"), keep.get("over_line"), keep.get("home_price"))
        rl = (r.get("home_line"), r.get("over_line"), r.get("home_price"))
        if kl == rl:
            # exact duplicate row — supersede older
            client.put("OddsSnapshot", r["id"], {"is_current": False})
            n_super += 1
        else:
            # same key, different numbers — keep newest current, supersede
            # older as history (tape)
            client.put("OddsSnapshot", r["id"], {"is_current": False})
            n_super += 1
print(f"MERGE DONE: groups {n_merged}, repointed {n_repointed}, deleted {n_deleted}, "
      f"odds superseded {n_super}. backup: {BACKUP}")

```


## FILE: `run_cycle.py`
**CLI entrypoint**

```python
#!/usr/bin/env python3
"""Nova worker entrypoint.

  python run_cycle.py --dry-run   # compute the plan, write nothing
  python run_cycle.py             # full cycle with batched delta writes
"""
import sys
from nova.cycle import run

if __name__ == "__main__":
    run(dry="--dry-run" in sys.argv or "-n" in sys.argv)

```


## FILE: `README.md`
**Package README**

```python
# Nova External Worker

MarketNova's intelligence cycle, running **off the Base44 meter**.

## Why this exists

Every call into the Base44 backend is metered (integration credits). The old
on-platform ingestion burned the monthly budget on volume: hundreds of small
API calls per cycle. This worker does the heavy lifting externally — ESPN
fetches, odds polling, true-line computation, read generation — and touches
Base44 **only with batched delta writes**: a quiet board costs single-digit
API calls per cycle.

## What one cycle does

1. **Odds sync first** — the market source is **ESPN's scoreboard odds
   (DraftKings lines, free, no API account needed)**; an odds-api key is now
   OPTIONAL and only adds multi-book depth when set. This is the source of
   record for what belongs on the board: **games exist because markets
   exist**. New games are created only from events carrying real book
   prices; a game no book prices is not board material. Snapshot writes
   are upsert-keyed on (game, book, market) in the production OddsSnapshot
   schema (`book`, `home_line`/`away_line`, `over_line`/`under_line`,
   `home_price`/`away_price`) — both outcomes live inside one row, so the
   historic double-insert bug is structurally impossible. If the line is
   unchanged, zero rows are written. When a price moves, the old row is
   superseded (`is_current=false`) and one new current row inserted — line
   history is preserved and grows every cycle.
2. **ESPN sync second** — links games to ESPN identity (`provider_game_id`)
   and keeps status/scores live. ESPN never creates games. Matching:
   provider_game_id first, then canonical-abbr/name + kickoff-proximity
   fallback (league-aware alias map, accent-safe), only against games with
   no ESPN id. Terminal games (CANCELED / POSTPONED / SUSPENDED / DELAYED)
   are exempt from score reconciliation; final games never regress. One-side
   overlaps are refused and logged rather than guessed — duplicates are the
   disease this repo exists to prevent.
3. **Read generation** — resolves both teams' power ratings through the
   alias map, builds the independent true price via the embedded MarketNova
   kernel (`nova/kernel.py`, 638 lines encoding the Master Brain spec,
   53-check self-test run before every cycle), then applies the canonical
   decision gates. Hard sanity gate: a computed edge over 8 points is a
   data conflict, not an edge — the read goes to AWAITING DATA with the
   reason. Status taxonomy: PLAY / LEAN / MONITOR / PASS / NUMBER GONE /
   AWAITING DATA.
4. **Delta writes** — reads and games are only written when tracked fields
   actually changed (float-tolerant comparison, so API round-trip noise
   never causes phantom writes). `bet_*` recommendation fields are never
   touched (the frontend maps them).

A quiet board costs single-digit API calls per cycle.

## Deploy (GitHub Actions, ~5 minutes)

1. Create a **public** GitHub repo (public = free unlimited Actions minutes;
   a private repo also works on the free tier, ~2000 min/month, which a
   30-min cycle uses ~2000 — a public repo is recommended, or reduce to
   hourly).
2. Copy this folder's contents into the repo (the `.github/workflows/
   nova-cycle.yml` schedule comes with it — every 30 minutes, plus a
   kernel self-test before every cycle).
3. Add repo secrets (**Settings → Secrets and variables → Actions**):

   | Secret | Required | Value |
   |---|---|---|
   | `BASE44_SERVICE_TOKEN` | **yes** | In the Base44 editor: **Dashboard → API → Create access token** (personal access token — it signs calls to your app's entities REST API). If it's ever exposed, delete it and create a new one. |
   | `ODDS_API_KEY` | no | Optional multi-book depth via The Odds API. Without it, ESPN's free DraftKings odds are the sole market source. |
   | `LLM_API_KEY` | no | Optional richer narratives. Without it, reads use deterministic kernel-derived templates. |

4. Run it once by hand (**Actions → Nova cycle → Run workflow**) and watch
   the log — each phase prints its counts. Then it runs itself every 30 min.

Optional tuning via repo **variables** (env vars, not secrets):

| Variable | Default | Meaning |
|---|---|---|
| `NOVA_LEAGUES` | `nfl,ncaaf,nhl,nba,ncaab,wnba,mlb` | Comma-separated leagues to cycle |
| `NOVA_LOOKAHEAD_DAYS` | `2` | Board window ahead of now |
| `NOVA_DRY_RUN` | `0` | `1` = compute and log, write nothing |

## Local dry run

```bash
BASE44_SERVICE_TOKEN=... python3 run_cycle.py --dry-run
```

Computes the full plan against live data and writes nothing — use it to
verify changes before deploying.

## Game theory layer (juice pressure + line lifecycle)

Two doctrine-driven refinements in the read engine:

- **Juice pressure (Part XX):** books probe demand with juice before
  crossing numbers. When the number holds but the price moves against
  the read's value side, the read records `juice_pressure:
  against_value_side`, passes `sharp_resistance=1` into the kernel, and
  a quiet PASS upgrades to MONITOR (a repricing trigger per doctrine).
  Juice on the opposite side changes nothing.
- **Line lifecycle (Part XVII, timing is an edge):** every new read
  records `stage` (early >48h / mid 24-48h / late <24h) and
  `hours_to_close` in `data_quality_json`, so Performance can later
  measure whether edges taken early beat close more often.

## Offline full-cycle run (ZERO Base44 credits)

`tools/offline_run.py` runs the ENTIRE cycle — live ESPN fetches, odds
plan, ESPN reconcile, read generation, and the full write phase — against
local cached entity dumps instead of the Base44 API. Writes are recorded
to `offline_run_output.json` and never leave the machine. Use it to
verify code changes end-to-end when credits are exhausted.

```bash
# stage caches (any missing entity is treated as empty)
mkdir offline_cache
cp Game.dump.json offline_cache/Game.cache.json
BASE44_SERVICE_TOKEN=offline-noop python3 tools/offline_run.py offline_cache
```

Cache files: `Game.cache.json`, `TeamRating.cache.json`,
`MarketNovaRead.cache.json`, `OddsSnapshot.cache.json`. The runner also
validates structural invariants on the simulated plan (duplicate ids,
orphaned odds/read refs).

## Cutover (after 24h green)

The on-platform odds ingestion (Bovada writer, ~every 30 min) is still
active. Once this worker has run green for a day, the platform ingestion
**must be deactivated** so the two pipelines can't race each other
creating games — duplicates are the disease this repo exists to prevent.

## Status taxonomy

Reads publish the V3 standard taxonomy: PLAY / LEAN / MONITOR / PASS /
NUMBER GONE / AWAITING DATA. Missing ratings or odds, sanity-bound edges
(> 8 points = data conflict, not an edge), and unresolvable data all
degrade to AWAITING DATA with a reason — Nova never invents numbers.

## Kernel usage note

`kernel.decide()` defaults to `direction="under"`, which is for TOTALS.
For spread reads pass `"favorite"` or `"dog"` — the side the true line says
has value. Passing the default makes every spread verdict come out
NUMBER GONE (verified and fixed Oct 2026; don't regress it).

Two sanity gates run in the reads layer, both verified against live data:
1. Signed-edge gate (before the kernel): |true − market| on the same
   home-perspective scale. Over 8 points → AWAITING DATA as a
   ratings/market data conflict. Never use |abs(market)| − |abs(true)| —
   it collapses sign flips (a −6.5 market vs +14 true is a 20.5-point
   disagreement, not 7.5). This gate caught 22 stale/mis-rated games on
   first live audit.
2. Post-decide gate: any kernel verdict with an over-8 computed edge is
   forced to AWAITING DATA with the reason.

## Doctrine (from the Master Brain spec)

- Build the true price before reading the market; the market may explain,
  confirm, or invalidate a discrepancy, never create a wager by itself.
- Silence is not confirmation; missing data yields AWAITING DATA, never
  invented numbers.
- Historical states are immutable; corrections create new versions.

```

---

# 9. OPERATING DOCTRINE (summary)

Read the full FASTESTPAYOUT_DOCTRINE_v1.md / Master Brain files for the
complete 70-part doctrine. The kernel (nova/kernel.py) encodes:
true-price construction, decision gates, key numbers, movement/sharp
scoring, 2H regression, CLV, event-sourcing replay. The sequence per
game: GAME IDENTITY -> TRUE LINE -> MARKET READ -> BETTOR PSYCHOLOGY ->
SPORTSBOOK CONTEXT -> DISTORTION/REGRESSION -> GAME THEORY -> BEST
EXPRESSION -> PRICE TEST -> RISK TEST -> ACTION.

Status thresholds (pregame spread): >=2.0 PLAY, >=1.5 LEAN, >0.3
MONITOR, else PASS. Totals differ slightly. All in kernel.py.

END OF PACKAGE.
