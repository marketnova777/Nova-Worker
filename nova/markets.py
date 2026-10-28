"""Secondary market reads: TOTAL and MONEYLINE.

The spread read (nova/reads.py) stays the canonical anchor — one
research snapshot, one chain, now three market expressions
(doctrine Part LV: best expression; founder spec section on
multi-market reads).

Totals: an independent recent-form pace estimate is blended INTO the
market anchor with heavy shrinkage (the market carries most of the
information; our five-game scoring sample only tilts it). No form
sample -> the market total stands as fair and the read is an honest
PASS. The blend is capped so a noisy sample can never manufacture an
edge bigger than TOTAL_BLEND_CAP.

Moneylines: fair win probability derives from the kernel's true
spread via a normal margin model (league-specific sigma), then
compares to the DEVIGGED book probability — the edge is post-vig by
construction. Fair price is published in American odds; buy-to is the
fair price itself (any book price better than fair is value).

Every read ends in the standard taxonomy. No forced plays.
"""

import math
from statistics import NormalDist

from nova import config, kernel, trueline

_NORMAL = NormalDist()


def _erfinv(z):
    """Inverse error function via the normal quantile:
    erfinv(z) = sqrt(2) * Phi^-1((z+1)/2)."""
    return _NORMAL.inv_cdf((z + 1.0) / 2.0) / math.sqrt(2.0)


def _fmt_amer(price):
    """Render American odds without float noise (-150.0 -> '-150')."""
    try:
        p = float(price)
        return f"{int(p):+d}" if float(p).is_integer() else f"{p:+.1f}"
    except (TypeError, ValueError):
        return str(price)

# ---------------------------------------------------------------------------
# League margin sigma (approximate historical final-margin standard
# deviations; documented approximation, honest telemetry).
# ---------------------------------------------------------------------------
MARGIN_SIGMA = {
    # priors only — compute_ml_read calibrates per game from the market's
    # own spread<->ML pair and shrinks toward these (Oct 28 2026: NHL/MLB
    # were ~40% too tight, manufacturing +10-15% fake ML edges)
    "nfl": 13.2, "ncaaf": 16.5,
    "nba": 11.8, "ncaab": 11.0, "wnba": 10.5,
    "nhl": 3.30,
    "mlb": 5.00,
}
DEFAULT_SIGMA = 12.0

# shrinkage: weight of the MARKET anchor in the total blend (our
# recent-form model carries the remainder). Conservative by design.
# Oct 28 2026: the total model could not publish a real disagreement —
# a 70% market anchor with a 4-pt cap caps the edge at ~1.2 pts, below the
# 1.5 PLAY band, so every total read collapsed to PASS unless environment
# moved it. The market is still the accuracy anchor (it prices the league
# scoring level), but the recent-form/pace model now carries 45% so a
# genuine tempo mismatch is allowed to express itself.
TOTAL_MARKET_WEIGHT = 0.55
TOTAL_MIN_SAMPLE = 3          # games per side to use the form model
TOTAL_BLEND_CAP = 6.0         # max |true - market| the blend can produce
TOTAL_FULL_SAMPLE = 6         # games per side for full model weight

STATUS_MAP = {"PASS": "PASS", "MONITOR": "MONITOR", "LEAN": "PLAY",
              "PLAY": "PLAY", "STRONG_DISCREPANCY": "PLAY",
              # kernel gate statuses must land in the V3 taxonomy too
              "NO_MODEL": "AWAITING DATA",
              "INFORMATION_INCOMPLETE": "AWAITING DATA",
              "REPRICE_REQUIRED": "AWAITING DATA",
              "AWAITING DATA": "AWAITING DATA"}
STATUS_RANK = {"PASS": 0, "MONITOR": 1, "PLAY": 3}

