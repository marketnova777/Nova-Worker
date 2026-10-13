"""Nova Props engine: player-level projections from real usage + the
MarketNova brain. Zero LLM, zero integration cost — ESPN summaries are free.

Doctrine compliance (never-invent rules):
  - Baselines come ONLY from the player's most recent completed game (real
    boxscore) or an explicit league default labeled as such.
  - Nova ranges are projections, never priced against a book number we don't
    have. Status is MONITOR with an honest "book price not yet ingested"
    note until a props feed exists.
  - No player is invented: every prop traces to an ESPN athlete id.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from nova import matching

UA = "python-urllib/3.11"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".summary_cache")

ROSTER_CACHE = os.path.join(os.path.dirname(__file__), "..", ".roster_cache")
ROSTER_TTL_SECONDS = 7 * 24 * 3600  # rosters change mid-week at most


def fetch_roster(league, team_id):
    """{athlete_espn_id: position abbreviation} from ESPN team roster.
    Cached 7 days — roster churn is weekly."""
    if not team_id:
        return {}
    slug = SPORT_SLUGS.get(league)
    if not slug:
        return {}
    os.makedirs(ROSTER_CACHE, exist_ok=True)
    p = os.path.join(ROSTER_CACHE, f"{league}_{team_id}.json")
    now = time.time()
    if os.path.exists(p):
        try:
            blob = json.load(open(p))
            if now - blob.get("ts", 0) < ROSTER_TTL_SECONDS:
                return blob.get("positions") or {}
        except (ValueError, OSError):
            pass
    positions = {}
    try:
        r = _get(f"https://site.api.espn.com/apis/site/v2/sports/{slug}/teams/{team_id}/roster")
        raw = r.get("athletes")
        ath_list = []
        if isinstance(raw, list):  # groups: [{"position": "offense", "items": [...]}]
            for grp in raw:
                if isinstance(grp, dict):
                    ath_list += grp.get("items") or []
        elif isinstance(raw, dict):
            for _k, grp in raw.items():
                if isinstance(grp, dict):
                    ath_list += grp.get("items") or []
        for e in ath_list:
            if isinstance(e, dict) and e.get("id"):
                pos = (e.get("position") or {}).get("abbreviation")
                if pos:
                    positions[str(e["id"])] = pos
    except Exception:
        return {}
    try:
        json.dump({"ts": now, "positions": positions}, open(p, "w"))
    except OSError:
        pass
    return positions


LEAGUE_DEFAULTS = {
    # league: {prop_type: (baseline, range spread)}
    "nfl":  {"pass_yards": 225.0, "rush_yards": 70.0, "receiving_yards": 62.0},
    "ncaaf": {"pass_yards": 215.0, "rush_yards": 75.0, "receiving_yards": 55.0},
    "nba":  {"points": 18.0},
    "nhl":  {"saves": 26.0, "skater_points": 1.0},
}

SPORT_SLUGS = {
    "nfl": "football/nfl", "ncaaf": "football/college-football",
    "nba": "basketball/nba", "nhl": "hockey/nhl",
}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _cached(event_id):
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, f"{event_id}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except (ValueError, OSError):
            pass
    return None


def fetch_summary(league, event_id, force=False):
    """Completed-game summaries never change — cache on disk forever."""
    if not force:
        s = _cached(event_id)
        if s is not None:
            return s
    slug = SPORT_SLUGS.get(league)
    if not slug:
        return None
    try:
        s = _get(f"https://site.api.espn.com/apis/site/v2/sports/{slug}/summary?event={event_id}")
    except Exception:
        return None
    try:
        p = os.path.join(CACHE_DIR, f"{event_id}.json")
        json.dump(s, open(p, "w"))
    except OSError:
        pass
    return s


HEADSHOT_LEAGUES = {"nfl": "nfl", "ncaaf": "college-football",
                    "nba": "nba", "nhl": "nhl"}


def _athlete_row(athlete, league="nfl"):
    a = athlete.get("athlete") or {}
    head = (a.get("headshot") or {}).get("href")
    if not head and a.get("id"):
        head = (f"https://a.espncdn.com/i/headshots/"
                f"{HEADSHOT_LEAGUES.get(league, 'nfl')}/players/full/{a['id']}.png")
    return {
        "name": a.get("displayName"),
        "id": a.get("id"),
        "pos": (a.get("position") or {}).get("abbreviation"),
        "headshot": head,
        "stats": athlete.get("stats") or [],
    }


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def baselines_football(summary, league="nfl"):
    """([player dicts], {team_abbr: espn team id}) from a completed boxscore."""
    out, team_ids = [], {}
    for grp in (summary.get("boxscore") or {}).get("players") or []:
        gteam = grp.get("team") or {}
        team_ab = gteam.get("abbreviation")
        if team_ab and gteam.get("id"):
            team_ids[str(team_ab).upper()] = str(gteam["id"])
        for cat in grp.get("statistics") or []:
            cat_name = cat.get("name") or ""
            labels = cat.get("labels") or []
            for athlete in cat.get("athletes") or []:
                row = _athlete_row(athlete, league)
                if not row["name"] or not row["id"]:
                    continue
                stats = row["stats"]
                rec = {"team_abbr": team_ab, "cat": cat_name, "row": row}
                if cat_name == "passing" and "YDS" in labels:
                    rec["value"] = _num(stats[labels.index("YDS")]) if len(stats) > labels.index("YDS") else None
                    rec["prop_type"] = "pass_yards"
                elif cat_name == "rushing" and "YDS" in labels:
                    rec["value"] = _num(stats[labels.index("YDS")]) if len(stats) > labels.index("YDS") else None
                    rec["prop_type"] = "rush_yards"
                elif cat_name == "receiving" and "YDS" in labels:
                    rec["value"] = _num(stats[labels.index("YDS")]) if len(stats) > labels.index("YDS") else None
                    rec["prop_type"] = "receiving_yards"
                else:
                    continue
                if rec["value"] is not None:
                    out.append(rec)
    return out, team_ids


def baselines_basketball(summary, league="nba"):
    out, team_ids = [], {}
    for grp in (summary.get("boxscore") or {}).get("players") or []:
        gteam = grp.get("team") or {}
        team_ab = gteam.get("abbreviation")
        if team_ab and gteam.get("id"):
            team_ids[str(team_ab).upper()] = str(gteam["id"])
        for cat in grp.get("statistics") or []:
            labels = cat.get("labels") or []
            if "PTS" not in labels:
                continue
            for athlete in cat.get("athletes") or []:
                row = _athlete_row(athlete, league)
                if not row["name"] or not row["id"] or not row["stats"]:
                    continue
                if str(row["stats"][0]) in ("--", "DNP", "DND", "N/A"):
                    continue
                try:
                    pts = _num(row["stats"][labels.index("PTS")])
                except (IndexError, TypeError):
                    continue
                if pts is None:
                    continue
                out.append({"team_abbr": team_ab, "cat": "points",
                            "row": row, "value": pts, "prop_type": "points"})
    return out, team_ids


def baselines_hockey(summary, league="nhl"):
    out, team_ids = [], {}
    for grp in (summary.get("boxscore") or {}).get("players") or []:
        gteam = grp.get("team") or {}
        team_ab = gteam.get("abbreviation")
        if team_ab and gteam.get("id"):
            team_ids[str(team_ab).upper()] = str(gteam["id"])
        for cat in grp.get("statistics") or []:
            cat_name = cat.get("name") or ""
            labels = cat.get("labels") or []
            if cat_name == "goalies" and "SV" in labels:
                for athlete in cat.get("athletes") or []:
                    row = _athlete_row(athlete, league)
                    if not row["name"] or not row["id"]:
                        continue
                    try:
                        sv = _num(row["stats"][labels.index("SV")])
                    except (IndexError, TypeError):
                        continue
                    if sv:
                        out.append({"team_abbr": team_ab, "cat": "goalies",
                                    "row": row, "value": sv, "prop_type": "saves"})
            elif cat_name in ("skaters", "forwards", "defenses") and "G" in labels and "A" in labels:
                for athlete in cat.get("athletes") or []:
                    row = _athlete_row(athlete, league)
                    if not row["name"] or not row["id"]:
                        continue
                    try:
                        pts = (_num(row["stats"][labels.index("G")] or 0) or 0) + \
                              (_num(row["stats"][labels.index("A")] or 0) or 0)
                    except (IndexError, TypeError):
                        continue
                    out.append({"team_abbr": team_ab, "cat": "skaters",
                                "row": row, "value": pts, "prop_type": "skater_points"})
    return out, team_ids


def _pick(league, baselines, prop_type, team_abbr, n=1, exclude=()):
    """Top baseline players for a team/prop_type (excluding earlier picks)."""
    cands = [b for b in baselines
             if b["prop_type"] == prop_type
             and str(b["team_abbr"]).upper() == str(team_abbr).upper()
             and b["row"]["id"] not in exclude]
    cands.sort(key=lambda b: (b["value"] is None, -(b["value"] or 0)))
    return cands[:n]


def script_factor(league, prop_type, team_is_favorite, margin, role):
    """Game-script volume adjustment from Nova's true line (doctrine:
    game-state contamination, Part XXXII). Factors are deliberately modest —
    a single-game baseline is a wide prior, not a precise projection."""
    fav_mag = abs(margin)
    if league in ("nfl", "ncaaf"):
        if prop_type == "pass_yards":
            if team_is_favorite and fav_mag >= 7:
                return 0.90, "controlled script — leading side sheds pass volume late"
            if (not team_is_favorite) and fav_mag >= 7:
                return 1.12, "trailing script — pass volume lifts in chase states"
        elif prop_type == "rush_yards":
            if team_is_favorite and fav_mag >= 7:
                return 1.15, "leading script — rushing closes the game"
            if (not team_is_favorite) and fav_mag >= 7:
                return 0.80, "trailing script — ground game is abandoned"
        elif prop_type == "receiving_yards":
            if (not team_is_favorite) and fav_mag >= 7:
                return 1.10, "trailing script — pass-heavy second half"
    return 1.0, "competitive script — no volume distortion expected"


def build_props(game, league, true_line, market_total, last_game_by_team, ctx):
    """Prop payloads for one market game. Returns list of dicts matching the
    proposed Prop entity contract."""
    lg_defaults = LEAGUE_DEFAULTS.get(league)
    if not lg_defaults:
        return []
    summaries = (ctx or {}).get("summaries") or {}
    positions = (ctx or {}).get("positions") or {}
    home, away = str(game.get("home_team") or ""), str(game.get("away_team") or "")
    home_ab = game.get("home_team_abbr") or ""
    away_ab = game.get("away_team_abbr") or ""
    # true line is home-perspective; negative = home favored
    try:
        margin = float(true_line or 0.0)
    except (TypeError, ValueError):
        return []
    props = []
    used = set()

    def team_props(team_name, team_ab, is_home):
        is_fav = (margin < 0) == is_home
        last = last_game_by_team.get(matching.canon(team_name, league))
        baselines = summaries.get(last) if last else []
        if not baselines:
            return []
        specs = {
            "pass_yards": 1, "rush_yards": 1, "receiving_yards": 2,
            "points": 4, "saves": 1, "skater_points": 2,
        }.items()
        made = []
        for prop_type, n in specs:
            if prop_type not in lg_defaults:
                continue
            picks = _pick(league, baselines, prop_type, team_ab, n=n, exclude=used)
            for b in picks:
                used.add(b["row"]["id"])
                factor, note = script_factor(
                    league, prop_type, is_fav, margin, "value")
                base = b["value"]
                conf = 62
                source = "last completed game (ESPN boxscore)"
                if base is None:
                    base = lg_defaults[prop_type]
                    conf = 35
                    source = "league default — no recent boxscore for this player"
                mid = base * factor
                lo, hi = mid * 0.84, mid * 1.18
                q = lambda x: round(x / 5.0) * 5 if prop_type not in ("saves", "skater_points") else round(x)
                lo, hi = int(q(lo)), int(q(hi))
                if hi <= lo:
                    hi = lo + (1 if prop_type in ("saves", "skater_points") else 5)
                if abs(margin) < 1:
                    script_line = ("Nova projects this game effectively even "
                                    f"(true line {margin:+.1f})")
                else:
                    script_line = (f"Nova projects {team_name} "
                                   f"{'favored' if is_fav else 'trailing'} by {abs(margin):.1f}")
                read = (
                    f"{b['row']['name']} posted {base:g} {prop_type.replace('_', ' ')} in the last completed game. "
                    f"{script_line} — {note}. Fair range {lo}-{hi} "
                    f"(source: {source}). Book price not yet ingested — grade suspended."
                )
                made.append({
                    "game_id": game.get("id"),
                    "league_code": str(league).upper(),
                    "sport": league,
                    "player_name": b["row"]["name"],
                    "player_espn_id": str(b["row"]["id"]),
                    "position": positions.get(str(b["row"]["id"]))
                        or CAT_FALLBACK_POS.get(prop_type)
                        or b["row"]["pos"],
                    "team_abbr": team_ab,
                    "headshot_url": b["row"]["headshot"],
                    "prop_type": prop_type,
                    "stat_label": prop_type.replace("_", " "),
                    "baseline_last_game": float(base) if b["value"] is not None else None,
                    "baseline_source": source,
                    "nova_fair_low": lo,
                    "nova_fair_high": hi,
                    "nova_read": read,
                    "game_script_note": note,
                    "status": "MONITOR",
                    "data_confidence": conf,
                    "market_line": None,
                    "market_price": None,
                    "is_current": True,
                })
        return made

    props += team_props(home, home_ab, True)
    props += team_props(away, away_ab, False)
    return props


def team_last_game(games, league, now=None):
    """{team_canon: espn_event_id of latest completed game}."""
    now = now or datetime.now(timezone.utc)
    per_team = {}
    for g in games:
        lg = str(g.get("league_code") or g.get("sport") or "").lower()
        if league and lg != league:
            continue
        if str(g.get("status") or "").lower() not in ("final", "final/ot"):
            continue
        gid = g.get("provider_game_id")
        if not gid:
            continue
        try:
            st = datetime.fromisoformat(str(g.get("start_time") or "").replace("Z", "+00:00"))
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if st > now:
            continue
        for side in ("home", "away"):
            nm = g.get(f"{side}_team") or g.get(f"{side}_team_name")
            if not nm:
                continue
            key = matching.canon(str(nm), league)
            cur = per_team.get(key)
            if cur is None or st > cur[0]:
                per_team[key] = (st, gid)
    return {k: v[1] for k, v in per_team.items()}


CAT_FALLBACK_POS = {
    "pass_yards": "QB", "rush_yards": "RB", "receiving_yards": "WR/TE",
    "points": "G/F", "saves": "G", "skater_points": "F/D",
}


def load_baselines(league, last_game_by_team, log=print):
    """Fetch cached summaries + rosters. Returns
    {"summaries": {event_id: [players]}, "positions": {athlete_id: pos}}."""
    summaries, all_team_ids = {}, {}
    fetcher = {"nfl": baselines_football, "ncaaf": baselines_football,
               "nba": baselines_basketball, "nhl": baselines_hockey}.get(league)
    if not fetcher:
        return {"summaries": summaries, "positions": {}}
    seen_events = {}
    for event_id in set(last_game_by_team.values()):
        s = fetch_summary(league, event_id)
        if not s:
            seen_events[event_id] = ([], {})
            continue
        rows, tids = fetcher(s, league)
        seen_events[event_id] = (rows, tids)
        for ab, tid in tids.items():
            all_team_ids.setdefault(ab, tid)
        time.sleep(0.3)
    for event_id in set(last_game_by_team.values()):
        rows, _t = seen_events.get(event_id) or ([], {})
        summaries[event_id] = rows
    # roster positions per espn athlete id (cached 7 days per team)
    positions = {}
    for ab, tid in all_team_ids.items():
        for aid, pos in fetch_roster(league, tid).items():
            if pos:
                positions[aid] = pos
        time.sleep(0.15)
    log("props", f"{league}: {len(all_team_ids)} team ids, "
        f"{len(positions)} roster positions")
    return {"summaries": summaries, "positions": positions}
