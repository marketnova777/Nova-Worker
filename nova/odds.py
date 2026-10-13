"""The Odds API ingest — upsert semantics (fixes the historic double-insert bug).

Creation doctrine: GAMES EXIST BECAUSE MARKETS EXIST. New games are created
from odds-api events that no existing Game matches; ESPN never creates games.
ESPN's job is linking (provider_game_id) and live sync; odds is the source of
record for what belongs on the board.

Rules that killed the old churn:
  1. ONE snapshot row per (game, book, market) — both outcomes (home/away,
     over/under) live inside that single row, never one row per outcome.
  2. If the current row for that key has the same line AND juice, write NOTHING.
     A quiet market costs zero writes.
  3. If it changed, supersede the old row (is_current=false) and insert one new
     current row — line history is preserved.
"""
from nova import config, matching

ODDS_HEADERS = {"User-Agent": "python-urllib/3.11"}


def fetch_odds(league, api_key=None):
    import json
    import urllib.request
    key = api_key or config.ODDS_API_KEY
    if not key:
        print("  odds: no ODDS_API_KEY, skipping")
        return []
    sport = config.ODDS_API_SPORTS.get(league)
    if not sport:
        return []
    url = (f"{config.ODDS_API_BASE}/sports/{sport}/odds"
           f"?apiKey={key}&regions=us&markets=spreads,totals,moneylines"
           f"&oddsFormat=american")
    try:
        req = urllib.request.Request(url, headers=ODDS_HEADERS)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  odds {league}: fetch failed: {e}")
        return []


def flatten_event(ev):
    """Flatten one odds-api event into desired market rows.

    Rows: {event_id, commence_time, home, away, book, market, line,
    home_price, away_price} with market in {spread, total, moneyline}.
    """
    home, away = ev.get("home_team"), ev.get("away_team")
    rows = []
    for bm in ev.get("bookmakers", []):
        book = bm.get("key") or bm.get("title")
        for mk in bm.get("markets", []):
            mk_key = mk.get("key")
            outcomes = {o["name"]: o for o in mk.get("outcomes", [])}
            if mk_key == "spreads":
                o = outcomes.get(home)
                if o is not None:
                    rows.append(_row(ev, book, "spread", o.get("point"), o.get("price"),
                                     outcomes.get(away, {}).get("price")))
            elif mk_key == "totals":
                o = outcomes.get("Over")
                if o is not None:
                    rows.append(_row(ev, book, "total", o.get("point"), o.get("price"),
                                     outcomes.get("Under", {}).get("price")))
            elif mk_key == "h2h":
                o = outcomes.get(home)
                if o is not None:
                    rows.append(_row(ev, book, "moneyline", None, o.get("price"),
                                     outcomes.get(away, {}).get("price")))
    return rows


def _row(ev, book, market, line, home_price, away_price):
    return {
        "event_id": ev.get("id"),
        "commence_time": ev.get("commence_time"),
        "home_team": ev.get("home_team"),
        "away_team": ev.get("away_team"),
        "book": book,
        "market": market,
        "line": line,
        "home_price": home_price,
        "away_price": away_price,
    }


def espn_rows(rec):
    """Build desired odds rows from an ESPN scoreboard record (free source:
    DraftKings spread/total via ESPN, no odds-api account needed)."""
    od = rec.get("odds")
    if not od:
        return []
    rows = []
    base = {
        "source": "espn",
        "event_id": rec.get("espn_id"),
        "espn_id": rec.get("espn_id"),
        "commence_time": rec.get("start_time"),
        "home_team": rec.get("home_name"),
        "away_team": rec.get("away_name"),
        "home_team_abbr": rec.get("home_abbr"),
        "away_team_abbr": rec.get("away_abbr"),
        "book": od.get("provider") or "DraftKings",
    }
    if od.get("spread") is not None:
        rows.append({**base, "market": "spread", "line": od["spread"],
                     "home_price": None, "away_price": None})
    if od.get("total") is not None:
        rows.append({**base, "market": "total", "line": od["total"],
                     "home_price": None, "away_price": None})
    if od.get("home_ml") is not None and od.get("away_ml") is not None:
        rows.append({**base, "market": "moneyline", "line": None,
                     "home_price": od["home_ml"], "away_price": od["away_ml"]})
    return rows


