"""MODE 2 SYNTHESIS LAYER — the founder's research process, automated.

Nova's deep-analysis engine: for a qualifying game, OpenAI (or any
Responses-API-compatible model) receives the deterministic worker's
structured inputs (kernel baseline, odds tape, injuries, weather, rest,
form, movement, sourced research findings) and must construct a
MarketNova Analysis Pack — a structured JSON answering the founder's 16
sportsbook-research questions, ending in an independent FAIR RANGE.

DOCTRINE (binding):
- Synthesis is an ANALYSIS INPUT to the one canonical MarketNovaRead —
  never a second opinion, never a chatbot answer, never a UI decorator.
- The kernel still owns everything downstream: sanity bounds, vig, key
  numbers, edge math, thresholds, action taxonomy, NUMBER GONE, buy-to.
  A validated synthesis changes exactly one kernel input — the true
  line — plus the narrative fields (thesis/risk/invalidators).
- Fair RANGE, not fake precision. Ranges wider than the sanity cap are
  rejected, ranges outside the ratings-sanity bound are rejected and the
  deterministic baseline stands (fail-open, logged).
- Unsourced web claims never alter the fair price: synthesis only sees
  research findings that already passed nova/research.py source
  discipline (claim/source/url/retrieved_at/verification).
- action_candidate is ADVISORY ONLY — the kernel decides the state.
- Rollout: fixture mode (one game per cycle) until verified; then slate.
"""

import json
import re
import urllib.request
import urllib.error

from nova import config, doctrine
from nova.research import (OPENAI_RESPONSES_URL, _output_text, _extract_json,
                           _iso, _now, _parse_dt)

SYNTH_VERSION = "2"

# The doctrine modules loaded per sport. NHL has no dedicated brain yet —
# CORE + FASTESTPAYOUT carry the sport-agnostic reasoning and the sport
# questions below carry the NHL specifics.
_SPORT_BRAINS = {
    "nfl": "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+NFL_BRAIN",
    "ncaaf": "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+NCAAF_BRAIN",
    "mlb": "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+MLB_BRAIN+F5_BRAIN",
    "f5": "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+MLB_BRAIN+F5_BRAIN",
    "nba": "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+BASKETBALL_BRAIN",
    "wnba": "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+BASKETBALL_BRAIN",
    "ncaab": "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+BASKETBALL_BRAIN",
    "nhl": "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE",
}
_DEFAULT_BRAINS = "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE"

# Sport-specific game-construction inputs the synthesis must weigh
# (founder doctrine sections 18-27; the NFL/NCAAF/MLB/NHL specifics).
SPORT_QUESTIONS = {
    "nhl": [
        "confirmed goalie and goaltending form", "5v5 expected goals",
        "shot share", "high-danger chance creation", "finishing/save regression",
        "special teams", "line combinations", "forecheck/breakout/transition",
        "rest and travel", "injury impact"],
    "mlb": [
        "starting pitcher (mix, velocity, command, K-BB)", "platoon matchup",
        "confirmed lineup and pitch-type fit", "bullpen availability",
        "park and weather", "F5 vs full-game structure"],
    "nfl": [
        "QB play", "OL/DL matchup", "EPA and success rate",
        "run/pass efficiency", "pressure", "explosives", "injuries",
        "coaching and game script", "pace", "weather", "key-number sensitivity"],
    "ncaaf": [
        "QB play", "OL/DL matchup", "EPA and success rate",
        "run/pass efficiency", "pressure", "explosives", "injuries",
        "coaching, depth and rotation", "pace", "weather",
        "key-number sensitivity", "talent reasserting late"],
    "nba": ["pace", "shot profile (rim/3PA)", "turnovers", "OREB",
            "FT environment", "rotation/minutes", "injuries", "rest",
            "matchup"],
    "wnba": ["pace", "shot profile (rim/3PA)", "turnovers", "OREB",
             "FT environment", "rotation/minutes", "injuries", "rest",
             "matchup"],
    "ncaab": ["pace", "shot profile (rim/3PA)", "turnovers", "OREB",
              "FT environment", "rotation/minutes", "injuries", "rest",
              "matchup"],
}
DEFAULT_SPORT_QUESTIONS = [
    "personnel availability", "matchup mechanics", "rest and travel",
    "recent structural vs noisy performance", "environment"]

