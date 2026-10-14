"""Postgame grading — the learning loop (doctrine Parts XVIII, LXI-LXIII).

Nova prices a game, the market prices it, the game happens. This module
closes the loop: every final game with a spread read gets an honest,
deterministic grade written to MarketNovaRead.final_read_json:

  - calibration: |true_line - actual margin| vs |market_line - margin|
    (was Nova's independent fair closer to reality than the book's price?)
  - CLV: entry market line vs the closing snapshot on the value side
    (doctrine Part XVIII — performance is judged on beats-to-close,
    not wins/losses)
  - value result: did the value side cover (only graded for reads that
    implied a side: PLAY / LEAN / MONITOR)

Never-invent invariants apply: missing scores or odds yield nulls with
an explicit reason, never guesses. Grading is idempotent — a read whose
final_read_json already carries graded=true is never re-graded, and the
original thesis fields are never rewritten (Part LXI: freeze the thesis).
"""
import json
from datetime import datetime, timedelta, timezone


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _already_graded(read):
    frj = read.get("final_read_json")
    if isinstance(frj, str):
        try:
            frj = json.loads(frj)
        except (ValueError, TypeError):
            return False
    return isinstance(frj, dict) and frj.get("graded") is True


def _stamp(o):
    for k in ("snapshot_time", "created_date"):
        try:
            dt = datetime.fromisoformat(str(o.get(k, "")).replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except (ValueError, TypeError):
            continue
    return None


def closing_line(odds_rows, kickoff=None):
    """Latest spread snapshot at/before kickoff (home-perspective line);
    falls back to the latest snapshot at all. None when no snapshots."""
    rows = [o for o in odds_rows
            if str(o.get("market_type", "")).lower() == "spread"]
    if not rows:
        return None
    if kickoff is not None:
        pre = [o for o in rows
               if _stamp(o) is not None and _stamp(o) <= kickoff]
        if pre:
            rows = pre
    best, best_t = None, None
    for o in rows:
        t = _stamp(o)
        if best is None or (t is not None and (best_t is None or t > best_t)):
            best, best_t = o, t
    return _f(best.get("home_line")) if best else None


def grade_read(game, read, odds_rows, now=None):
    """Grade payload for one (final game, spread read) pair, or None when
    the inputs are missing (never a guessed grade)."""
    now = now or datetime.now(timezone.utc)
    hs, as_ = _f(game.get("home_score")), _f(game.get("away_score"))
    if hs is None or as_ is None:
        return None
    margin = round(hs - as_, 2)
    true_line = _f(read.get("true_line_value")
                   or read.get("marketnova_true_line"))
    entry = _f(read.get("market_line"))
    status = str(read.get("status") or "").upper().strip()
    payload = {"graded": True, "graded_at": now.isoformat(),
               "margin": margin, "status_at_entry": status or None}
    if true_line is None or entry is None:
        payload["notes"] = "missing true line or entry line — calibration skipped"
        return payload
    model_err = round(abs(margin - true_line), 2)
    market_err = round(abs(margin - entry), 2)
    payload["true_line"] = true_line
    payload["market_line_entry"] = entry
    payload["model_error"] = model_err
    payload["market_error"] = market_err
    payload["calibration_winner"] = (
        "TIE" if abs(model_err - market_err) < 0.25
        else ("MODEL" if model_err < market_err else "MARKET"))
    # value side result (only reads that implied a side)
    edge_dir = str(read.get("edge_direction") or "").upper()
    side = ("HOME" if "HOME" in edge_dir
            else "AWAY" if "AWAY" in edge_dir else None)
    payload["value_side"] = side
    if side and status in ("PLAY", "LEAN", "MONITOR"):
        threshold = -entry  # margin > threshold -> home covered
        cover = margin > threshold if side == "HOME" else margin < threshold
        payload["value_result"] = (
            "PUSH" if abs(margin - threshold) < 1e-9
            else ("COVERED" if cover else "LOST"))
    # CLV vs the closing snapshot
    kickoff = None
    try:
        kickoff = datetime.fromisoformat(
            str(game.get("start_time", "")).replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        kickoff = None
    close = closing_line(odds_rows, kickoff)
    if close is None:
        payload["clv_points"] = None
        payload["notes"] = (payload.get("notes") or "") + " no spread snapshots — CLV unavailable"
    else:
        payload["closing_line"] = close
        if side == "HOME":
            clv = entry - close
        elif side == "AWAY":
            clv = close - entry
        else:
            clv = None
        payload["clv_points"] = round(clv, 2) if clv is not None else None
    return payload


def grade_finals(games, reads, odds_by_game, now=None, window_days=5):
    """[(read_id, grade_payload)] for final games inside the reconcile
    window. Idempotent: reads already graded (or with a legacy
    final_read_json blob) are only re-graded when they are ungraded."""
    now = now or datetime.now(timezone.utc)
    reads_by_game = {}
    for r in reads:
        if str(r.get("market_type", "")).lower() != "spread":
            continue
        reads_by_game.setdefault(r.get("game_id"), []).append(r)
    out = []
    for g in games:
        if str(g.get("status", "")).lower() != "final":
            continue
        try:
            start = datetime.fromisoformat(
                str(g.get("start_time", "")).replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            start = None
        if start is None or not (timedelta(0) <= now - start <= timedelta(days=window_days)):
            continue
        for r in reads_by_game.get(g.get("id"), []):
            if _already_graded(r):
                continue
            payload = grade_read(g, r, odds_by_game.get(g.get("id")) or [],
                                 now=now)
            if payload:
                out.append((r["id"], payload))
    return out


def calibration_summary(graded):
    """Rolling self-knowledge for the log / Performance: how often has
    Nova's fair beaten the book's price, and what is the CLV record."""
    n = len(graded)
    if not n:
        return {"graded": 0}
    win = sum(1 for g in graded if g.get("calibration_winner") == "MODEL")
    tie = sum(1 for g in graded if g.get("calibration_winner") == "TIE")
    clvs = [g["clv_points"] for g in graded if g.get("clv_points") is not None]
    covers = [g["value_result"] for g in graded if g.get("value_result")]
    return {
        "graded": n,
        "model_closer": win,
        "tie": tie,
        "market_closer": n - win - tie,
        "model_closer_pct": round(100.0 * win / n, 1),
        "avg_clv_points": round(sum(clvs) / len(clvs), 2) if clvs else None,
        "clv_positive": sum(1 for x in clvs if x > 0),
        "clv_n": len(clvs),
        "value_record": {v: covers.count(v) for v in ("COVERED", "LOST", "PUSH")
                         if covers.count(v)},
    }