# fair-probability band where the normal margin model is trusted to
# carry action weight on ML reads
ML_TRUST_BAND = (0.15, 0.85)
# a published ML edge (probability points) beyond this is a model
# artifact from the spread->ML conversion, not market disagreement
ML_EDGE_SANITY = 10.0


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def _odds_age_conf(market_row, base=90, floor=30):
    """Odds-age decay, same rule as the spread read."""
    from datetime import datetime, timezone
    try:
        cd = str((market_row or {}).get("created_date") or "")
        if not cd:
            return base
        odt = datetime.fromisoformat(cd.replace("Z", "+00:00"))
        if odt.tzinfo is None:
            odt = odt.replace(tzinfo=timezone.utc)
        age_min = max(0.0, (datetime.now(timezone.utc) - odt).total_seconds() / 60.0)
        return max(floor, base - int(age_min / 6))
    except (ValueError, TypeError):
        return base


def _hours_to_game(game):
    from datetime import datetime, timezone
    try:
        st = str(game.get("start_time") or "").replace("Z", "+00:00")
        dt = datetime.fromisoformat(st)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        if delta >= 0:
            return round(delta, 1), ("early" if delta > 48
                                     else ("mid" if delta >= 24 else "late"))
    except (ValueError, TypeError):
        pass
    return None, "unknown"


# ---------------------------------------------------------------------------
# TOTALS
# ---------------------------------------------------------------------------
def _form_scoring(form_row):
    """(avg_scored, avg_allowed, n) from a TeamForm last-5 sample, or
    None when the sample is too small. Honest on missing rows."""
    import json
    if not form_row:
        return None
    try:
        games = json.loads(form_row.get("last_5_json") or "[]")
    except (ValueError, TypeError):
        return None
    scored, allowed, n, wsum = 0.0, 0.0, 0, 0.0
    for gme in games:
        try:
            s = float(gme.get("score"))
            a = float(gme.get("opponent_score"))
        except (TypeError, ValueError):
            continue
        # recency weight: the first entry is the newest game in the sample
        w = 1.0 / (1.0 + 0.35 * n)
        scored += s * w
        allowed += a * w
        wsum += w
        n += 1
    if n < TOTAL_MIN_SAMPLE or wsum <= 0:
        return None
    return scored / wsum, allowed / wsum, n


