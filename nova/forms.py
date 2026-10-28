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
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from nova import espn, matching

# Oct 28 2026: weekly sports were starving the total model — a 21-day
# NCAAF window is 3 games, so one bye left n=2 and the sample test
# (TOTAL_MIN_SAMPLE=3) rejected it, publishing market-anchor-only totals
# with a hard 0.0 edge. LAST_N still caps the actual sample at 5.
WINDOW_DAYS = {"nba": 28, "nfl": 45, "ncaaf": 45, "nhl": 21, "mlb": 21,
               "wnba": 21}
DEFAULT_WINDOW = 14
LAST_N = 5


def completed_games(league, window=DEFAULT_WINDOW, days=None):
    """ESPN completed (final) events for the last `window` days.
    `days` (optional) restricts fetches to known game-days — ESPN
    throttles rapid-fire scoreboard calls and a throttled day means a
    whole week of samples silently vanishing below the honesty gate."""
    games = []
    today = datetime.now(timezone.utc).date()
    if days:
        wanted = sorted(set(days))
    else:
        wanted = [today - timedelta(days=delta) for delta in range(window)]
    for d in wanted:
        data = None
        for attempt in range(4):          # ESPN throttles rapid fire —
            try:                          # pace + retry, never swallow a
                data = espn.scoreboard(league, d)   # whole game-day silently
                break
            except Exception:
                time.sleep(2.0 * (attempt + 1))
        if data is None:
            print(f"  form-backfill: {league} {d} fetch failed after retries")
            continue
        time.sleep(0.4)
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


def build_forms(games, abbr_by_id=None):
    """{official_abbr: chronological last-N rows} for both sides.
    abbr_by_id ({team_id: official_abbr}) translates ESPN scoreboard
    abbrs (OKLA) to official abbrs (OU) via the team-id join."""
    abbr_by_id = abbr_by_id or {}
    per_team = defaultdict(list)
    names = {}
    for rec in games:
        st = str(rec.get("start_time") or "")
        for tid_key, abbr_key, name_key in (
                ("home_team_id", "home_abbr", "home_name"),
                ("away_team_id", "away_abbr", "away_name")):
            tid = str(rec.get(tid_key) or "")
            nm = rec.get(name_key)
            key = (abbr_by_id.get(tid) if tid else None) \
                or (rec.get(abbr_key) if not abbr_by_id else None)
            if key and nm:
                names.setdefault(key, nm)
        for my_abbr, my_id, my_score, opp_abbr, opp_id, opp_score, home in (
                (rec.get("home_abbr"), rec.get("home_team_id"),
                 rec.get("home_score"),
                 rec.get("away_abbr"), rec.get("away_team_id"),
                 rec.get("away_score"), True),
                (rec.get("away_abbr"), rec.get("away_team_id"),
                 rec.get("away_score"),
                 rec.get("home_abbr"), rec.get("home_team_id"),
                 rec.get("home_score"), False)):
            my_key = abbr_by_id.get(str(my_id or "")) or my_abbr
            opp_key = abbr_by_id.get(str(opp_id or "")) or opp_abbr
            if not my_key:
                continue
            try:
                ms, os_ = float(my_score), float(opp_score)
            except (TypeError, ValueError):
                continue
            per_team[my_key].append({
                "date": st,
                "opponent": opp_key,
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


def _game_days(client, league, window):
    """Dates (UTC) on which the league actually played final games in
    the window, per the DB's own Game records — fetch only those."""
    days = set()
    today = datetime.now(timezone.utc).date()
    earliest = today - timedelta(days=window)
    for g in client.list_all("Game", fields=["id", "league_code", "status",
                                            "start_time"]):
        if str(g.get("league_code") or "").lower() != league:
            continue
        if g.get("status") != "final":
            continue
        st = str(g.get("start_time") or "")
        if not st:
            continue
        try:
            dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
        except ValueError:
            continue
        d = dt.astimezone(timezone.utc).date()
        if d >= earliest:
            days.add(d)
    return sorted(days)


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
        tinfo = espn.teams_info(league)   # official abbrs via id join
        win = (windows or {}).get(league) or WINDOW_DAYS.get(league, DEFAULT_WINDOW)
        try:
            gdays = _game_days(client, league, win)
        except Exception:
            gdays = None
        games = completed_games(league, win, days=gdays)
        forms, names = build_forms(games, abbr_by_id=tinfo.get("by_id"))
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


    # dedupe: keep one row per (league, canon abbr) — ESPN's two abbr
    # conventions and re-created rows can otherwise pile up duplicates
    try:
        _all = client.list_all("TeamForm", fields=["id", "team_abbr",
                                                   "league_code",
                                                   "last_5_json",
                                                   "last_updated"])
        _groups = {}
        for r in _all:
            _lg = str(r.get("league_code") or "").lower()
            _groups.setdefault(
                (_lg, matching.canon(r.get("team_abbr") or "", _lg)),
                []).append(r)
        _stale = []
        for _grp in _groups.values():
            if len(_grp) < 2:
                continue
            _grp.sort(key=lambda r: (len(json.loads(r.get("last_5_json")
                                                     or "[]")),
                                     r.get("last_updated") or ""),
                      reverse=True)
            _stale += [r["id"] for r in _grp[1:]]
        for rid in _stale:
            try:
                client.delete("TeamForm", rid)
            except Exception:
                pass
        if _stale:
            stats["_dedupe"] = (0, 0, 0, len(_stale))
    except Exception:
        pass
