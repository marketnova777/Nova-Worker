"""True-line game-reality construction (founder spec sections 3/37, Part XXX).

The true line is NOT ratings alone. This module layers bounded, deterministic
adjustments on the power-rating baseline using data the cycle already fetches:

  SPREAD construction
    injury_delta  — personnel impact (ESPN injury book, severity + position
                    weighted), priced per side, capped.
    rest_delta    — measured rest advantage from the schedule (deadband on
                    normal rest, capped; extra weight on back-to-backs in
                    nba/nhl).
    form_delta    — last-5 average margin differential (recency context the
                    ratings may lag), SMALL weight because markets price
                    streak info efficiently (Liu 2026) — it corrects rating
                    staleness, never chases momentum.

  TOTAL construction
    weather_delta — wind/rain/temperature effects (outdoor sports only;
                    enrich already skips indoor venues).
    injury_total   — scoring impact of missing QBs (football) priced on the
                    total.

Sign convention (matches kernel.build_true_price): a POSITIVE spread delta
makes the HOME side stronger. Fail-open: any missing input contributes zero
and never blocks a read. Every delta is capped and journaled in a breakdown
that rides the read telemetry — the UI can always show HOW the true line was
built. The kernel still owns every downstream rule (signed-edge gate, vig,
key numbers, thresholds, status, NUMBER GONE).
"""
from nova import config


def _clamp(v, cap):
    return max(-cap, min(cap, float(v)))


def _num(v, default=0.0):
    try:
        x = float(v)
        return x if x == x else default   # NaN guard
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- injuries

def _side_injury_impact(rows, league):
    """Raw personnel-impact points for one side from the injury book.
    The Injury store is written by several sources (worker + app-side
    automations), so the same player can appear multiple times; a player
    is ONE person, not N — dedupe by canonical name, keep the strongest
    (max-impact) copy."""
    def _impact(r):
        st = str(r.get("status") or "").lower()
        imp = _num(r.get("impact_rating"), 0.0)
        if "questionable" in st:
            imp *= 0.35
        elif "doubtful" in st:
            imp *= 0.60
        pos = str(r.get("position") or "").upper()
        if league in ("nfl", "ncaaf") and pos == "QB":
            imp += 1.5 if "out" in st else (0.8 if "doubtful" in st else 0.3)
        elif league == "nhl" and pos == "G":
            imp += 1.0 if "out" in st else 0.3
        return imp

    best = {}
    for r in rows or []:
        key = str(r.get("player_name") or "").strip().lower() \
            or str(r.get("position") or "")
        imp = _impact(r)
        if key not in best or imp > best[key][0]:
            best[key] = (imp, r)
    total = 0.0
    flagged = []
    for key, (imp, r) in best.items():
        if imp > 0:
            total += imp
            if len(flagged) < 4:
                st = str(r.get("status") or "").lower()
                flagged.append(f"{r.get('position') or '?'} "
                               f"{r.get('player_name') or '?'} ({st})")
    return min(total, config.TRUELINE_INJURY_SIDE_CAP), flagged


def injury_delta(injury_details, league):
    """(delta, breakdown). Home injuries weaken home -> negative delta."""
    det = injury_details or {}
    home_imp, home_flags = _side_injury_impact(det.get("home"), league)
    away_imp, away_flags = _side_injury_impact(det.get("away"), league)
    raw = config.TRUELINE_INJURY_SCALE * (away_imp - home_imp)
    delta = _clamp(raw, config.TRUELINE_INJURY_CAP)
    return delta, {
        "home_impact": round(home_imp, 2), "away_impact": round(away_imp, 2),
        "delta": round(delta, 2),
        "home_flagged": home_flags, "away_flagged": away_flags,
    }


def injury_total_delta(injury_details, league):
    """Scoring-environment impact on the TOTAL: missing QBs (football)."""
    if league not in ("nfl", "ncaaf"):
        return 0.0, None
    det = injury_details or {}
    n_qb = 0
    for side in ("home", "away"):
        for r in det.get(side) or []:
            st = str(r.get("status") or "").lower()
            if str(r.get("position") or "").upper() == "QB" and "out" in st:
                n_qb += 1
    if not n_qb:
        return 0.0, None
    return max(-n_qb * config.TRUELINE_QB_TOTAL_POINTS, -config.TRUELINE_QB_TOTAL_CAP), {
        "out_qbs": n_qb, "delta": -min(n_qb, 2) * config.TRUELINE_QB_TOTAL_POINTS,
    }


# ------------------------------------------------------------------ rest

