"""TeamForm refresh from ESPN completed games.

TeamForm feeds the total-read pace model (nova/markets.py) and the
spread read's streak/recency telemetry. Rows go stale when nobody
writes them (NBA had zero rows Oct 16 2026 -> every total read fell
back to the market anchor at 0.0 edge). This module keeps them fresh:

  - tools/backfill_team_form.py  — one-off / manual backfill
  - nova/cycle.py                — daily self-heal refresh (UTC 00/12)

Both call refresh(): fetch ESPN completed games for the window, build
chronological last-5 samples per team, upsert keyed (abbr, league).
Idempotent, canon-aware, honest on missing data.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from nova import espn, matching

WINDOW_DAYS = {"nba": 21, "nfl": 14, "nhl": 14, "mlb": 14, "wnba": 14}
DEFAULT_WINDOW = 14
LAST_N = 5


def completed_games(league, window=DEFAULT_WINDOW):
    """ESPN completed (final) events for the last `window` days."""
    games = []
    today = datetime.now(timezone.utc).date()
    for delta in range(window):
        d = today - timedelta(days=delta)
        try:
            data = espn.scoreboard(league, d)
        except Exception:
            continue
        for ev in data.get("events", []):
            try:
                rec = espn.parse_event(ev)
            except Exception:
                continue
            if espn.normalize_status(rec.get("status"),
                                     rec.get("status_detail")) != "final":
                continue
            hs, as_ = rec.get("home_score"), rec.get("away_score")
            if hs is None or as_ is None:
                continue
            try:
                if float(hs) == float(as_):
                    continue        # tie/noise — skip
            except (TypeError, ValueError):
                continue
            games.append(rec)
    return games


def build_forms(games):
    """{team_abbr: chronological last-N rows} for both sides."""
    per_team = defaultdict(list)
    names = {}
    for rec in games:
        st = str(rec.get("start_time") or "")
        if rec.get("home_abbr") and rec.get("home_name"):
            names.setdefault(rec["home_abbr"], rec["home_name"])
        if rec.get("away_abbr") and rec.get("away_name"):
            names.setdefault(rec["away_abbr"], rec["away_name"])
        for my_abbr, my_score, opp_abbr, opp_score, home in (
                (rec.get("home_abbr"), rec.get("home_score"),
                 rec.get("away_abbr"), rec.get("away_score"), True),
                (rec.get("away_abbr"), rec.get("away_score"),
                 rec.get("home_abbr"), rec.get("home_score"), False)):
            if not my_abbr:
                continue
            try:
                ms, os_ = float(my_score), float(opp_score)
            except (TypeError, ValueError):
                continue
            per_team[my_abbr].append({
                "date": st,
                "opponent": opp_abbr,
                "score": int(ms),
                "opponent_score": int(os_),
                "home": home,
                "result": "W" if ms > os_ else "L",
            })
    forms = {}
    for abbr, rows in per_team.items():
        rows.sort(key=lambda r: str(r.get("date") or ""))
        forms[abbr] = rows[-LAST_N:]
    return forms, names


def refresh(client, leagues, windows=None):
    """Refresh TeamForm rows for the given ESPN leagues. Returns
    {league: (games, teams, updated, created)}. Idempotent."""
    windows = windows or {}
    existing = {}
    for r in client.list_all("TeamForm",
                             fields=["id", "team_abbr", "league_code"]):
        key = (str(r.get("team_abbr") or "").upper(),
               str(r.get("league_code") or "").lower())
        existing.setdefault(key, r)

    stats = {}
    for league in leagues:
        if league not in espn.config.ESPN_LEAGUES:
            continue
        games = completed_games(league, windows.get(league, DEFAULT_WINDOW))
        forms, names = build_forms(games)
        puts, creates = [], []
        now_iso = datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        for abbr, rows in forms.items():
            payload = {
                "team_abbr": abbr.upper(),
                "team_name": names.get(abbr) or abbr.upper(),
                "league_code": league,
                "last_5_json": json.dumps(rows),
                "last_updated": now_iso,
            }
            cur = existing.get((abbr.upper(), league))
            if cur is None:
                for (a, l), row in existing.items():
                    if l == league and matching.canon(a, league) == \
                            matching.canon(abbr.upper(), league):
                        cur = row
                        break
            if cur:
                puts.append((cur["id"], payload))
                existing[(cur.get("team_abbr") or abbr.upper()).upper(),
                         league] = cur
            else:
                existing[(abbr.upper(), league)] = payload
                creates.append(payload)
        if puts:
            client.bulk_put("TeamForm", puts)
        if creates:
            client.bulk_create("TeamForm", creates)
        stats[league] = (len(games), len(forms), len(puts), len(creates))
    return stats
