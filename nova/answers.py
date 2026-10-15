"""Local answer generation for Ask Nova — zero Base44 credits.

The worker owns ALL intelligence text. This module builds an "answer
pack" per read: a dict of question-intent -> answer string, derived
ONLY from fields the read already carries (status, true line, edge,
buy-to, invalidators, market psychology, tape, telemetry) plus optional
league headlines fetched online via nova.web (GitHub Actions network,
never a metered Base44 AI call).

Storage: serialized JSON on MarketNovaRead.thesis_json (a live-schema
string field that has always been null — now the canonical carrier for
the worker-written answer pack). The Ask Nova panel renders these
verbatim; nova/reads' composition is the fallback for stale reads.

Never-invent invariants (FASTESTPAYOUT): every sentence traces to a
field in the payload. AWAITING DATA answers say what is missing and
what unblocks it. Answers never recommend action past buy-to.
"""

import json

from nova import web


def _fmt_line(x):
    try:
        return f"{float(x):+.1f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(x, nd=1):
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def _team(game, side):
    return (game.get(f"{side}_team") or game.get(f"{side}_team_name")
            or side.upper())


def build_pack(game, result, narrative, news=None, context=None, research=None,
               meta=None):
    """Return the answer-pack dict for one read, or None if nothing
    useful can be said (all fields missing). AWAITING DATA gets honest
    per-intent answers, not generic filler."""
    status = result.get("status")
    away = _team(game, "away")
    home = _team(game, "home")
    matchup = f"{away} @ {home}"
    true_line = result.get("true_line")
    market_line = result.get("market_line")
    edge = result.get("edge_value")
    pack = {}

    awaiting = status == "AWAITING DATA"
    if awaiting:
        base = (f"AWAITING DATA for {matchup} — Nova does not publish a true "
                "line without the required inputs.")
        pack["true_line"] = base + (" Power ratings for one or both teams are "
                                    "missing; the read self-resolves when "
                                    "TeamRating data lands.")
        pack["should_i_bet"] = ("No. " + base + " Passing is a position — Nova "
                                "does not grade a market it cannot price.")
        pack["where_does_value_end"] = base
        pack["why_is_the_market_here"] = (base + " What the board prints "
                                         "without Nova context is a price, "
                                         "not a read.")
        pack["what_breaks_the_thesis"] = ("There is no thesis to break yet — "
                                          "the blocker is missing data, not a "
                                          "market condition.")
        pack["what_would_change_novas_mind"] = ("TeamRating rows for both "
                                                "teams, then a current spread "
                                                "print on the board.")
    else:
        thesis = narrative.get("thesis") or result.get("thesis")
        why = narrative.get("why_market_here") or result.get("why_market_here") \
            or result.get("market_psychology")
        risk = narrative.get("risk") or result.get("risk")
        buy_to = narrative.get("buy_to") or result.get("buy_to")
        trigger = result.get("next_trigger")
        tape = narrative.get("market_tape")
        true_basis = narrative.get("true_line_basis")
        reg = narrative.get("regression_notes")
        tl = _fmt_line(true_line)
        ml = _fmt_line(market_line)
        gap = _fmt_num(edge)

        pack["true_line"] = ((
            f"Nova's true spread for {matchup} is {tl} against the board's "
            f"{ml} — a {gap}-point gap on the value side. "
            + (true_basis or "")).strip())
        pack["should_i_bet"] = (
            f"Status {status}. " + (thesis or "")).strip()
        pack["where_does_value_end"] = (
            (buy_to or (f"Value ends where the edge reaches zero — Nova's "
                        f"fair is {tl}. ")) +
            (f" Trigger watch: {trigger}" if trigger else "")).strip()
        pack["why_is_the_market_here"] = ((why or "") + (" " + tape if tape else "")).strip()
        pack["what_breaks_the_thesis"] = (risk or "").strip()
        pack["what_would_change_novas_mind"] = (
            (f"Re-rates Nova when: {trigger}" if trigger else
             "A TeamRating update or a market move to the buy-to threshold.") +
            (f" Regression watch: {reg}" if reg else "")).strip()

    if news:
        lines = [f"- {n.get('headline')}" for n in news[:5] if n.get("headline")]
        if lines:
            pack["latest_news"] = (
                f"Recent {str(game.get('league_code') or '').upper()} headlines "
                "(league-level, fetched fresh this cycle — not team-specific):\n"
                + "\n".join(lines))
    # headline lookup failed or league unsupported — omit the intent
    # entirely rather than print stale filler

    # ---- team context: real injury / probable-starter / weather data.
    # context is None when the fetch failed — omit the intent entirely
    # (never print "no injuries" when Nova simply could not look).
    if context is not None:
        away = _team(game, "away")
        home = _team(game, "home")
        lines = []
        inj_a = context.get("injuries_away") or []
        inj_h = context.get("injuries_home") or []
        if inj_a or inj_h:
            lines.append("Injury report (ESPN, fetched this cycle):")
            if inj_a:
                lines.append(f"  {away}: " + "; ".join(inj_a[:6]))
            if inj_h:
                lines.append(f"  {home}: " + "; ".join(inj_h[:6]))
        prob = context.get("probables")
        if prob:
            lines.append(f"Probable starters (ESPN): {prob}")
        wx = (result.get("weather") or "").strip()
        if wx:
            lines.append(f"Weather: {wx}")
        if lines:
            lines.append("Context feeds the true line — it is not a bet "
                         "signal on its own.")
            pack["team_context"] = "\n".join(lines)

    # ---- analysis versioning chain (doctrine 34/79): immutable read
    # versions ride the same canonical carrier. meta is stamped by the
    # cycle from the deterministic input fingerprint.
    if meta:
        pack["meta"] = {k: meta.get(k) for k in
                        ("analysis_version", "supersedes_version",
                         "input_hash", "research_version", "created_at")
                        if meta.get(k) is not None}

    # ---- MODE 2 analysis pack: the deep-synthesis object the read
    # consumed. Structured (never flattened) so every surface renders the
    # same canonical intelligence. update_diff carries the UPDATE
    # contract (what changed / old-new fair / old-new status / why).
    ap = result.get("analysis_pack")
    if ap is not None:
        pack["analysis_pack"] = ap
        if ap.get("update_diff"):
            try:
                from nova.synthesis import render_update
                txt = render_update(ap["update_diff"])
                if txt:
                    pack["update"] = txt
            except Exception:
                pass

    # ---- live research: MODE 2 web findings (OpenAI web_search).
    # research is None when the layer is disabled or the game was not a
    # target this cycle — omit the intent entirely (never print filler).
    if research is not None:
        pack["live_research"] = research

    return pack or None


def pack_json(game, result, narrative, news=None, context=None, research=None,
                meta=None):
    """Serialized answer pack for MarketNovaRead.thesis_json, or None."""
    pack = build_pack(game, result, narrative, news, context, research,
                       meta=meta)
    if not pack:
        return None
    try:
        return json.dumps({"answer_pack": pack}, ensure_ascii=False)
    except Exception:
        return None