def rest_delta(rest, league):
    """(delta, breakdown). More rest than opponent = stronger (deadbanded)."""
    r = rest or {}
    h, a = r.get("home"), r.get("away")
    if h is None or a is None:
        return 0.0, None
    diff = _num(h) - _num(a)
    # a back-to-back (0 rest) in a daily league is never "normal rest" —
    # the deadband does not apply to the fatigue side
    b2b = league in ("nba", "nhl") and (_num(h) == 0 or _num(a) == 0)
    if -1 <= diff <= 1 and not b2b:
        return 0.0, None   # both on normal rest: no adjustment
    raw = config.TRUELINE_REST_SCALE * (diff if b2b else
                                        (diff - 1 if diff > 1 else diff + 1))
    delta = _clamp(raw, config.TRUELINE_REST_CAP_B2B if b2b
                   else config.TRUELINE_REST_CAP)
    return delta, {"home_rest": h, "away_rest": a, "delta": round(delta, 2)}


# ------------------------------------------------------------------ form

def _avg_margin(form_row):
    """Mean margin from the last-5 json; None under the 3-game minimum."""
    import json as _json
    raw = form_row.get("last_5_json")
    if not raw:
        return None
    try:
        rows = _json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if not isinstance(rows, list) or len(rows) < config.TRUELINE_FORM_MIN_GAMES:
        return None
    ms = [_num(r.get("score")) - _num(r.get("opponent_score")) for r in rows]
    return sum(ms) / len(ms)


def form_delta(form):
    """(delta, breakdown). Small recency corrector — never a momentum chase."""
    f = form or {}
    hm, am = _avg_margin(f.get("home") or {}), _avg_margin(f.get("away") or {})
    if hm is None or am is None:
        return 0.0, None
    raw = config.TRUELINE_FORM_SCALE * (hm - am)
    delta = _clamp(raw, config.TRUELINE_FORM_CAP)
    return delta, {"home_avg_margin_l5": round(hm, 1),
                   "away_avg_margin_l5": round(am, 1),
                   "delta": round(delta, 2)}


# --------------------------------------------------------------- weather

def weather_total_delta(weather):
    """(delta, breakdown). Environment priced on the TOTAL only — honest
    because wind/rain direction on the SPREAD is matchup-specific."""
    if not weather:
        return 0.0, None
    wind = _num(weather.get("wind_speed_mph"))
    rain = _num(weather.get("precipitation_pct"))
    temp = _num(weather.get("temp_f"))
    d, notes = 0.0, []
    if wind >= 25:
        d -= 2.0; notes.append(f"heavy wind {wind:.0f} mph")
    elif wind >= 18:
        d -= 1.0; notes.append(f"wind {wind:.0f} mph")
    if rain >= 65:
        d -= 1.0; notes.append(f"rain risk {rain:.0f}%")
    if temp >= 92:
        d += 0.8; notes.append(f"heat {temp:.0f}F")
    elif temp <= 32:
        d -= 0.5; notes.append(f"cold {temp:.0f}F")
    if not notes:
        return 0.0, None
    d = max(d, config.TRUELINE_WEATHER_TOTAL_MIN)
    return d, {"notes": notes, "delta": round(d, 2)}


# -------------------------------------------------------- full construction

def construction(league, form=None, rest=None, injury_details=None,
                 weather=None):
    """The full game-reality stack. Returns deltas + journal for telemetry."""
    inj_d, inj_b = injury_delta(injury_details, league)
    rest_d, rest_b = rest_delta(rest, league)
    form_d, form_b = form_delta(form)
    wx_d, wx_b = weather_total_delta(weather)
    tot_inj_d, tot_inj_b = injury_total_delta(injury_details, league)

    spread_stack = round(inj_d + rest_d + form_d, 2)
    total_stack = round((wx_d or 0.0) + (tot_inj_d or 0.0), 2)

    active = [b for b in (inj_b if (inj_b or {}).get("delta") else None,
                          rest_b, form_b) if b]
    return {
        "injury_delta": inj_d,
        "rest_delta": rest_d,
        "form_delta": form_d,
        "weather_delta": (wx_d or 0.0) + (tot_inj_d or 0.0),
        "spread_delta": spread_stack,
        "total_delta": total_stack,
        "breakdown": {
            "injury": inj_b, "rest": rest_b, "form": form_b,
            "weather": wx_b, "injury_total": tot_inj_b,
            "active_modules": [b.get("delta") for b in []] or
                             [k for k, v in (("injury", (inj_b or {}).get("delta")),
                                             ("rest", (rest_b or {}).get("delta")),
                                             ("form", (form_b or {}).get("delta")),
                                             ("weather", (wx_b or {}).get("delta")))
                              if v],
            "spread_stack": spread_stack,
            "total_stack": total_stack,
            "note": ("Game-reality construction: ratings baseline adjusted by "
                     "personnel, schedule and form (bounded, fail-open); "
                     "environment priced on the total."),
        },
    }