def compute_total_read(game, form, market_rows, weather=None,
                       injury_details=None):
    """Build the TOTAL read result dict, or None when no total price
    exists (nothing to compare -> no record; same contract as spreads)."""
    total_row = (market_rows or {}).get("total")
    if not total_row or total_row.get("market_line") is None:
        return None
    try:
        market_total = float(total_row["market_line"])
    except (TypeError, ValueError):
        return None

    league_l = str(game.get("league_code") or game.get("sport") or "").lower()

    # ---- true total: recent-form pace estimate blended into the anchor ----
    h = _form_scoring((form or {}).get("home")) if form else None
    a = _form_scoring((form or {}).get("away")) if form else None
    basis = "market anchor (no scoring sample on one or both sides — market stands as fair)"
    true_total = market_total
    if h and a:
        est = (h[0] + a[1]) / 2.0 + (a[0] + h[1]) / 2.0
        # sample-size discipline: a 3-5 game early-season pace sample cannot
        # carry full weight (Oct 28 2026). The model's share scales linearly
        # to FULL_SAMPLE games, so thin samples lean on the market and the
        # basis text says so.
        n_min = min(h[2], a[2])
        model_w = (1 - TOTAL_MARKET_WEIGHT) * min(1.0, n_min / float(TOTAL_FULL_SAMPLE))
        blend = (1 - model_w) * market_total + model_w * est
        blend = min(market_total + TOTAL_BLEND_CAP,
                    max(market_total - TOTAL_BLEND_CAP, blend))
        true_total = round(blend, 1)
        basis = (f"{1 - model_w:.0%} market anchor / {model_w:.0%} "
                 f"recency-weighted pace model ({n_min}-game samples, "
                 f"blend capped at {TOTAL_BLEND_CAP:.0f} pts)")

    # game-reality construction (nova/trueline.py): environment + missing-QB
    # effects priced on the TOTAL — bounded, journaled, fail-open
    wx_d, wx_b = trueline.weather_total_delta(weather)
    inj_tot, inj_b = trueline.injury_total_delta(injury_details, league_l)
    env_delta = (wx_d or 0.0) + (inj_tot or 0.0)
    if env_delta:
        true_total = round(true_total + env_delta, 1)
        env_bits = []
        if wx_b and wx_b.get("notes"):
            env_bits.append("; ".join(wx_b["notes"]))
        if inj_b:
            env_bits.append(f"{inj_b['out_qbs']} OUT QB(s)")
        basis += f"; environment {env_delta:+.1f} ({', '.join(env_bits)})"

    raw_edge = round(abs(market_total - true_total), 2)
    if raw_edge > config.MAX_EDGE_POINTS:
        return {"status": "AWAITING DATA",
                "reason": (f"True total {true_total:.1f} vs market {market_total:.1f} "
                           "disagrees beyond the sanity bound — data conflict, not an edge."),
                "raw_edge": raw_edge, "true_line": true_total,
                "market_line": market_total}

    direction = "over" if true_total > market_total else "under"
    value_side = direction          # totals: the side IS over/under

    # vig test (Part XX): value-side juice beyond -110 costs edge
    side_price = None
    if direction == "over":
        side_price = total_row.get("over_price") or total_row.get("home_price")
    else:
        side_price = total_row.get("under_price") or total_row.get("away_price")
    vig_penalty = 0.0
    try:
        p = float(side_price)
        if p <= -110:
            vig_penalty = round(0.25 * (abs(p) - 110) / 10.0, 2)
    except (TypeError, ValueError):
        pass
    effective_edge = round(raw_edge - vig_penalty, 2)

    tp = kernel.TruePrice(spread=true_total, total=true_total,
                          projected_home=true_total / 2.0,
                          projected_away=true_total / 2.0,
                          calculated_before_market_comparison=True)
    decision = kernel.decide(true_price=tp, market_line=market_total,
                            market="pregame_total", direction=direction,
                            supporting_modules=[])
    status = STATUS_MAP.get(str(getattr(decision, "status", "PASS")), "PASS")
    # vig cap: published status is capped by the post-juice edge
    adj = STATUS_MAP.get(kernel.classify_threshold(effective_edge, "pregame_total"), "PASS")
    if STATUS_RANK.get(adj, 0) < STATUS_RANK.get(status, 0):
        status = adj

    mpp = float(getattr(decision, "minimum_playable_price", 0.0) or 0.0)
    playable_until = (f"{direction.upper()} {mpp:.1f} or better"
                      if direction == "over"
                      else f"UNDER {mpp:.1f} or better")
    hours, stage = _hours_to_game(game)
    conf = _odds_age_conf(total_row)

    return {
        "status": status,
        "market": "total",
        "true_line": true_total,
        "market_line": market_total,
        "raw_edge": raw_edge,
        "edge_value": raw_edge,
        "edge_unit": "POINTS",
        "edge_score_display": f"{raw_edge:.1f} pts",
        "edge_direction": "NOVA_FAVORS_" + direction.upper(),
        "value_side": value_side,
        "market_odds": None,
        "buy_to": playable_until,
        "minimum_playable": mpp,
        "playable_until": playable_until,
        "next_trigger": f"Re-run when the total approaches {mpp:.1f}.",
        "invalidators": [
            f"Total crosses the play threshold ({playable_until})",
            "TeamForm scoring sample materially changes the pace estimate",
            "Juice moves against the value side past -130",
        ],
        "data_confidence": conf,
        "confidence": conf,
        "true_line_basis": basis,
        "why_market_here": (
            f"The book posts {market_total:.1f}; Nova's blended pace model makes fair "
            f"{true_total:.1f}. The gap is a recent-form tilt on the market anchor, "
            "not a disagreement about team strength."),
        "thesis": (
            (f"Nova's true total is {true_total:.1f} against a market "
             f"{market_total:.1f} — no meaningful gap. Passing is a position."
             if raw_edge < 0.05 else
             f"Nova's true total is {true_total:.1f} against a market {market_total:.1f} — "
             f"{raw_edge:.1f} pts of {direction} lean.")
            + (f" Juice costs {vig_penalty:.1f} of it." if vig_penalty else "")),
        "risk": ("Five-game scoring samples are noisy; the blend is capped at "
                 f"{TOTAL_BLEND_CAP:.0f} points for exactly that reason. Environment "
                 "adjustments are capped on the total; unmodeled pace-shifting "
                 "personnel news (late scratches) can still move the game."),
        "telemetry": {
            "market": "total", "value_side": value_side,
            "vig_penalty": vig_penalty, "effective_edge": effective_edge,
            "hours_to_close": hours, "stage": stage,
            "model_basis": basis,
            "environment": {"weather": wx_b, "personnel_total": inj_b,
                            "env_delta": round(env_delta, 2)},
        },
    }


