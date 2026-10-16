"""Nova live research layer — OpenAI Responses API + web_search.

The world-facing research tool for MarketNova. OpenAI handles live web
research (injuries, lineups, goalies, weather, beat-reporter news) that the
deterministic worker does not already have from structured feeds.

HARD DOCTRINE (Emergency Stabilization + FASTESTPAYOUT):
- ChatGPT is a RESEARCH tool, never the price engine. Research output NEVER
  writes true lines, edges, or action states; the deterministic kernel owns
  the market decision. Do not ask OpenAI "what should the true line be".
- Every claim keeps source, url, retrieved_at, verification and a source
  quality tier. Research never gets flattened into untraceable prose.
- Fail-open everywhere: no key, API errors, malformed output → research is
  skipped for that game and the cycle continues unaffected.
- Cost control: MODE 2 only for stale-per-TTL scheduled games inside the
  research window, capped at NOVA_RESEARCH_MAX_GAMES per cycle.

MODES (the router novaChat mirrors client-side):
- MODE 0 deterministic — answer from kernel decision fields (fair line,
  buy-to, taxonomy). Never searches.
- MODE 1 cached Nova — research intent exists and is fresh; serve it.
- MODE 2 live web research — research intent missing or stale; OpenAI
  web_search runs this cycle and refreshes it.
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

from nova import config
from nova import llm_queue

# Source hierarchy (lower tier = better). Domains map to the doctrine's
# preference order: official league/team first, then injury/lineup feeds,
# reputable sports news, established beat reporters, everything else as
# lead-only material.
_TIER_DOMAINS = {
    # 1 — official league/team sources
    1: ("nhl.com", "nba.com", "nfl.com", "mlb.com", "wnba.com", "ncaa.com",
        "ncaa.org", "mlssoccer.com", "cfl.ca"),
    # 2 — official injury / lineup feed providers
    2: ("espn.com", "espn.co.uk"),
    # 3 — highly reputable sports news
    3: ("apnews.com", "theathletic.com", "si.com", "sportsillustrated.com",
        "cbssports.com", "sports.yahoo.com", "foxsports.com",
        "nbcsports.com", "sportingnews.com", "bleacherreport.com",
        "mlb.com", "rotoinfo.com"),
    # 4 — established beat reporters / specialist sports sources
    4: ("rotowire.com", "dailyfaceoff.com", "nhltradetracker.com",
        "profootballtalk.com", "substack.com"),
}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# MODE ROUTER
# --------------------------------------------------------------------------

# Never-search intents: answered entirely from canonical MarketNova state.
_DETERMINISTIC_PATTERNS = (
    "fair line", "true line", "nova fair", "buy-to", "buy to", "buy to",
    "why is this a play", "why a play", "explain", "what is a", "what is the",
    "number gone", "what did nova", "taxonomy", "what does nova think",
    "how does nova get there", "value window", "edge", "status", "risk",
    "breaks the thesis", "would change nova", "monitor", "lean", "pass",
    "best expression", "minimum playable", "clv", "closing line",
)

# Research intents: current-world information the structured feeds may miss.
_RESEARCH_PATTERNS = (
    "update", "confirmed", "confirm", "check the", "check if", "injur",
    "lineup", "goalie", "pitcher", "starter", "starting", "scratch",
    "weather", "beat", "reporter", "news", "last hour", "anything change",
    "research", "roster", "trade", "travel", "coach", "venue", "roof",
    "availability", "line combination", "who is out", "who's out",
    "day-to-day", "game-time decision", "gtd", " activate", "ir list",
)


def classify(question):
    """Route a Nova question. Returns 0 (deterministic), 2 (research layer
    required — freshness decides MODE 1 vs 2 at render time)."""
    q = " " + str(question or "").lower() + " "
    for pat in _RESEARCH_PATTERNS:
        if pat in q:
            return 2
    for pat in _DETERMINISTIC_PATTERNS:
        if pat in q:
            return 0
    # unknown questions default to canonical state first (cheapest, honest)
    return 0


# --------------------------------------------------------------------------
# PROMPT
# --------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are Nova's live research layer.

Research the following event:

{event}

League: {league}
Start time (UTC): {start}

Current MarketNova state (canonical; do not re-derive it):
{snapshot}

Find only CURRENT information that could materially affect the
MarketNova analysis of this game. Research:

- injuries and player availability
- lineup / starter / goalie / pitcher status
- recent personnel or roster changes
- weather / venue conditions if relevant
- rest / travel developments
- credible matchup-specific news
- coach or team comments with actual analytical relevance

Rules:
- Do not create a betting recommendation.
- Do not create a true line or predict a score.
- Do not claim sharp money or public betting percentages.
- Do not speculate beyond sources. If something is uncertain, list it as
  uncertain.
- Prefer official league/team sources, then injury feeds, then reputable
  sports news, then established beat reporters. Never treat an
  uncorroborated social post as confirmed.

Return ONLY this JSON structure (no prose around it):

{{
  "material_changes": [
    {{"claim": "...", "source": "source name", "url": "https://...",
      "verification": "confirmed|reported|unverified"}}
  ],
  "confirmed_facts": [
    {{"claim": "...", "source": "source name", "url": "https://...",
      "verification": "confirmed"}}
  ],
  "uncertain_items": [
    {{"claim": "...", "source": "source name", "url": "https://..."}}
  ],
  "reanalysis_required": true/false
}}

Set reanalysis_required true only if a confirmed change materially affects
the inputs MarketNova prices this game on (starting goalie, starting
pitcher, key player out, venue/weather shift)."""