def find_game(games, row, league=""):
    """Match an odds row to a Game: exact identity first (external_id for
    odds-api rows, provider_game_id for ESPN rows), then abbr/name + day
    — fallback matches are league-scoped (NY is Liberty in NCAAF, the
    Liberty in WNBA; cross-league name collisions are real)."""
    for g in games:
        if row.get("espn_id") and g.get("provider_game_id") == row["espn_id"]:
            return g
    for g in games:
        if row.get("event_id") and g.get("external_id") == row["event_id"]:
            return g
    best, best_gap = None, None
    for g in games:
        if league and str(g.get("league_code", "")).upper() != league.upper():
            continue
        if _side_match((g.get("home_team"), g.get("home_team_name")),
                      row["home_team"], league) and \
           _side_match((g.get("away_team"), g.get("away_team_name")),
                      row["away_team"], league) and \
           matching.same_day(g, row["commence_time"]):
            gap = _dt_gap(g, row["commence_time"])
            if best is None or gap < best_gap:
                best, best_gap = g, gap
    return best


def _side_match(db_vals, odds_name, league=""):
    """Odds rows carry full names ('Georgia Bulldogs'); DB games may carry
    abbrs ('UGA', 'GEOR'), names, or both split across *_team / *_team_name.
    Every candidate field is tried through the league-scoped alias map
    (GEOR->UGA, VIRG->VT, ...) plus containment/token matching, accent-safe.
    A miss here means a DUPLICATE GAME gets created on first sighting —
    this matcher is the last line of defense."""
    if not odds_name:
        return False
    if isinstance(db_vals, str):
        db_vals = (db_vals,)
    n = str(odds_name).strip().lower().translate(matching._ACCENTS)
    nt = set(n.split())
    canon_n = matching.canon(odds_name, league)
    for db_val in db_vals:
        if not db_val:
            continue
        d = str(db_val).strip().lower().translate(matching._ACCENTS)
        if matching.canon(db_val, league) == canon_n:
            return True
        if d == n or d in n or n in d:
            return True
        dt = set(d.split())
        if dt and dt.issubset(nt):
            return True
    return False


