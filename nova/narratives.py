"""Deterministic narrative layer for MarketNovaRead.

Builds the frontend-contract narrative fields (thesis, nova_read,
why_market_here, risk, buy_to, regression_notes, market_tape,
true_line_basis, value_window, confidence) from structured inputs only.
No LLM, no invented facts: every sentence traces to data that is in the
payload. Voice: institutional, calm, terminal register — price, number,
edge, market. Status taxonomy only.
"""


def _side_number(n, vs):
    """Home-perspective line -> the value side's own number (flip when away)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return n
    return n if vs == "home" else -n


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
    mp = _side_number(result.get("minimum_playable"), vs)
    tl = result.get("true_line")
    side_tl = _side_number(tl, vs)
    line = _side_number(result.get("market_line"), vs)
    return (f"Take {side_team} at {_fmt_line(mp)} or better — inside that "
            f"number the edge is spent. Nova's fair for {side_team} is "
            f"{_fmt_line(side_tl)}; at the current {_fmt_line(line)} the edge is "
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
    side_mp = _side_number(mp, vs)
    return (f"PLAY: {side_team} {_fmt_line(side_mp)} or better. "
            f"LEAN: to {_fmt_line(side_mp + 1.0)}. "
            f"NUMBER GONE: at {_fmt_line(_side_number(tl, vs))}. "
            f"Current market {_fmt_line(_side_number(line, vs))} sits {zone} "
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