def build_prompt(game, read, league_label=None):
    """Research prompt for one game, per the MarketNova research contract."""
    away = game.get("away_team") or "?"
    home = game.get("home_team") or "?"
    event = f"{away} @ {home}"
    if game.get("venue"):
        event += f" — venue: {game.get('venue')}"
    start = str(game.get("start_time") or "unknown")
    league = str(league_label or game.get("league_code") or game.get("sport") or "").upper()
    snapshot = {
        "nova_status": read.get("status"),
        "nova_true_line": read.get("marketnova_true_line"),
        "market_line": read.get("market_line"),
        "edge": read.get("edge_value"),
        "one_line_read": read.get("one_line_read"),
    }
    return PROMPT_TEMPLATE.format(
        event=event, league=league, start=start,
        snapshot=json.dumps(snapshot, ensure_ascii=False))


# --------------------------------------------------------------------------
# LLM BRIDGE (Base44 AI via the LLMTask queue — OpenAI removed Oct 16 2026)
# --------------------------------------------------------------------------
def _output_text(resp):
    """Extract the assistant text and url_citation annotations from a
    Responses API payload."""
    texts, citations = [], []
    for item in resp.get("output") or []:
        if item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if block.get("type") in ("output_text", "text"):
                texts.append(block.get("text") or "")
                for ann in block.get("annotations") or []:
                    if ann.get("type") == "url_citation" and ann.get("url"):
                        citations.append({"url": ann.get("url"),
                                          "title": ann.get("title") or ""})
    return "\n".join(texts).strip(), citations


def _is_model_error(err):
    try:
        body = err.read().decode("utf-8", "replace") if hasattr(err, "read") else str(err)
    except Exception:
        body = str(err)
    return "model" in body.lower() and ("not_found" in body.lower()
                                       or "does not exist" in body.lower()
                                       or "unsupported" in body.lower()
                                       or "deprecat" in body.lower())


# --------------------------------------------------------------------------
# OUTPUT PARSING + SOURCE DISCIPLINE
# --------------------------------------------------------------------------

def source_quality(url, source_name=""):
    """Tier 1 (official) .. 5 (lead-only) per the doctrine hierarchy."""
    host = ""
    m = re.search(r"https?://([^/]+)", str(url or ""), re.I)
    if m:
        host = m.group(1).lower().replace("www.", "")
    text = f"{host} {str(source_name or '').lower()}"
    for tier in (1, 2, 3, 4):
        for dom in _TIER_DOMAINS[tier]:
            if dom in text:
                return tier
    return 5