def plan(games, existing_odds, desired_rows, league):
    """Compute the full odds write plan.

    Returns (game_creates, link_updates, supersede_ids, new_rows):
      game_creates: payloads for odds events with no matching Game
      link_updates: [(game_id, {"external_id": ...})] — first odds link
      supersede_ids: OddsSnapshot ids to flip is_current=false
      new_rows: new OddsSnapshot payloads (one per changed key)
    """
    from nova.api import changed_fields

    game_creates, link_updates = [], []
    # provisional game list: existing + planned creates (so odds rows
    # attach to the game they just created)
    virtual = list(games)
    seen_event_ids = set()

    game_by_key = {}
    for row in desired_rows:
        eid = row["event_id"]
        if eid in seen_event_ids:
            continue
        seen_event_ids.add(eid)
        g = find_game(virtual, row, league)
        if g is None:
            payload = {
                "league_code": league.upper(),
                "sport": league.upper(),
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "start_time": row["commence_time"],
                "status": "scheduled",
                "__create": len(game_creates),
            }
            if row.get("source") == "espn":
                payload["provider_game_id"] = eid  # ESPN-sourced identity
            else:
                payload["external_id"] = eid        # odds-api identity
            g = payload
            virtual.append(g)
            game_creates.append(g)
        elif (row.get("source") != "espn" and eid and not g.get("external_id")
              and "__create" not in g):
            link_updates.append((g["id"], {"external_id": eid}))

    # now reconcile odds rows against existing snapshots (real schema:
    # book / market_type / home_line / over_line / home_price ...)
    by_key = {}
    for o in existing_odds:
        if not o.get("is_current", False):
            continue
        key = (o.get("game_id"), o.get("book"), str(o.get("market_type", "")).lower())
        by_key.setdefault(key, []).append(o)

    supersede, new_rows, juice_moves = [], [], []
    for row in desired_rows:
        g = find_game(virtual, row, league)
        if g is None:
            continue
        key = (g.get("id"), row["book"], row["market"])
        curs = by_key.get(key) or []
        matching = [c for c in curs if _same_row(c, row)]
        if matching:
            # price already carried; keep ONE current row per key and
            # supersede any duplicates (heals historic double-inserts)
            keep = matching[0]
            for c in curs:
                if c is not keep:
                    supersede.append(c["id"])
            continue
        for c in curs:
            supersede.append(c["id"])
        new_rows.append({
            "game_ref": ("create", g["__create"]) if "__create" in g else ("id", g.get("id")),
            **snapshot_payload(row),
        })
        # Juice-move classification (game theory layer): the number held
        # but the price moved — books probe demand with juice before
        # crossing numbers. This is a leading indicator, not a line move.
        if curs and "__create" not in g and row["market"] == "spread":
            old = curs[0]
            try:
                line_held = (old.get("home_line") is not None
                             and row.get("line") is not None
                             and float(old["home_line"]) == float(row["line"]))
            except (TypeError, ValueError):
                line_held = False
            if line_held:
                for side, pf in (("home", "home_price"), ("away", "away_price")):
                    try:
                        op, np_ = float(old.get(pf)), float(row.get(pf))
                        if op and np_ and op != np_:
                            juice_moves.append({
                                "game_id": g.get("id"), "market": "spread",
                                "side": side, "line_held": True,
                                "worse": abs(np_) > abs(op),
                                "from": op, "to": np_})
                    except (TypeError, ValueError):
                        pass
    return game_creates, link_updates, supersede, new_rows, juice_moves


def snapshot_payload(row):
    """OddsSnapshot row in the production schema (matches the platform
    ingestion's shape so the frontend renders both identically)."""
    p = {"book": row["book"], "market_type": row["market"], "is_current": True,
         "region": "us", "is_stale": False, "is_opening": False, "is_closing": False}
    if row["market"] == "spread":
        p["home_line"] = row["line"]
        p["away_line"] = -row["line"] if row["line"] is not None else None
        p["home_price"] = row.get("home_price")
        p["away_price"] = row.get("away_price")
    elif row["market"] == "total":
        p["over_line"] = row["line"]
        p["under_line"] = row["line"]
        p["over_price"] = row.get("home_price")
        p["under_price"] = row.get("away_price")
    elif row["market"] == "moneyline":
        p["home_price"] = row.get("home_price")
        p["away_price"] = row.get("away_price")
    return p


def _same_row(cur, row):
    """True if the current snapshot already carries this exact price."""
    mt = row["market"]
    if mt == "spread":
        if not _num_eq(cur.get("home_line"), row["line"]):
            return False
        if row.get("home_price") is not None and not _num_eq(cur.get("home_price"), row.get("home_price")):
            return False
        # away-side juice moves must snapshot too when carried
        return row.get("away_price") is None or _num_eq(cur.get("away_price"), row.get("away_price"))
    if mt == "total":
        if not _num_eq(cur.get("over_line"), row["line"]):
            return False
        # juice moves on totals must snapshot too (odds-api rows carry prices)
        return row.get("home_price") is None or _num_eq(cur.get("over_price"), row.get("home_price"))
    if mt == "moneyline":
        return _num_eq(cur.get("home_price"), row.get("home_price")) and                _num_eq(cur.get("away_price"), row.get("away_price"))
    return False


def _num_eq(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return a == b


def _dt_gap(g, iso):
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(g.get("start_time", "")).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return abs((a - b).total_seconds())
    except (ValueError, TypeError):
        return float("inf")