# FASTESTPAYOUT questions every pack must answer explicitly.
FASTESTPAYOUT_QUESTIONS = [
    "What is the public likely seeing?",
    "What is the sportsbook pricing?",
    "What does the underlying game say?",
    "What part of the obvious story is already priced?",
    "What is likely structural?",
    "What is likely variance?",
    "Has the market already corrected?",
    "What is the best expression?",
    "At what price is the number gone?",
]

PROMPT_TEMPLATE = """{doctrine_context}

# MODE 2 SYNTHESIS TASK

You are Nova's synthesis layer for this event. The deterministic engine
below gives you verified structured data and its own ratings-based
baseline. Your job is the research reasoning the baseline cannot do:
construct the sport-specific game and produce an independent fair RANGE.

## EVENT
{event}

League: {league}
Start (UTC): {start}

## VERIFIED STRUCTURED DATA (worker-sourced; trust these over your priors)
{data}

## SOURCED WEB RESEARCH (already verified for provenance)
{research}

## SPORT-SPECIFIC GAME CONSTRUCTION — weigh each input where data exists:
{sport_questions}

## FASTESTPAYOUT QUESTIONS — answer each explicitly in the pack
{fp_questions}

## RULES (binding)
- Fair range, not fake precision: give a low and a high. Uncertainty
  widens the range; never output decimal-point certainty you do not have.
- Do NOT copy the market and nudge it. Construct the game, then compare.
- Never claim sharp money, public percentages, traps, or "Vegas knows"
  without a sourced fact above.
- Unsourced claims must not move your fair range.
- Do not produce a betting recommendation or a locked pick. The state
  machine decides action from your range; you propose, it rules.
- Every material claim in the pack must be traceable: include source
  and url when it came from the research or data above.
- If an input is missing, say UNKNOWN in the relevant field — never
  invent a value.

## OUTPUT CONTRACT — return ONLY this JSON, no prose around it:
{{
  "research_version": "{rv}",
  "event_id": "{event_id}",
  "market_snapshot": {{"open": <number|null>, "current": <number>,
                       "movement": "<what moved and why, or none>"}},
  "game_reality": {{}},
  "personnel": {{}},
  "matchup": {{}},
  "efficiency": {{}},
  "environment": {{}},
  "situation": {{}},
  "regression": {{"structural": [], "noisy": []}},
  "bettor_story": "",
  "sportsbook_context": "",
  "nova_fair_range": {{"low": <number>, "high": <number>}},
  "fair_price_reasoning": [],
  "best_expression": "",
  "buy_to": "",
  "primary_risk": "",
  "invalidators": [],
  "data_quality": "",
  "action_candidate": "",
  "sources": [{{"claim": "", "source": "", "url": "", "retrieved_at": "",
                "verification": ""}}]
}}

The home-perspective spread convention: negative = home favored. Your
nova_fair_range must be in that convention (e.g. home favored by ~3
=> low -3.5, high -2.5)."""


# --------------------------------------------------------------------------
# CONTEXT + PROMPT
# --------------------------------------------------------------------------

def sport_brain_spec(league):
    return _SPORT_BRAINS.get(str(league or "").lower(), _DEFAULT_BRAINS)