def _extract_json(text):
    """Pull the research JSON object out of the model output."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    start = text.find("{")
    if start >= 0:
        depth, end = 0, -1
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
    return None


def _normalize_claim(item, retrieved_at, citations):
    """Enforce the source-discipline contract on one claim."""
    if not isinstance(item, dict) or not str(item.get("claim") or "").strip():
        return None
    claim = {
        "claim": str(item["claim"]).strip(),
        "source": str(item.get("source") or "").strip() or "unknown",
        "url": str(item.get("url") or "").strip(),
        "retrieved_at": retrieved_at,
        "verification": str(item.get("verification") or "").strip().lower(),
    }
    if claim["verification"] not in ("confirmed", "reported", "unverified"):
        claim["verification"] = "reported" if claim["url"] else "unverified"
    if not claim["url"]:
        # a claim with no URL is lead-only material, never confirmed
        claim["verification"] = "unverified"
        for c in citations:
            if c.get("title") and c["title"].lower() in claim["source"].lower():
                claim["url"] = c["url"]
                if claim["verification"] == "unverified":
                    claim["verification"] = "reported"
                break
    claim["source_quality"] = source_quality(claim["url"], claim["source"])
    if claim["source_quality"] >= 5 and claim["verification"] == "confirmed":
        # weak source cannot carry a confirmed flag without corroboration
        claim["verification"] = "reported"
    return claim


def parse_research(text, citations, retrieved_at):
    """Parse + normalize the model's research JSON. Returns the findings
    dict or None (honest fail — never invent findings)."""
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    out = {"material_changes": [], "confirmed_facts": [],
           "uncertain_items": [], "sources": [],
           "reanalysis_required": bool(data.get("reanalysis_required"))}
    for key in ("material_changes", "confirmed_facts", "uncertain_items"):
        items = data.get(key) if isinstance(data.get(key), list) else []
        for it in items[:12]:
            c = _normalize_claim(it, retrieved_at, citations)
            if c:
                out[key].append(c)
    # sources: the distinct cited claims used above
    seen, srcs = set(), []
    for key in ("material_changes", "confirmed_facts", "uncertain_items"):
        for c in out[key]:
            k = (c["claim"], c["url"])
            if k not in seen:
                seen.add(k)
                srcs.append(c)
    out["sources"] = srcs
    return out


# --------------------------------------------------------------------------
# PACK INTENT + TTL POLICY
# --------------------------------------------------------------------------

def pack_intent(findings, researched_at, model):
    """The structured live_research intent stored inside the answer pack.

    Kept structured (never flattened prose) so every claim stays traceable
    to its source, url, retrieval time and verification state.
    """
    n = len(findings.get("material_changes") or [])
    no_change = n == 0 and not findings.get("reanalysis_required")
    summary = ("No material change since Nova's previous read."
               if no_change else
               f"{n} material change{'s' if n != 1 else ''} found — "
               "see items below.")
    return {
        "research_version": 1,
        "researched_at": researched_at,
        "model": model,
        "summary": summary,
        "no_material_change": no_change,
        "reanalysis_required": bool(findings.get("reanalysis_required")),
        "material_changes": findings.get("material_changes") or [],
        "confirmed_facts": findings.get("confirmed_facts") or [],
        "uncertain_items": findings.get("uncertain_items") or [],
        "sources": findings.get("sources") or [],
    }


def last_researched(read):
    """researched_at from a read's stored pack (handles the Base44
    string-vs-object JSON roundtrip)."""
    pack = read.get("thesis_json")
    if isinstance(pack, str):
        try:
            pack = json.loads(pack)
        except ValueError:
            return None
    if not isinstance(pack, dict):
        return None
    lr = pack.get("answer_pack", {}).get("live_research") if isinstance(pack.get("answer_pack"), dict) else None
    if not isinstance(lr, dict):
        return None
    return _parse_dt(lr.get("researched_at"))


def ttl_hours(game, now=None):
    """Research freshness budget by proximity to start (cost control):
    far out = long TTL, close to game = short TTL."""
    start = _parse_dt(game.get("start_time"))
    if not start:
        return config.RESEARCH_TTL_FAR
    hrs = (start - (now or _now())).total_seconds() / 3600.0
    if hrs <= 4:
        return config.RESEARCH_TTL_NEAR
    if hrs <= 24:
        return config.RESEARCH_TTL_MID
    return config.RESEARCH_TTL_FAR


def is_stale(game, read, now=None):
    """True when this game needs MODE 2 live research."""
    at = last_researched(read)
    if at is None:
        return True
    age = ((now or _now()) - at).total_seconds() / 3600.0
    return age >= ttl_hours(game, now)


def pick_candidates(games, reads_by_game, now=None, cap=None):
    """Scheduled games inside the research window whose current read is
    research-stale, soonest start first, capped per cycle."""
    now = now or _now()
    cap = cap if cap is not None else config.RESEARCH_MAX_GAMES
    cands = []
    for g in games:
        if str(g.get("status") or "").lower() != "scheduled":
            continue
        start = _parse_dt(g.get("start_time"))
        if not start:
            continue
        hrs = (start - now).total_seconds() / 3600.0
        if hrs < 0 or hrs > config.RESEARCH_WINDOW_H:
            continue
        read = reads_by_game.get(g.get("id"))
        if not read:
            continue                       # new games research next cycle
        if not is_stale(g, read, now):
            continue
        cands.append((start, g, read))
    cands.sort(key=lambda x: x[0])
    return [(g, read) for _, g, read in cands[:cap]]


# --------------------------------------------------------------------------
# CYCLE ENTRY POINT
# --------------------------------------------------------------------------

def enabled():
    return not config.RESEARCH_DISABLED


def run_research(games, reads_by_game, client, log=lambda *a: None, now=None):
    """MODE 2 research pass. Returns {game_id: live_research pack intent}.

    The findings ride the read's thesis_json answer pack; the deterministic
    engine is never modified by research. reanalysis_required is logged so
    the repricing question is visible — but true lines only ever change
    through the kernel + structured feeds.
    """
    if not enabled():
        log("live research disabled (NOVA_RESEARCH=0) — MODE 0/1 only")
        return {}
    now = now or _now()
    candidates = pick_candidates(games, reads_by_game, now)
    if not candidates:
        log("no stale research targets this cycle")
        return {}
    out = {}
    for g, read in candidates:
        away, home = g.get("away_team") or "?", g.get("home_team") or "?"
        label = f"{away}@{home}"
        # 1) collect a finished bridge task (async: enqueued last cycle,
        #    run by the app-side Base44 AI function)
        try:
            done = llm_queue.collect(client, "research", g.get("id"), log=log)
        except Exception as e:
            log(f"{label}: queue error (non-fatal): {e}")
            done = None
        if done is not None:
            task_id, text, model = done
            try:
                findings = parse_research(text, [], _iso(now))
                if findings is None:
                    log(f"{label}: unparseable research output — skipped")
                else:
                    intent = pack_intent(findings, _iso(now), model)
                    out[g["id"]] = intent
                    log(f"{label}: {len(intent['material_changes'])} material, "
                                 f"{len(intent['confirmed_facts'])} confirmed, "
                                 f"{len(intent['sources'])} sources"
                        + (" — REANALYSIS REQUIRED" if intent["reanalysis_required"] else ""))
            except Exception as e:
                log(f"{label}: research parse error (non-fatal): {e}")
            finally:
                llm_queue.mark_consumed(client, task_id, log=log)
            continue
        # 2) no result yet — make sure a task is in flight
        try:
            llm_queue.enqueue(client, "research", g.get("id"),
                              build_prompt(g, read), log=log)
        except Exception as e:            # research must never break a cycle
            log(f"{label}: enqueue error (non-fatal): {e}")
    log(f"{len(out)}/{len(candidates)} games researched this cycle")
    return out
