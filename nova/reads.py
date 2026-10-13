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
