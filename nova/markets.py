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

from nova import config, kernel

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
    "nfl": 13.2, "ncaaf": 16.5,
    "nba": 11.8, "ncaab": 11.0, "wnba": 10.5,
    "nhl": 2.15,
    "mlb": 3.0,
}
DEFAULT_SIGMA = 12.0

# shrinkage: weight of the MARKET anchor in the total blend (our
# recent-form model carries the remainder). Conservative by design.
TOTAL_MARKET_WEIGHT = 0.7
TOTAL_MIN_SAMPLE = 3          # games per side to use the form model
TOTAL_BLEND_CAP = 4.0         # max |true - market| the blend can produce

STATUS_MAP = {"PASS": "PASS", "MONITOR": "MONITOR", "LEAN": "LEAN",
              "PLAY": "PLAY", "STRONG_DISCREPANCY": "PLAY"}
STATUS_RANK = {"PASS": 0, "MONITOR": 1, "LEAN": 2, "PLAY": 3}

# fair-probability band where the normal margin model is trusted to
# carry action weight on ML reads
ML_TRUST_BAND = (0.15, 0.85)


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
    scored, allowed, n = 0.0, 0.0, 0
    for gme in games:
        try:
            s = float(gme.get("score"))
            a = float(gme.get("opponent_score"))
        except (TypeError, ValueError):
            continue
        scored += s
        allowed += a
        n += 1
    if n < TOTAL_MIN_SAMPLE:
        return None
    return scored / n, allowed / n, n


def compute_total_read(game, form, market_rows):
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
        blend = TOTAL_MARKET_WEIGHT * market_total + (1 - TOTAL_MARKET_WEIGHT) * est
        blend = min(market_total + TOTAL_BLEND_CAP,
                    max(market_total - TOTAL_BLEND_CAP, blend))
        true_total = round(blend, 1)
        basis = (f"{TOTAL_MARKET_WEIGHT:.0%} market anchor / "
                 f"{1 - TOTAL_MARKET_WEIGHT:.0%} recent-form pace "
                 f"({h[2]}-game samples, blend capped at {TOTAL_BLEND_CAP:.0f} pts)")

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
            f"Nova's true total is {true_total:.1f} against a market {market_total:.1f} — "
            f"{raw_edge:.1f} pts of {direction} lean."
            + (f" Juice costs {vig_penalty:.1f} of it." if vig_penalty else "")),
        "risk": ("Five-game scoring samples are noisy; the blend is capped at "
                 f"{TOTAL_BLEND_CAP:.0f} points for exactly that reason. Weather and "
                 "pace-shaping personnel news are NOT modeled in this read."),
        "telemetry": {
            "market": "total", "value_side": value_side,
            "vig_penalty": vig_penalty, "effective_edge": effective_edge,
            "hours_to_close": hours, "stage": stage,
            "model_basis": basis,
        },
    }


# ---------------------------------------------------------------------------
# MONEYLINES
# ---------------------------------------------------------------------------
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
    sigma = MARGIN_SIGMA.get(league_l, DEFAULT_SIGMA)
    # true spread is home-perspective (negative = home favored); expected
    # home margin = -spread; P(home wins) = Phi(margin / sigma)
    fair_home = 0.5 * (1 + math.erf((-true_spread / sigma) / math.sqrt(2)))

    edge_home = (fair_home - devig_home) * 100.0     # probability points
    value_side = "home" if edge_home > 0 else "away"
    raw_edge = round(abs(edge_home), 2)
    fair_side = fair_home if value_side == "home" else 1 - fair_home
    book_side = devig_home if value_side == "home" else 1 - devig_home
    fair_amer = _prob_to_american(fair_side)
    book_price = ml_row.get(f"{value_side}_price")

    # status from the kernel's moneyline thresholds (EV in probability
    # points); no creditable supporting modules -> capped at LEAN
    status = STATUS_MAP.get(kernel.classify_threshold(raw_edge, "moneyline"), "PASS")
    if status == "PLAY":
        status = "LEAN"      # PLAY_REQUIRES_SUPPORT: one-input model, no modules
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
                "raw_edge": raw_edge, "true_line": fair_amer,
                "market_line": book_price, "market_odds": book_price,
                "edge_unit": "PROBABILITY"}

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
