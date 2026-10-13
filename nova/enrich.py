"""Context enrichment: free data sources, zero LLM, zero integration-credit cost.

Fills the Game Detail page's data contract with honest, deterministic data:
  - WeatherSnapshot  (open-meteo, geocoded from the ESPN venue city)
  - Injury           (ESPN injuries endpoint, window teams only)
  - TravelContext    (rest days / back-to-back, computed from the schedule)
  - Analysis         (situational / form / movement context from data Nova
                      already holds — never invented)

Doctrine: nothing here may invent a number, a source, or a claim. Every
payload field traces to ESPN, open-meteo, or Nova's own ratings/odds.
"""
import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from nova import matching

UA = "python-urllib/3.11"
GEO_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", ".weather_geo.json")

WMO_CODES = {
    0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing Fog", 51: "Light Drizzle", 53: "Drizzle",
    55: "Heavy Drizzle", 61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Light Snow", 73: "Snow", 75: "Heavy Snow", 80: "Rain Showers",
    81: "Rain Showers", 82: "Violent Showers", 95: "Thunderstorms",
    96: "Thunderstorms/Hail", 99: "Severe Thunderstorms",
}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _compass(deg):
    try:
        d = float(deg) % 360
    except (TypeError, ValueError):
        return ""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((d + 11.25) // 22.5) % 16]


def _geo_cache():
    if os.path.exists(GEO_CACHE_PATH):
        try:
            return json.load(open(GEO_CACHE_PATH))
        except (ValueError, OSError):
            pass
    return {}


def _geocode(city, state):
    """City-level geocode with an on-disk cache (venues repeat weekly)."""
    if not city:
        return None
    key = f"{city}|{state or ''}".lower()
    cache = _geo_cache()
    if key in cache:
        return cache[key]
    q = urllib.parse.quote(f"{city}, {state}" if state else city)
    try:
        g = _get(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1")
        r = (g.get("results") or [{}])[0]
        if r.get("latitude") is None:
            cache[key] = None
            return None
        cache[key] = {"lat": r["latitude"], "lon": r["longitude"]}
    except Exception:
        return None
    try:
        json.dump(cache, open(GEO_CACHE_PATH, "w"))
    except OSError:
        pass
    return cache[key]


def weather_payloads(games_with_venue, now=None):
    """WeatherSnapshot payloads for outdoor games in the window.

    games_with_venue: [{game_id, league_code, venue, city, state, indoor,
                        start_time}] — venue facts come from the ESPN event.
    Returns {game_id: payload}.
    """
    out = {}
    now = now or datetime.now(timezone.utc)
    for g in games_with_venue:
        if g.get("indoor") or not g.get("city"):
            continue
        if g.get("league_code", "").lower() in ("nhl", "nba", "wnba", "ncaab"):
            continue    # indoor sports — weather is not a factor
        try:
            start = datetime.fromisoformat(str(g.get("start_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start < now:
            continue
        if start - now > __import__("datetime").timedelta(days=3):
            continue    # open-meteo hourly horizon
        geo = _geocode(g.get("city"), g.get("state"))
        if not geo:
            continue
        try:
            f = _get(
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={geo['lat']}&longitude={geo['lon']}"
                "&hourly=temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation_probability,wind_speed_10m,wind_direction_10m,weather_code"
                "&temperature_unit=fahrenheit&wind_speed_unit=mph"
                "&forecast_days=4&timezone=auto")
        except Exception:
            continue
        h = f.get("hourly") or {}
        times = h.get("time") or []
        if not times:
            continue
        # hour bucket = game start in venue-local time
        local = start.astimezone()  # not reliable; use string match instead
        want = start.strftime("%Y-%m-%dT%H")
        idx = None
        for i, t in enumerate(times):
            if t.startswith(want):
                idx = i
                break
        if idx is None:
            # fall back: closest hour to start (offsets handled by timezone=auto
            # via date window)
            cands = [i for i, t in enumerate(times) if t[:10] == want[:10]]
            if not cands:
                continue
            idx = cands[min(range(len(cands)), key=lambda k: abs(k - (start.hour + 0)))]
        temp = h["temperature_2m"][idx]
        feels = h["apparent_temperature"][idx]
        rh = h["relative_humidity_2m"][idx]
        rain = h["precipitation_probability"][idx]
        wind = h["wind_speed_10m"][idx]
        wdir = h["wind_direction_10m"][idx]
        code = h["weather_code"][idx]
        cond = WMO_CODES.get(code, "Mixed")
        wind_impact = ("significant" if wind >= 18 else
                       "moderate" if wind >= 10 else "minimal")
        if wind >= 18:
            read = "wind-impacted"
            rationale = (f"Projected {wind:.0f} mph winds — passing, deep balls and "
                         "kicking carry measurable risk; monitor for number impact.")
        elif (rain or 0) >= 60:
            read = "wet-ball-impacted"
            rationale = (f"{rain:.0f}% precipitation probability — wet-ball conditions "
                         "slow pace and compress scoring; totals and passing props adjust.")
        else:
            read = "neutral"
            rationale = f"{cond}, {wind:.0f} mph wind — conditions are not expected to move the number."
        out[g["game_id"]] = {
            "game_id": g["game_id"],
            "league_code": str(g.get("league_code") or "").lower(),
            "venue": g.get("venue") or "Unknown",
            "roof_type": "outdoor",
            "roof_status": "open",
            "temp_f": round(float(temp), 1),
            "feels_like_f": round(float(feels), 1),
            "humidity_pct": round(float(rh), 1),
            "precipitation_pct": round(float(rain or 0), 1),
            "wind_speed_mph": round(float(wind), 1),
            "wind_direction_deg": round(float(wdir), 1),
            "wind_direction_text": _compass(wdir),
            "conditions": cond,
            "wind_impact": wind_impact,
            "weather_read": read,
            "weather_read_rationale": rationale,
            "is_stale": False,
            "fetched_at": datetime.now(timezone.utc).isoformat(
                timespec="milliseconds").replace("+00:00", "Z"),
        }
        time.sleep(0.1)     # polite client
    return out


SEVERITY = {
    "out for season": ("critical", 5.0), "ir": ("critical", 5.0),
    "out indefinitely": ("critical", 4.0), "out": ("high", 4.0),
    "doubtful": ("moderate", 2.5), "questionable": ("moderate", 2.0),
}
INJURY_STATUS_OK = {"out", "out for season", "out indefinitely", "doubtful",
                    "questionable", "ir"}


def injury_payloads(league, espn_slug, window_teams, max_per_team=14):
    """Injury payloads for the teams actually playing in the window.

    window_teams: {display_name_lower: team_abbr} from the ESPN slate.
    Returns list of payloads (create-or-update matched upstream by key).
    """
    try:
        d = _get(f"https://site.api.espn.com/apis/site/v2/sports/{espn_slug}/injuries")
    except Exception:
        return []
    out = []
    for group in d.get("injuries") or []:
        gname = str(group.get("displayName") or "").lower()
        abbr = None
        for tname, tabbr in window_teams.items():
            if matching.canon(gname, league) == matching.canon(tname, league) \
               or gname.split()[0] == tname.split()[0]:
                abbr = tabbr
                break
        if not abbr:
            continue
        n = 0
        for item in (group.get("injuries") or []):
            if n >= max_per_team:
                break
            status_raw = str(item.get("status") or "").strip()
            status_l = status_raw.lower()
            if status_l not in INJURY_STATUS_OK:
                continue
            a = item.get("athlete") or {}
            name = a.get("displayName")
            if not name:
                continue
            pos = (a.get("position") or {}).get("abbreviation")
            det = item.get("details") or {}
            itype = " ".join(x for x in [det.get("type"), det.get("detail")] if x)
            sev, impact = SEVERITY.get(status_l, ("moderate", 2.0))
            out.append({
                "league_code": league,
                "source_provider": "ESPN",
                "player_name": name,
                "position": pos,
                "team_abbr": abbr,
                "team_name": str(group.get("displayName") or ""),
                "status": status_raw.title() if status_l in ("out", "doubtful", "questionable") else status_raw.title(),
                "severity": sev,
                "impact_rating": impact,
                "injury_type": itype or None,
                "is_sample": False,
            })
            n += 1
    return out


def rest_map(games, league):
    """{team_canon: [(start_dt, venue), ...]} sorted ascending, per league."""
    from nova import matching as M
    per_team = {}
    for g in games:
        lg = str(g.get("league_code") or g.get("sport") or "").lower()
        if league and lg != league:
            continue
        try:
            st = datetime.fromisoformat(str(g.get("start_time") or "").replace("Z", "+00:00"))
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        for side in ("home", "away"):
            nm = g.get(f"{side}_team") or g.get(f"{side}_team_name")
            if not nm:
                continue
            key = M.canon(str(nm), league)
            per_team.setdefault(key, []).append((st, g.get("venue")))
    for k in per_team:
        per_team[k].sort(key=lambda x: x[0])
    return per_team


def days_rest(per_team, team_name, before, league=""):
    """Days since the team's previous game (schedule-based, honest)."""
    from nova import matching as M
    key = M.canon(str(team_name or ""), league)
    hist = per_team.get(key) or []
    prev = [(st, v) for st, v in hist if st < before]
    if not prev:
        return None, None
    return (before - prev[-1][0]).days, prev[-1][1]


def travel_payload(game, per_team, league):
    """TravelContext payload for the AWAY team of a game."""
    try:
        start = datetime.fromisoformat(str(game.get("start_time") or "").replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    rest, prev_venue = days_rest(per_team, game.get("away_team"), start, league)
    if rest is None:
        return None
    abbr = game.get("away_team_abbr") or game.get("away_team")
    return {
        "game_id": game.get("id"),
        "league_code": league,
        "team_abbr": abbr,
        "days_rest": int(rest),
        "is_back_to_back": bool(rest <= 1),
        "previous_venue": prev_venue,
        "current_venue": game.get("venue"),
        "updated_at": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"),
    }


def movement_context(rows):
    """Factual open-vs-current movement from a game's odds rows."""
    spread = [r for r in rows if str(r.get("market_type")).lower() == "spread"]
    if len(spread) < 2:
        return None
    def cd(r):
        try:
            return datetime.fromisoformat(str(r.get("created_date")).replace("Z", "+00:00"))
        except ValueError:
            return None
    dated = [(cd(r), r) for r in spread if cd(r) is not None]
    if len(dated) < 2:
        return None
    dated.sort(key=lambda x: x[0])
    opn, cur = dated[0][1], dated[-1][1]
    ol, cl = opn.get("home_line"), cur.get("home_line")
    if ol is None or cl is None:
        return None
    try:
        ol, cl = float(ol), float(cl)
    except (TypeError, ValueError):
        return None
    if ol == cl:
        return None
    direction = "toward_home" if cl < ol else "toward_away"
    return {
        "open_line": ol, "current_line": cl, "direction": direction,
        "n_snapshots": len(dated),
        "detail": f"Spread opened {ol:+.1f}, now {cl:+.1f} "
                  f"({abs(cl - ol):.1f} toward {'home' if direction == 'toward_home' else 'away'} "
                  f"across {len(dated)} snapshots)",
    }


def analysis_payload(game, result, form_pair, movement, weather, rest_ctx):
    """Analysis record: situational / form / movement context for the
    game page's matchup + situation layers. Only factual fields."""
    league = str(game.get("league_code") or game.get("sport") or "").lower()
    bits = []
    if rest_ctx and rest_ctx.get("away") is not None and rest_ctx.get("home") is not None:
        bits.append(f"Away is on {rest_ctx['away']} days rest vs {rest_ctx['home']} for home"
                    + (" (back-to-back for the road side)" if rest_ctx["away"] <= 1 else ""))
    def _fmt(side):
        if not form_pair or not form_pair.get(side):
            return None
        rows = form_pair[side].get("last_5_json") or []
        try:
            rows = json.loads(rows) if isinstance(rows, str) else rows
        except ValueError:
            return None
        if not isinstance(rows, list) or not rows:
            return None
        wl = "".join("W" if str(r.get("result", "")).upper().startswith("W") else "L" for r in rows[-5:])
        try:
            marg = sum(float(r.get("score", 0)) - float(r.get("opponent_score", 0)) for r in rows[-5:]) / len(rows[-5:])
        except (TypeError, ValueError):
            return wl
        return f"{wl} (avg margin {marg:+.1f})"
    away_f, home_f = _fmt("away"), _fmt("home")
    form_line = []
    if home_f:
        form_line.append(f"Home enters {home_f}")
    if away_f:
        form_line.append(f"away enters {away_f}")
    situational = "; ".join(bits)
    h2h = ("Head-to-head series data is not ingested — recent-form comparison: "
           + "; ".join(form_line)) if form_line else \
          "Recent-form data not yet available for this matchup."
    sharp = []
    if movement:
        sharp.append({"type": "line_move", "detail": movement["detail"],
                      "claim": "observed movement — causation unconfirmed"})
    gs_tempo = result.get("game_script_tempo")
    gs_notes = result.get("game_script_notes")
    return {
        "game_id": game.get("id"),
        "league_code": league,
        # required by the live Analysis schema; mirrors the Nova read verdict
        "recommendation": str(result.get("status") or "pass").lower(),
        "game_script_tempo": gs_tempo or "NOT PROJECTED",
        "game_script_notes": gs_notes or
            "Game script is projected for football matchups from Nova's true line; "
            "other sports do not use script templating.",
        "situational_summary": situational or None,
        "h2h_summary": h2h,
        "public_bias_narrative": (
            "Ticket-split data is not ingested, so no public-percentage claims are made. "
            f"The number's construction (market {result.get('market_line'):+.1f} vs Nova fair "
            f"{result.get('true_line'):+.1f}) is the only positioning evidence."),
        "monitor_trigger": result.get("next_trigger"),
        "sharp_money_indicators_json": json.dumps(sharp) if sharp else "[]",
        "is_sample": False,
    }