def build_context(game, baseline, market_line, tape=None, injuries=None,
                  injury_details=None, weather=None, rest=None, form=None,
                  movement=None, league=None, research=None):
    """Compact, verified context dict for the synthesis prompt. Everything
    here comes from worker feeds or the proven research pass — the model
    reasons over evidence, not priors."""
    lg = str(league or game.get("league_code") or game.get("sport") or "").lower()
    ctx = {
        "league": lg.upper(),
        "away_team": game.get("away_team"),
        "home_team": game.get("home_team"),
        "venue": game.get("venue"),
        "kernel_baseline_true_line": baseline,
        "market_line": market_line,
        "odds_tape": tape or [],
    }
    if injuries:
        ctx["injury_count"] = injuries
    if injury_details:
        ctx["injury_details"] = injury_details[:14]
    if weather:
        ctx["weather"] = weather
    if rest:
        ctx["rest_days"] = rest
    if form:
        ctx["recent_form"] = form
    if movement:
        ctx["market_movement"] = movement
    if research:
        items = []
        for key in ("material_changes", "confirmed_facts", "uncertain_items"):
            items.extend(research.get(key) or [])
        if items:
            ctx["research_findings"] = items[:20]
    return ctx


def build_prompt(game, context, league=None):
    lg = str(league or context.get("league") or "").lower()
    try:
        doctrine_context = doctrine.prompt_context(sport_brain_spec(lg))
    except Exception:
        doctrine_context = doctrine.prompt_context(_DEFAULT_BRAINS)
    away, home = context.get("away_team") or "?", context.get("home_team") or "?"
    sport_qs = "\n".join(f"- {q}" for q in
                         SPORT_QUESTIONS.get(lg, DEFAULT_SPORT_QUESTIONS))
    fp_qs = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(FASTESTPAYOUT_QUESTIONS))
    research = context.get("research_findings") or []
    research_txt = ("\n".join(
        f"- [{r.get('verification', 'reported').upper()}] {r.get('claim')} "
        f"(source: {r.get('source')}, {r.get('url')})"
        for r in research[:20]) if research else
        "None this cycle — rely on the structured data only.")
    return PROMPT_TEMPLATE.format(
        doctrine_context=doctrine_context,
        event=f"{away} @ {home}",
        league=context.get("league") or lg.upper(),
        start=game.get("start_time") or "unknown",
        data=json.dumps({k: v for k, v in context.items()
                         if k not in ("research_findings", "league",
                                      "away_team", "home_team")},
                        ensure_ascii=False),
        research=research_txt,
        sport_questions=sport_qs,
        fp_questions=fp_qs,
        rv=SYNTH_VERSION,
        event_id=game.get("provider_game_id") or game.get("id") or "")


# --------------------------------------------------------------------------
# CALL + PARSE + VALIDATE
# --------------------------------------------------------------------------

