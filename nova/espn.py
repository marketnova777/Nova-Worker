"""ESPN scoreboard ingest — same matching rules as bridge v1.5, generalized.

Matching order:
  1. provider_game_id (ESPN event id) — exact identity
  2. fallback: both sides match on abbr OR name (token/containment), same
     league + kickoff proximity — only against games with NO
     provider_game_id (a game that already has one is a different ESPN
     event; doubleheaders must never re-link)
  3. one-side-only overlap = AMBIGUOUS: never create, log for manual link.
     Conservative by design — a missed create is recoverable, a duplicate
     is the disease this repo exists to prevent.

Terminal games (CANCELED / POSTPONED / SUSPENDED / DELAYED) are exempt from
score reconciliation. Home/away comes from competitors[].homeAway.
"""
import json
import urllib.request
from datetime import datetime

from nova import config
from nova import matching

ESPN_HEADERS = {"User-Agent": "python-urllib/3.11"}  # verified accepted


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers=ESPN_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def scoreboard(league, date):
    path = config.ESPN_LEAGUES[league]
    return fetch_json(f"{config.ESPN_BASE}/{path}/scoreboard?dates={date.strftime('%Y%m%d')}")


def parse_event(ev):
    comp = ev["competitions"][0]
    home = away = None
    for c in comp.get("competitors", []):
        side = {
            "abbr": (c.get("team") or {}).get("abbreviation"),
            "name": (c.get("team") or {}).get("displayName"),
            "score": c.get("score"),
        }
        if c.get("homeAway") == "home":
            home = side
        else:
            away = side
    status = ((ev.get("status") or {}).get("type") or {})
    odds0 = (comp.get("odds") or [None])[0]
    rec_odds = None
    if odds0:
        rec_odds = {
            "provider": ((odds0.get("provider") or {}).get("name") or "DraftKings"),
            # ESPN's "spread" is home-perspective (negative = home favored),
            # same convention as Nova's true line — verified Oct 2026.
            "spread": _num(odds0.get("spread")),
            "total": _num(odds0.get("overUnder")),
            "home_ml": _num((odds0.get("homeTeamOdds") or {}).get("moneyLine")),
            "away_ml": _num((odds0.get("awayTeamOdds") or {}).get("moneyLine")),
        }
    venue = comp.get("venue") or {}
    vaddr = venue.get("address") or {}
    probables = []
    for pr in comp.get("probables") or []:
        a = (pr or {}).get("athlete") or {}
        t = (pr or {}).get("team") or {}
        nm = a.get("displayName")
        if nm:
            probables.append({"name": nm,
                              "team_abbr": t.get("abbreviation"),
                              "note": a.get("shortComment") or None})
    return {
        "odds": rec_odds,
        "espn_id": ev.get("id"),
        "start_time": ev.get("date"),
        "status": status.get("name", "").lower(),
        "status_detail": status.get("description", ""),
        "home_abbr": home["abbr"] if home else None,
        "home_name": home["name"] if home else None,
        "home_score": home["score"] if home else None,
        "away_abbr": away["abbr"] if away else None,
        "away_name": away["name"] if away else None,
        "away_score": away["score"] if away else None,
        "venue": venue.get("fullName"),
        "venue_city": vaddr.get("city"),
        "venue_state": vaddr.get("state"),
        "venue_indoor": bool(venue.get("indoor")),
        "probables": probables or None,
    }


def normalize_status(status, status_detail):
    s = (status or "").lower()
    d = (status_detail or "").lower()
    both = s + " " + d
    if "cancel" in both:
        return "canceled"
    if "postpon" in both:
        return "postponed"
    if "suspend" in both:
        return "suspended"
    if "delay" in both:
        return "delayed"
    # FINAL before in-game checks: "Final/OT" must stay final, never live
    if s == "final" or "final" in both:
        return "final"
    # any in-game state (halftime, end of quarter/period/inning, OT...) is live
    if (s in ("inprogress", "in_progress") or "in progress" in both
            or "halftime" in both or "overtime" in both
            or any(w in both for w in ("end of 1st", "end of 2nd", "end of 3rd", "end of 4th"))):
        return "live"
    return "scheduled"


def teams_map(league):
    """{canon(name): abbr} for a league from the ESPN team list.
    Covers display + short display names. Returns {} on failure."""
    out = {}
    try:
        data = fetch_json(f"{config.ESPN_BASE}/"
                          f"{config.ESPN_LEAGUES[league]}/teams?limit=500")
    except Exception:
        return out
    for wrapped in ((data.get("sports") or [{}])[0]
                    .get("leagues", [{}])[0].get("teams", [])):
        t = wrapped.get("team", wrapped)
        abbr = t.get("abbreviation")
        if not abbr:
            continue
        for key in ("displayName", "shortDisplayName", "name"):
            v = t.get(key)
            if v:
                from nova import matching
                out[matching.canon(v, league)] = abbr.upper()
    return out


def collect_events(league, days):
    from datetime import timedelta
    events = []
    from datetime import timezone as _tz
    today = datetime.now(_tz.utc).date()
    back = int(getattr(config, "RECONCILE_BACK_DAYS", 5))
    for delta in range(-back, days + 1):
        d = today + timedelta(days=delta)
        try:
            data = scoreboard(league, d)
            events.extend(data.get("events", []))
        except Exception as e:
            print(f"  espn {league} {d}: fetch failed: {e}")
    return events