# ---------------------------------------------------------------------------
# MONEYLINES
# ---------------------------------------------------------------------------
def _norm_ppf(p):
    """Inverse standard normal CDF (Acklam rational approximation)."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p out of (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _american_to_prob(price):
    """American odds -> implied win probability (with vig)."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None
    return (abs(p) / (abs(p) + 100)) if p < 0 else (100 / (p + 100))


def _prob_to_american(prob):
    """Fair win probability -> fair American price (no vig)."""
    if prob is None or not (0.01 < prob < 0.99):
        return None
    if prob >= 0.5:
        return round(-100 * prob / (1 - prob))
    return round(100 * (1 - prob) / prob)


def compute_ml_read(game, true_spread, market_rows):
    """Build the MONEYLINE read from the kernel's true spread. None when
    no ML price exists or no true spread (AWAITING DATA spreads carry
    no honest probability)."""
    ml_row = (market_rows or {}).get("moneyline")
    if not ml_row:
        return None
    try:
        true_spread = float(true_spread)
    except (TypeError, ValueError):
        return None
    home_p = _american_to_prob(ml_row.get("home_price"))
    away_p = _american_to_prob(ml_row.get("away_price"))
    if home_p is None or away_p is None or (home_p + away_p) <= 0:
        return None
    devig_home = home_p / (home_p + away_p)

    league_l = str(game.get("league_code") or game.get("sport") or "").lower()
    prior_sigma = MARGIN_SIGMA.get(league_l, DEFAULT_SIGMA)

    # --- per-game margin sigma from THIS game's own spread<->ML pair ---
    # The market's ML is the market's own win-probability curve for its own
    # spread: invert it to recover the implied margin sigma, then shrink
    # toward the league prior. This removes the cross-sport calibration
    # error that produced impossible ML edges (Oct 28 2026).
    sigma = prior_sigma
    mkt_spread = None
    try:
        sp_row = (market_rows or {}).get("spread") or {}
        if sp_row.get("market_line") is not None:
            mkt_spread = float(sp_row["market_line"])
    except (TypeError, ValueError):
        mkt_spread = None
    ml_pair_conflict = None
    # derive only where the line/devig pair is informative: near pick'em
    # the implied sigma explodes and the derivation is noise
    if mkt_spread is not None and abs(mkt_spread) >= 1.0 and 0.22 <= devig_home <= 0.78:
        try:
            z = _norm_ppf(devig_home)
            margin = -mkt_spread            # home margin implied by the line
            if abs(z) > 1e-6 and margin / z > 0:
                sigma_game = margin / z     # positive implied sigma
                lo, hi = 0.50 * prior_sigma, 2.0 * prior_sigma
                if sigma_game < lo or sigma_game > hi:
                    # the game's own spread and ML say different things
                    # about its margin curve (different books/times) —
                    # a data conflict, never an edge
                    ml_pair_conflict = sigma_game
                else:
                    sigma = 0.7 * sigma_game + 0.3 * prior_sigma
        except (ValueError, ZeroDivisionError):
            pass
    if ml_pair_conflict is not None:
        return {"status": "AWAITING DATA",
                "reason": (f"Spread {mkt_spread:+.1f} and ML-implied curve "
                           f"(sigma {ml_pair_conflict:.1f} vs league "
                           f"{prior_sigma:.1f}) disagree — cross-market data "
                           "conflict, not an edge."),
                "raw_edge": None, "true_line": None,
                "market_line": None,
                "market_odds": ml_row.get("home_price"),
                "edge_unit": "PROBABILITY",
                "one_line_read": None,
                "telemetry": {"market": "moneyline", "conflict": "spread_ml_pair",
                              "implied_sigma": round(ml_pair_conflict, 2)},
                "invalidators": ["Spread and ML prices disagree about this game's curve"],
                "buy_to": None, "minimum_playable": None,
                "data_confidence": 0, "confidence": 0,
                "true_line_basis": "suppressed — spread/ML pair conflict",
                "why_market_here": "Suppressed: the game's own spread and ML imply "
                                   "different margin curves (cross-book/timestamp conflict).",
                "thesis": "No honest ML read at this pair — the two markets "
                          "contradict each other.",
                "risk": "Suppressed read: data conflict, not an edge.",
                "next_trigger": "Re-run when the spread and ML pairs agree.",
                "playable_until": None}
    # true spread is home-perspective (negative = home favored); expected
    # home margin = -spread; P(home wins) = Phi(margin / sigma)
    fair_home = 0.5 * (1 + math.erf((-true_spread / sigma) / math.sqrt(2)))

    edge_home = (fair_home - devig_home) * 100.0     # probability points
    value_side = "home" if edge_home > 0 else "away"
    raw_edge = round(abs(edge_home), 2)
    if raw_edge > ML_EDGE_SANITY:
        return {"status": "AWAITING DATA",
                "reason": (f"Spread-derived ML edge {raw_edge:.1f} pts is beyond the "
                           f"sanity bound ({ML_EDGE_SANITY:.0f}) — cross-market "
                           "conflict, not an edge."),
                "raw_edge": None, "true_line": None,
                "market_line": None,
                "market_odds": ml_row.get(f"{value_side}_price"),
                "edge_unit": "PROBABILITY",
                "one_line_read": None,
                "telemetry": {"market": "moneyline", "conflict": "edge_beyond_sanity",
                              "claimed_edge": raw_edge},
                "invalidators": [f"A spread-derived ML edge of {raw_edge:.1f} pts "
                                 "exceeds the model sanity bound"],
                "buy_to": None, "minimum_playable": None,
                "data_confidence": 0, "confidence": 0,
                "true_line_basis": "suppressed — cross-market conflict",
                "why_market_here": "Suppressed: the spread-derived probability and "
                                   "the book's ML disagree beyond the model bound.",
                "thesis": "No honest ML read at this pair — the spread and ML "
                          "markets conflict; nothing publishable.",
                "risk": "Suppressed read: data conflict, not an edge.",
                "next_trigger": "Re-run when the spread and ML pairs agree.",
                "playable_until": None}
    fair_side = fair_home if value_side == "home" else 1 - fair_home
    book_side = devig_home if value_side == "home" else 1 - devig_home
    fair_amer = _prob_to_american(fair_side)
    book_price = ml_row.get(f"{value_side}_price")

    # status from the kernel's moneyline thresholds (EV in probability
    # points); no creditable supporting modules -> capped at LEAN
    status = STATUS_MAP.get(kernel.classify_threshold(raw_edge, "moneyline"), "PASS")
    if status == "PLAY":
        status = "PLAY"      # one-input model, no modules: conviction noted, play stands (LEAN retired Oct 28)
    # tail-band honesty gate: the normal margin model is trustworthy near
    # pick'em and weakest in the tails — a 10% fair probability carries
    # more model error than the edge it claims. Outside the band the read
    # publishes (row exists) but never carries action weight.
    if fair_side < ML_TRUST_BAND[0] or fair_side > ML_TRUST_BAND[1]:
        status = "PASS"

    # sanity in MARGIN space: invert the devigged ML back to an implied
    # spread and compare it to Nova's true spread. Probability deltas are
    # naturally large near pick'em — the conflict test is whether the ML
    # market disagrees with the true line by more margin than the bound,
    # mirroring the spread read's signed-edge gate.
    margin_conflict = float("inf")
    implied_spread = None
    try:
        z = 2.0 * devig_home - 1.0
        if abs(z) < 1.0:
            implied_spread = -sigma * math.sqrt(2.0) * _erfinv(z)
            margin_conflict = abs(true_spread - implied_spread)
    except (ValueError, OverflowError):
        pass
    if margin_conflict > config.MAX_EDGE_POINTS:
        return {"status": "AWAITING DATA",
                "reason": (f"True spread {true_spread:+.1f} vs ML-implied "
                           f"{(implied_spread if implied_spread is not None else float('nan')):+.1f} "
                           f"disagree by {margin_conflict:.1f} — treated as a "
                           "cross-market data conflict, not an edge."),
                "raw_edge": None, "true_line": None,
                "market_line": None, "market_odds": book_price,
                "edge_unit": "PROBABILITY",
                "one_line_read": None,
                "telemetry": {"market": "moneyline", "conflict": "margin_space",
                              "margin_conflict": round(margin_conflict, 2)},
                "invalidators": [f"True spread and ML-implied spread disagree by "
                                 f"{margin_conflict:.1f} pts"],
                "buy_to": None, "minimum_playable": None,
                "data_confidence": 0, "confidence": 0,
                "true_line_basis": "suppressed — cross-market conflict",
                "why_market_here": "Suppressed: the ML-implied spread contradicts "
                                   "the true line beyond the bound.",
                "thesis": "No honest ML read — the markets conflict.",
                "risk": "Suppressed read: data conflict, not an edge.",
                "next_trigger": "Re-run when the pairs agree.",
                "playable_until": None}

    side_team = game.get(f"{value_side}_team") or value_side.upper()
    hours, stage = _hours_to_game(game)
    conf = _odds_age_conf(ml_row, base=80)

    return {
        "status": status,
        "market": "moneyline",
        "true_line": float(fair_amer) if fair_amer is not None else None,
        "market_line": book_price,
        "raw_edge": raw_edge,
        "edge_value": raw_edge,
        "edge_unit": "PROBABILITY",
        "edge_score_display": f"{raw_edge:.1f} EV pts",
        "edge_direction": "NOVA_FAVORS_" + ("HOME" if value_side == "home" else "AWAY"),
        "value_side": value_side,
        "market_odds": book_price,
        "buy_to": (f"{side_team} {_fmt_amer(fair_amer)} or better" if fair_amer is not None
                   else None),
        "minimum_playable": float(fair_amer) if fair_amer is not None else None,
        "playable_until": (f"{side_team} {_fmt_amer(fair_amer)} or better"
                           if fair_amer is not None else None),
        "next_trigger": "Re-run when the ML price or the true spread moves.",
        "invalidators": [
            f"Book price crosses the fair price ({_fmt_amer(fair_amer)})",
            "True spread moves with a TeamRating update",
            "Spread-based probability is wrong near pick'em in OT/tie leagues",
        ],
        "data_confidence": conf,
        "confidence": conf,
        "true_line_basis": (f"normal margin model on the kernel true spread "
                            f"({true_spread:+.1f}, sigma {sigma}) vs devigged book"),
        "why_market_here": (
            f"The devigged book prices {value_side} at {book_side:.1%}; the true "
            f"spread implies {fair_side:.1%}. The gap is the spread-to-ML "
            "conversion the market has not fully repriced."),
        "thesis": (
            f"Fair {side_team} ML is {_fmt_amer(fair_amer)}; the book posts {_fmt_amer(book_price)} — "
            f"{raw_edge:.1f} probability points of value."),
        "risk": ("ML fair price derives from the true SPREAD via a normal margin "
                 f"approximation (sigma {sigma}). OT/tie leagues and pick'em games "
                 "carry the widest model error."
                 + (" Fair probability is outside the trusted band — "
                    "this read never carries action weight."
                    if not (ML_TRUST_BAND[0] <= fair_side <= ML_TRUST_BAND[1]) else "")),
        "telemetry": {
            "market": "moneyline", "value_side": value_side,
            "fair_prob": round(fair_side, 4), "book_prob": round(book_side, 4),
            "sigma": sigma, "hours_to_close": hours, "stage": stage,
            "edge_is_post_vig": True,
        },
    }