def call_synthesis(prompt, log=lambda *a: None):
    """One synthesis call (no web tool — the model reasons over supplied
    evidence). Same model chain + fallback as the research layer."""
    if not config.OPENAI_API_KEY:
        return None
    payload = {"model": config.OPENAI_MODEL, "input": prompt}
    last_err = None
    for model in (config.OPENAI_MODEL, config.OPENAI_MODEL_FALLBACK):
        if not model or (last_err is not None and not _is_model_error(last_err)):
            break
        payload["model"] = model
        try:
            req = urllib.request.Request(
                OPENAI_RESPONSES_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(
                    req, timeout=config.SYNTH_TIMEOUT_S) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404 and _is_model_error(e):
                log(f"model {model} rejected; trying fallback")
                continue
            log(f"OpenAI {e.code} on synthesis: "
                f"{str(e.read()[:200] if hasattr(e, 'read') else e)}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            log(f"OpenAI unreachable for synthesis: {e}")
            return None
    return None


def _is_model_error(err):
    try:
        body = err.read().decode("utf-8", "replace") if hasattr(err, "read") else str(err)
    except Exception:
        body = str(err)
    return "model" in body.lower() and ("not_found" in body.lower()
                                       or "does not exist" in body.lower()
                                       or "unsupported" in body.lower())


def parse_pack(text):
    """Extract + shape-check the analysis pack. Returns dict or None."""
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    rng = data.get("nova_fair_range")
    if not isinstance(rng, dict):
        return None
    try:
        low, high = float(rng["low"]), float(rng["high"])
    except (TypeError, ValueError, KeyError):
        return None
    pack = {
        "synth_version": SYNTH_VERSION,
        "created_at": _iso(_now()),
        "market_snapshot": data.get("market_snapshot") if isinstance(data.get("market_snapshot"), dict) else {},
        "game_reality": data.get("game_reality") if isinstance(data.get("game_reality"), dict) else {},
        "personnel": data.get("personnel") if isinstance(data.get("personnel"), dict) else {},
        "matchup": data.get("matchup") if isinstance(data.get("matchup"), dict) else {},
        "efficiency": data.get("efficiency") if isinstance(data.get("efficiency"), dict) else {},
        "environment": data.get("environment") if isinstance(data.get("environment"), dict) else {},
        "situation": data.get("situation") if isinstance(data.get("situation"), dict) else {},
        "regression": data.get("regression") if isinstance(data.get("regression"), dict) else {},
        "bettor_story": str(data.get("bettor_story") or ""),
        "sportsbook_context": str(data.get("sportsbook_context") or ""),
        "nova_fair_range": {"low": low, "high": high},
        "fair_price_reasoning": [str(x) for x in (data.get("fair_price_reasoning") or [])][:12]
        if isinstance(data.get("fair_price_reasoning"), list) else [],
        "best_expression": str(data.get("best_expression") or ""),
        "buy_to": str(data.get("buy_to") or ""),
        "primary_risk": str(data.get("primary_risk") or ""),
        "invalidators": [str(x) for x in (data.get("invalidators") or [])][:8]
        if isinstance(data.get("invalidators"), list) else [],
        "data_quality": str(data.get("data_quality") or ""),
        "action_candidate": str(data.get("action_candidate") or ""),
        "sources": [s for s in (data.get("sources") or [])[:16]
                     if isinstance(s, dict) and s.get("claim")][:16],
    }
    return pack


def validate_range(pack, baseline):
    """Kernel-side sanity on the synthesized fair range. Returns
    (validated_mid, notes) — mid is None when the baseline stands."""
    notes = []
    low = pack["nova_fair_range"]["low"]
    high = pack["nova_fair_range"]["high"]
    if low > high:
        return None, ["range inverted (low > high) — rejected"]
    width = high - low
    if width > config.SYNTH_MAX_RANGE_WIDTH:
        return None, [f"range width {width:.1f} exceeds sanity cap "
                      f"{config.SYNTH_MAX_RANGE_WIDTH} — too uncertain to price"]
    mid = round((low + high) / 2.0, 2)
    if baseline is not None:
        drift = abs(mid - float(baseline))
        if drift > config.SYNTH_SANITY_MAX:
            return None, [f"synthesis mid {mid:+.1f} drifts {drift:.1f} from "
                          f"ratings baseline {float(baseline):+.1f} — beyond "
                          f"sanity bound {config.SYNTH_SANITY_MAX}; baseline stands"]
        if drift > config.SYNTH_SANITY_MAX / 2:
            notes.append(f"synthesis drifts {drift:.1f} from baseline — "
                         "flagged for review")
    return mid, notes


# --------------------------------------------------------------------------
# FIXTURE SELECTION + UPDATE DIFF
# --------------------------------------------------------------------------

def synth_enabled():
    if not config.OPENAI_API_KEY:
        return False
    return str(config.SYNTH_MODE).lower() not in ("off", "0", "none")


def select_fixture(games, reads_by_game, now=None):
    """Fixture mode: exactly one deep-analysis game per cycle — the
    soonest-starting scheduled game with a current read that isn't
    AWAITING DATA. Slate mode widens only after fixture verification."""
    now = now or _now()
    cands = []
    for g in games:
        if str(g.get("status") or "").lower() != "scheduled":
            continue
        st = _parse_dt(g.get("start_time"))
        if not st or st < now:
            continue
        read = reads_by_game.get(g.get("id"))
        if not read or read.get("status") == "AWAITING DATA":
            continue
        cands.append((st, g, read))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    if str(config.SYNTH_MODE).lower() == "slate":
        return [(g, r) for _, g, r in cands[:config.SYNTH_MAX_GAMES]]
    st, g, r = cands[0]
    return [(g, r)]


def compare_packs(old_pack, new_pack):
    """The UPDATE contract: WHAT CHANGED / WHAT DID NOT / OLD FAIR /
    NEW FAIR / OLD STATUS / NEW STATUS / WHY — structured, not prose."""
    if not isinstance(old_pack, dict) or not isinstance(new_pack, dict):
        return None
    old_rng = old_pack.get("nova_fair_range") or {}
    new_rng = new_pack.get("nova_fair_range") or {}
    old_mid = _mid(old_rng)
    new_mid = _mid(new_rng)
    changed, unchanged = [], []
    for key in ("personnel", "matchup", "environment", "situation",
                "regression"):
        if _blob(old_pack.get(key)) != _blob(new_pack.get(key)):
            changed.append(key)
        else:
            unchanged.append(key)
    fair_changed = old_mid is not None and new_mid is not None and abs(old_mid - new_mid) >= 0.25
    if fair_changed:
        changed.append("fair_range")
    else:
        unchanged.append("fair_range")
    if not changed:
        return None
    return {
        "updated_at": _iso(_now()),
        "what_changed": changed,
        "what_did_not": [u for u in unchanged if u not in changed],
        "old_fair": old_rng if old_rng else None,
        "new_fair": new_rng,
        "old_status": old_pack.get("status_at_write") or "",
        "new_status": new_pack.get("status_at_write") or "",
        "why": (new_pack.get("fair_price_reasoning") or [])[:3],
    }


def _mid(rng):
    try:
        return round((float(rng["low"]) + float(rng["high"])) / 2.0, 2)
    except (TypeError, ValueError, KeyError):
        return None


def _blob(v):
    return json.dumps(v, sort_keys=True, default=str) if v else ""


def render_update(diff):
    """Human-readable UPDATE block for the answer pack (structured diff
    stays in analysis_pack.update_diff; this is the chat rendering)."""
    if not diff:
        return None
    lines = [f"UPDATE — {diff['updated_at']}"]
    lines.append("WHAT CHANGED: " + (", ".join(diff["what_changed"]) or "nothing"))
    lines.append("WHAT DID NOT: " + (", ".join(diff["what_did_not"]) or "n/a"))
    if diff.get("old_fair") and diff.get("new_fair"):
        o, n = diff["old_fair"], diff["new_fair"]
        lines.append(f"OLD FAIR: {o.get('low')} to {o.get('high')}")
        lines.append(f"NEW FAIR: {n.get('low')} to {n.get('high')}")
    if diff.get("old_status") and diff.get("new_status"):
        lines.append(f"OLD STATUS: {diff['old_status']}")
        lines.append(f"NEW STATUS: {diff['new_status']}")
    if diff.get("why"):
        lines.append("WHY: " + " | ".join(str(w) for w in diff["why"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# ENTRY POINT (called from the read loop for the fixture game)
# --------------------------------------------------------------------------

def synthesize(game, baseline, context, log=lambda *a: None):
    """Full MODE 2 pass for one game. Returns (validated_mid, pack, notes)
    or (None, None, notes) on any failure — the deterministic baseline
    always stands when synthesis cannot produce a validated range."""
    notes = []
    prompt = build_prompt(game, context)
    resp = call_synthesis(prompt, log=log)
    if resp is None:
        return None, None, ["synthesis call failed — baseline stands"]
    text, _cits = _output_text(resp)
    pack = parse_pack(text)
    if pack is None:
        return None, None, ["synthesis output failed the pack contract — baseline stands"]
    pack["model"] = resp.get("model") or config.OPENAI_MODEL
    mid, vnotes = validate_range(pack, baseline)
    notes.extend(vnotes)
    if mid is None:
        pack["validation"] = "rejected: " + "; ".join(vnotes)
        return None, pack, notes        # pack kept for debugging, price unchanged
    pack["validation"] = "accepted"
    pack["baseline_true_line"] = baseline
    pack["effective_true_line"] = mid
    return mid, pack, notes