# ------------------------------------------------------------------
# side matching: DB values may be abbrs ("NY") or names ("Coastal Carolina")
# ------------------------------------------------------------------

def _name_match(db_val, espn_abbr, espn_name, league=""):
    """True if a DB side value plausibly denotes this ESPN team."""
    if not db_val:
        return False
    db_val = str(db_val).strip().lower().translate(matching._ACCENTS)
    if matching.canon(db_val, league) == matching.canon(espn_abbr, league):
        return True
    n = str(espn_name or "").strip().lower().translate(matching._ACCENTS)
    if not n:
        return False
    if db_val == n or db_val in n or n in db_val:
        return True
    dt = set(db_val.split())
    nt = set(n.split())
    if dt and dt.issubset(nt):
        return True
    return False


def _sides_match(g, rec, league=""):
    """(home_ok, away_ok) — which sides of this DB game match the ESPN record."""
    home_ok = (_name_match(g.get("home_team"), rec.get("home_abbr"), rec.get("home_name"), league)
               or _name_match(g.get("home_team_name"), rec.get("home_abbr"), rec.get("home_name"), league))
    away_ok = (_name_match(g.get("away_team"), rec.get("away_abbr"), rec.get("away_name"), league)
               or _name_match(g.get("away_team_name"), rec.get("away_abbr"), rec.get("away_name"), league))
    return home_ok, away_ok


def reconcile_links(virtual_games, espn_recs, league):
    """Link ESPN events to games and compute live status/score deltas.

    ESPN NEVER creates games (odds is the source of record). For unmatched
    events this only logs. For matched events it returns patches:
      updates: [(game, patch)] — game may be an existing record (has id)
               or a planned create (has __create index; the cycle merges
               the patch into the create payload)
      issues:  [str] anomalies worth logging
    """
    from nova.api import changed_fields

    updates, issues = [], []
    by_espn = {g.get("provider_game_id"): g for g in virtual_games if g.get("provider_game_id")}

    for rec in espn_recs:
        status = normalize_status(rec["status"], rec["status_detail"])
        terminal = status in config.TERMINAL_EXEMPT
        g = by_espn.get(rec["espn_id"])

        if g is None:
            g, ambiguous = _fallback_match(virtual_games, rec, league)
            if g is None:
                if ambiguous:
                    issues.append(
                        f"WARN ambiguous {rec.get('away_abbr')}@{rec.get('home_abbr')} "
                        f"({rec.get('espn_id')}) — one-side overlap, not linking")
                continue  # no match: ESPN-only game, not board material

        # Guard: never regress a final game, never resurrect terminal games.
        if g.get("id") and g.get("status") == "final" and status != "final":
            issues.append(f"skip regression: {rec['espn_id']} final -> {status}")
            continue
        if g.get("id") and str(g.get("status", "")).lower() in config.TERMINAL_EXEMPT and status != g.get("status"):
            issues.append(f"skip terminal change: {rec['espn_id']} {g.get('status')} -> {status}")
            continue

        desired = {
            "status": status,
            "home_score": None if terminal else _num(rec["home_score"]),
            "away_score": None if terminal else _num(rec["away_score"]),
        }
        tracked = ["status", "home_score", "away_score"]
        # start_time: compare PARSED datetimes — ESPN uses minute precision,
        # odds-api second precision; a string compare would rewrite every cycle
        if rec.get("start_time") and _parse_iso(rec["start_time"]) != _parse_iso(g.get("start_time")):
            desired["start_time"] = rec["start_time"]
            tracked.append("start_time")
        if rec.get("venue"):
            desired["venue"] = rec["venue"]
            tracked.append("venue")
        patch = changed_fields(g, desired, tracked)

        if not g.get("provider_game_id") and rec["espn_id"]:
            patch = patch or {}
            patch["provider_game_id"] = rec["espn_id"]

        if patch:
            updates.append((g, patch))

    return updates, issues


def _fallback_match(games, rec, league):
    """(match, ambiguous). Both sides must match to link; exactly one side
    matching is ambiguous (refuse to create, refuse to link)."""
    best, one_side = None, False
    for g in games:
        if g.get("provider_game_id"):
            continue
        if str(g.get("league_code", "")).upper() != league.upper():
            continue
        if not matching.same_day(g, rec.get("start_time")):
            continue
        home_ok, away_ok = _sides_match(g, rec, league)
        if home_ok and away_ok:
            if best is None or _dt_gap(g, rec) < _dt_gap(best, rec):
                best = g
        elif home_ok or away_ok:
            one_side = True
    if best is not None:
        return best, False
    return None, one_side


def _dt_gap(g, rec):
    try:
        a = datetime.fromisoformat(str(g.get("start_time", "")).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(rec.get("start_time", "")).replace("Z", "+00:00"))
        return abs((a - b).total_seconds())
    except (ValueError, TypeError):
        return float("inf")


def _parse_iso(v):
    from datetime import timezone as _tz
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt.replace(tzinfo=_tz.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
