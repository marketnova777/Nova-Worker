"""One Nova cycle: odds sync -> ESPN link/live sync -> read generation -> writes.

Run modes:
  python run_cycle.py            # full cycle, writes to Base44
  python run_cycle.py --dry-run  # compute everything, write nothing, print the plan

Creation doctrine: GAMES EXIST BECAUSE MARKETS EXIST. New games come only
from odds-api events with real book prices. ESPN never creates games; it
links them (provider_game_id) and keeps status/scores live. A game no book
prices is not board material for a market-intelligence product.

Write budget doctrine: a quiet board should cost single-digit writes.
Every write is a true delta (changed fields only), verified before dispatch.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import sys

from nova import config, espn, odds, reads, llm, enrich, props, web, answers, grades, janitor, research, synthesis, versioning
from nova.api import Base44Client, changed_fields


def log(section, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {section}: {msg}")


def _injury_details(game, injuries):
    """{home: [rows], away: [rows]} matched by team abbr/name."""
    from nova import matching as _m
    out = {"home": [], "away": []}
    lg = str(game.get("league_code") or game.get("sport") or "").upper()
    for side in ("home", "away"):
        target = _m.canon(game.get(f"{side}_team") or game.get(f"{side}_team_name") or "", lg)
        if not target:
            continue
        for i in injuries or []:
            il = str(i.get("league_code") or "").upper()
            if il and il != lg:
                continue
            if _m.canon(i.get("team_abbr") or i.get("team_name") or "", lg) == target:
                out[side].append(i)
    return out



def _prior_pack_field(read, field):
    """Pull a structured intent off a prior read's thesis_json answer pack
    (handles the Base44 string-vs-object roundtrip)."""
    if not read:
        return None
    pack = read.get("thesis_json")
    if isinstance(pack, str):
        try:
            pack = json.loads(pack)
        except (ValueError, TypeError):
            return None
    if not isinstance(pack, dict):
        return None
    ap = pack.get("answer_pack")
    if isinstance(ap, dict) and ap.get(field) is not None:
        return ap[field]
    return None

def run(dry=None):
    dry = dry if dry is not None else config.DRY_RUN
    if dry:
        log("cycle", "DRY RUN — computing plan, writing nothing")
    client = Base44Client()

    # ---------- load current state once ----------
    log("load", "fetching Game / TeamRating / TeamForm / MarketNovaRead")
    games = client.list_all("Game")
    ratings = client.list_all("TeamRating")
    forms = client.list_all("TeamForm")
    existing_reads = client.list_all("MarketNovaRead", fields=[
        "id", "game_id", "market_type", "is_current", "status",
        "marketnova_true_line", "raw_edge", "what_the_book_is_doing", "one_line_read", "thesis_json",
        "true_line_value", "market_line", "edge_direction", "final_read_json"])
    existing_weather = client.list_all("WeatherSnapshot", fields=["id", "game_id", "created_date"])
    existing_travel = client.list_all("TravelContext", fields=["id", "game_id", "team_abbr", "days_rest"])
    existing_analysis = client.list_all("Analysis", fields=["id", "game_id", "created_date"])
    existing_injuries = client.list_all("Injury", fields=[
        "id", "league_code", "team_abbr", "player_name", "status", "severity",
        "injury_type", "impact_rating"])
    injuries_all = existing_injuries
    log("load", f"{len(games)} games, {len(ratings)} ratings, {len(forms)} form rows, "
          f"{len(existing_reads)} reads, {len(existing_injuries)} injuries, "
          f"{len(existing_analysis)} analyses")

    # ---------- ESPN collection (link/live sync + free DraftKings odds) ----------
    espn_recs_by_league = {}
    for league in config.LEAGUES:
        events = espn.collect_events(league, config.LOOKAHEAD_DAYS)
        recs = []
        for ev in events:
            r = espn.parse_event(ev)
            r["league"] = league
            recs.append(r)
        espn_recs_by_league[league] = recs

    # ---------- odds sync (source of record for game creation) ----------
    all_game_creates, all_link_updates = [], []
    league_plans = []
    odds_by_game = {}
    juice_by_game = {}
    for league in config.LEAGUES:
        # ESPN DraftKings odds are always free; odds-api adds multi-book
        # depth when (and only when) ODDS_API_KEY is set.
        desired = []
        for rec in espn_recs_by_league.get(league, []):
            desired.extend(odds.espnn_rows(rec) if hasattr(odds, "espnn_rows") else odds.espn_rows(rec))
        if config.ODDS_API_KEY:
            for ev in odds.fetch_odds(league):
                desired.extend(odds.flatten_event(ev))
        if desired and not odds_by_game:
            all_odds = client.list_all("OddsSnapshot", fields=[
                "id", "game_id", "book", "market_type", "home_line",
                "away_line", "over_line", "under_line", "home_price",
                "away_price", "over_price", "under_price", "is_current",
                "created_date"])
            for o in all_odds:
                odds_by_game.setdefault(o.get("game_id"), []).append(o)
        existing = [o for rows in odds_by_game.values() for o in rows]
        gc, lu, sup, nr, jm = odds.plan(games, existing, desired, league)
        all_game_creates.extend(gc)
        all_link_updates.extend(lu)
        league_plans.append((gc, lu, sup, nr))
        for j in jm:
            if j.get("game_id"):
                juice_by_game[j["game_id"]] = j
        log("odds", f"{league}: {len(desired)} markets desired, {len(gc)} new games, "
              f"{len(nr)} changed rows, {len(sup)} superseded, {len(lu)} links")

    # ---------- ESPN link + live sync (never creates games) ----------
    game_updates, issues = [], []
    for league in config.LEAGUES:
        recs = espn_recs_by_league.get(league, [])
        virtual = list(games) + all_game_creates
        up, iss = espn.reconcile_links(virtual, recs, league)
        game_updates.extend(up)
        issues.extend(iss)
        log("espn", f"{league}: {len(recs)} events, {len(up)} updates/links, "
              f"{len(iss)} issues")
    for i in issues[:15]:
        log("espn", f"issue: {i}")

    # ---------- Read generation (games in window, with current odds) ----------
    cutoff = datetime.now(timezone.utc) + timedelta(days=config.LOOKAHEAD_DAYS)
    # spread reads only — a moneyline read for the same game must never
    # receive a spread patch (field collision), and duplicate current
    # spread reads get healed to one.
    current_reads, stale_read_ids = {}, []
    seen_read_games = set()
    rest_maps, weather_by_game, injury_count = {}, {}, {}
    inj_by_team, inj_ok_leagues = {}, set()
    analysis_upserts, travel_upserts, prop_payloads = [], [], []
    props_ctx = {}
    skip_status = ("final",) + tuple(config.TERMINAL_EXEMPT)

    # ---- enrichment: weather + injuries (free ESPN/open-meteo, no LLM) ----
    venue_by_espn, probables_by_espn = {}, {}
    for league, recs in espn_recs_by_league.items():
        for rec in recs:
            if rec.get("espn_id"):
                venue_by_espn[rec["espn_id"]] = rec
                if rec.get("probables"):
                    probables_by_espn[rec["espn_id"]] = rec["probables"]

    def _pack_ctx(g):
        """Honest per-game context for the answer pack. None = omit intent
        (the injury fetch failed), never a false 'no injuries' claim."""
        lg = str(g.get("league_code") or "").lower()
        if lg not in inj_ok_leagues:
            return None
        ctx = {}
        for side in ("away", "home"):
            ab = g.get(f"{side}_team_abbr")
            ctx[f"injuries_{side}"] = inj_by_team.get((lg, ab), []) if ab else []
        prob = None
        plist = probables_by_espn.get(g.get("provider_game_id")) or []
        if plist:
            parts = []
            for p in plist:
                t = str(p.get("team_abbr") or "").upper()
                nm = p.get("name")
                if t == str(g.get("away_team_abbr") or "").upper():
                    parts.append(f"{nm} ({g.get('away_team')} {t})")
                elif t == str(g.get("home_team_abbr") or "").upper():
                    parts.append(f"{nm} ({g.get('home_team')} {t})")
                else:
                    parts.append(str(nm))
            prob = " vs ".join(parts)
        ctx["probables"] = prob
        return ctx
    weather_games = []
    for g in games:
        rec = venue_by_espn.get(g.get("provider_game_id"))
        if not rec:
            continue
        try:
            st = datetime.fromisoformat(str(g.get("start_time", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if st > cutoff or str(g.get("status", "")).lower() in skip_status:
            continue
        weather_games.append({
            "game_id": g["id"], "league_code": g.get("league_code") or g.get("sport"),
            "venue": rec.get("venue"), "city": rec.get("venue_city"),
            "state": rec.get("venue_state"), "indoor": rec.get("venue_indoor"),
            "start_time": g.get("start_time"),
        })
    try:
        weather_by_game = enrich.weather_payloads(weather_games)
    except Exception as e:
        log("enrich", f"weather fetch failed: {e}")
    injury_upserts = []
    for league in config.LEAGUES:
        recs = espn_recs_by_league.get(league, [])
        if not recs:
            continue
        wteams = {}
        for rec in recs:
            for side in ("home", "away"):
                nm, ab = rec.get(f"{side}_name"), rec.get(f"{side}_abbr")
                if nm and ab:
                    wteams[str(nm).lower()] = ab
        if not wteams:
            continue
        try:
            plist = enrich.injury_payloads(league, config.ESPN_LEAGUES[league], wteams)
        except Exception as e:
            log("enrich", f"injuries {league} failed: {e}")
            continue
        for p in plist:
            injury_upserts.append(p)
            inj_ok_leagues.add(p["league_code"])
        for p in plist:
            key = (p["league_code"], p["team_abbr"])
            txt = p["player_name"] + " (" + (p.get("position") or "—") + ", " + str(p.get("status") or "?")
            if p.get("injury_type"):
                txt += ": " + str(p["injury_type"])[:60]
            txt += ")"
            inj_by_team.setdefault(key, []).append(txt)
        log("enrich", f"{league}: {len(plist)} injury rows for {len(wteams)//2} games")
    for p in injury_upserts:
        for g in games:
            lg = str(g.get("league_code") or "").lower()
            if lg != p["league_code"]:
                continue
            hit = (p["team_abbr"] in (g.get("away_team_abbr"), g.get("home_team_abbr"))
                   or str(p.get("team_name") or "").lower() in
                       (str(g.get("away_team") or "").lower(), str(g.get("home_team") or "").lower()))
            if hit:
                injury_count[g["id"]] = injury_count.get(g["id"], 0) + 1
    log("enrich", f"weather rows for {len(weather_by_game)} outdoor games, "
          f"{len(injury_upserts)} injury rows")
    for r in existing_reads:
        if not r.get("is_current") or str(r.get("market_type", "")).lower() != "spread":
            continue
        gid = r.get("game_id")
        if gid in seen_read_games:
            stale_read_ids.append(r["id"])   # duplicate current read — heal
            continue
        seen_read_games.add(gid)
        current_reads[gid] = r

    # ---- lifecycle: retire reads on FINAL/terminal games (postgame
    # leftovers never stay on the live board; grades keep the data) ----
    retired_ids, current_reads = supersede_finals(games, current_reads)
    stale_read_ids.extend(retired_ids)
    if retired_ids:
        log("read", f"{len(retired_ids)} postgame reads superseded (final games)")

    # ---------- live research (MODE 2; OpenAI web_search, optional) ------
    # Runs BEFORE read building so findings ride this cycle's answer packs.
    # Research never touches lines/edges: it informs context only.
    research_by_game = {}
    try:
        research_by_game = research.run_research(
            games, current_reads, log=lambda m: log("research", m))
    except Exception as e:
        log("research", f"research pass failed (non-fatal): {e}")

    # ---------- MODE 2 synthesis (fixture rollout; verified before slate)
    # One deep-analysis game per cycle in fixture mode. The synthesis
    # produces a structured Analysis Pack + validated fair range that
    # becomes an INPUT to the canonical read — the kernel still owns all
    # downstream math and the state machine. Fail-open everywhere.
    fixture_games = []
    try:
        if not synthesis.synth_enabled():
            log("synth", "MODE 2 synthesis disabled (no OPENAI_API_KEY) — "
                         "reads stay on the ratings baseline")
        if synthesis.synth_enabled():
            fixture_games = synthesis.select_fixture(games, current_reads) or []
            for _fg, _fr in fixture_games:
                log("synth", f"fixture: {_fg.get('away_team')}@{_fg.get('home_team')}")
    except Exception as e:
        log("synth", f"fixture selection failed (non-fatal): {e}")
    fixture_ids = {g.get("id") for g, _r in fixture_games}

    read_patches = []
    news_by_league = {}  # one online lookup per league per cycle (nova/web)
    for g in games:
        try:
            start = datetime.fromisoformat(str(g.get("start_time", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        skip_status = ("final",) + tuple(config.TERMINAL_EXEMPT)
        if start > cutoff or str(g.get("status", "")).lower() in skip_status:
            continue
        rows = odds_by_game.get(g["id"], [])
        if not rows:
            continue
        pair = reads.resolve_ratings(g, ratings)
        if not pair:
            # Honest silence is not a blank card: a game with a live market
            # but no power ratings publishes an AWAITING DATA read (compute_read
            # returns the stub) — the board says what it's missing, never nothing.
            pair = {}
        # backfill missing team abbrs from the ESPN event (the page's
        # injury module matches on team_abbr vs game abbr fields)
        rec_ab = venue_by_espn.get(g.get("provider_game_id"))
        if rec_ab and (not g.get("away_team_abbr") or not g.get("home_team_abbr")) \
                and rec_ab.get("away_abbr") and rec_ab.get("home_abbr"):
            game_updates.append((g, {
                "home_team_abbr": rec_ab.get("home_abbr"),
                "away_team_abbr": rec_ab.get("away_abbr")}))
        # self-heal: the board buckets on game_date (ET convention) — a
        # game missing it is invisible on the board, so fill it on sight
        if not g.get("game_date") and g.get("start_time"):
            try:
                _st = datetime.fromisoformat(str(g.get("start_time")).replace("Z", "+00:00"))
                game_updates.append((g, {"game_date":
                    _st.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")}))
            except ValueError:
                pass
        markets = reads.freshest_market(rows)
        league_l = str(g.get("league_code") or g.get("sport") or "").lower()
        if league_l not in news_by_league:
            news_by_league[league_l] = web.league_news(league_l)
        if league_l not in rest_maps:
            rest_maps[league_l] = enrich.rest_map(games, league_l)
        rmap = rest_maps[league_l]
        def _rest(team):
            try:
                st = datetime.fromisoformat(str(g.get("start_time")).replace("Z", "+00:00"))
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                d, _ = enrich.days_rest(rmap, team, st, league_l)
                return d
            except ValueError:
                return None
        rest_ctx = {"home": _rest(g.get("home_team")), "away": _rest(g.get("away_team"))}
        n_inj = injury_count.get(g["id"], 0)
        tape = []
        for o in rows:
            if str(o.get("market_type") or "").lower() == "spread" and o.get("home_line") is not None:
                tape.append((o.get("created_date"), o.get("home_line"), o.get("home_price")))
        inj_details = _injury_details(g, injuries_all)
        # ---- analysis versioning (doctrine 34/79): immutable read chains.
        # input_hash fingerprints the MATERIAL inputs; quiet cycles keep
        # the prior version; material changes mint v+1 with supersedes.
        _frm = reads.resolve_form(g, forms)
        _pre_hash = versioning.input_fingerprint(
            g, pair, markets, juice=juice_by_game.get(g["id"]), form=_frm,
            movement=enrich.movement_context(rows),
            weather=weather_by_game.get(g["id"]), rest=rest_ctx,
            injuries=n_inj, injury_details=inj_details, tape=tape)
        _cur_read = current_reads.get(g.get("id"))
        _prior_meta = versioning.pack_meta(_cur_read.get("thesis_json")) \
            if _cur_read else None
        _meta = versioning.next_meta(_prior_meta, _pre_hash)
        result = reads.compute_read(g, pair, markets,
                                    juice=juice_by_game.get(g["id"]),
                                    form=reads.resolve_form(g, forms),
                                    movement=enrich.movement_context(rows),
                                    weather=weather_by_game.get(g["id"]),
                                    rest=rest_ctx, injuries=n_inj,
                                    tape=tape, injury_details=inj_details)
        # ---- MODE 2 deep synthesis for fixture games only ----
        if g.get("id") in fixture_ids and result.get("status") != "AWAITING DATA":
            try:
                # MODE 1 gate (doctrine 43): unchanged inputs + fresh pack ->
                # reuse the stored research snapshot; no re-synthesis burn.
                _prior_ap = _prior_pack_field(current_reads.get(g.get("id")),
                                              "analysis_pack")
                _cached_ap = versioning.cached_synthesis(
                    _prior_ap, _pre_hash, config.SYNTH_TTL_H)
                if _cached_ap:
                    log("synth", "cached analysis pack re-applied "
                                 "(inputs unchanged, pack fresh) — MODE 1")
                    result = reads.compute_read(
                        g, pair, markets,
                        juice=juice_by_game.get(g["id"]), form=_frm,
                        movement=enrich.movement_context(rows),
                        weather=weather_by_game.get(g["id"]),
                        rest=rest_ctx, injuries=n_inj,
                        tape=tape, injury_details=inj_details,
                        synth={"mid": _cached_ap["effective_true_line"],
                               "pack": _cached_ap})
                    result["analysis_pack"] = _cached_ap
                    if _cached_ap.get("fair_price_reasoning"):
                        result["thesis"] = " ".join(
                            str(x) for x in _cached_ap["fair_price_reasoning"][:4])
                    if _cached_ap.get("primary_risk"):
                        result["risk"] = str(_cached_ap["primary_risk"])
                    if _cached_ap.get("invalidators"):
                        result["invalidators"] = _cached_ap["invalidators"]
                    if _cached_ap.get("sportsbook_context"):
                        result["why_market_here"] = str(_cached_ap["sportsbook_context"])
                else:
                    # MODE 2: fresh deep synthesis for this fixture game.
                    _research_in = (research_by_game.get(g.get("id"))
                                    or _prior_pack_field(current_reads.get(g.get("id")),
                                                         "live_research"))
                    _ctx = synthesis.build_context(
                        g, result.get("true_line"),
                        (markets.get("spread") or {}).get("market_line"),
                        tape=tape, injuries=n_inj, injury_details=inj_details,
                        weather=weather_by_game.get(g["id"]), rest=rest_ctx,
                        form=_frm,
                        movement=enrich.movement_context(rows), league=league_l,
                        research=_research_in)
                    _mid, _pack, _notes = synthesis.synthesize(
                        g, result.get("true_line"), _ctx,
                        log=lambda m: log("synth", m))
                    for _n in _notes:
                        log("synth", f"{g.get('away_team')}@{g.get('home_team')}: {_n}")
                    if _mid is not None and _pack:
                        # version-chain the pack to its inputs (doctrine 34)
                        _pack["input_hash"] = _pre_hash
                        result = reads.compute_read(
                            g, pair, markets,
                            juice=juice_by_game.get(g["id"]),
                            form=_frm,
                            movement=enrich.movement_context(rows),
                            weather=weather_by_game.get(g["id"]),
                            rest=rest_ctx, injuries=n_inj,
                            tape=tape, injury_details=inj_details,
                            synth={"mid": _mid, "pack": _pack})
                        _pack["status_at_write"] = result.get("status")
                        # UPDATE contract vs the prior canonical pack
                        if _prior_ap:
                            _diff = synthesis.compare_packs(_prior_ap, _pack)
                            if _diff:
                                _pack["update_diff"] = _diff
                        result["analysis_pack"] = _pack
                        # synthesized narrative fields override templates —
                        # mechanics-level thesis per founder doctrine section 50
                        if _pack.get("fair_price_reasoning"):
                            result["thesis"] = " ".join(
                                str(x) for x in _pack["fair_price_reasoning"][:4])
                        if _pack.get("primary_risk"):
                            result["risk"] = str(_pack["primary_risk"])
                        if _pack.get("invalidators"):
                            result["invalidators"] = _pack["invalidators"]
                        if _pack.get("sportsbook_context"):
                            result["why_market_here"] = str(_pack["sportsbook_context"])
            except Exception as e:
                log("synth", f"synthesis failed (non-fatal, baseline stands): {e}")

        doing, one_line = reads.template_narrative(result, g)
        if current_reads.get(g["id"]) is None:  # new reads only — write budget
            richer = llm.narrate(g, result)
            if richer and all(richer):
                doing, one_line = richer
        desired_read = {
            # league_code/sport are REQUIRED by the live MarketNovaRead
            # schema; patches ignore them (changed_fields uses its own list)
            "league_code": str(g.get("league_code") or g.get("sport") or "").upper(),
            "sport": str(g.get("sport") or g.get("league_code") or "").upper(),
            "status": result["status"],
            "marketnova_true_line": result.get("true_line"),
            "raw_edge": result.get("raw_edge"),
            "what_the_book_is_doing": doing,
            "one_line_read": one_line,
            "data_quality_json": json.dumps(result.get("telemetry"))
                if result.get("telemetry") else None,
            "market_line": result.get("market_line"),
                "true_line_value": result.get("true_line"),
            "fair_price": result.get("true_line"),
            "edge_value": result.get("edge_value"),
                "market_odds": result.get("market_odds"),
            "edge_score_display": result.get("edge_score_display"),
            "edge_direction": result.get("edge_direction"),
            "minimum_playable_price": result.get("minimum_playable"),
            "playable_until": result.get("playable_until"),
            "next_trigger": result.get("next_trigger"),
            "invalidators": json.dumps(result.get("invalidators"))
                if result.get("invalidators") else None,
            "market_psychology": result.get("market_psychology"),
            "data_confidence": result.get("data_confidence"),
            "key_number_quality": result.get("key_number_quality"),
            "weather": result.get("weather"),
            "line_movement_direction": result.get("line_movement_direction"),
            "sharp_resistance": result.get("sharp_resistance"),
            "public_bias": result.get("public_bias"),
            "game_state_support": result.get("game_state_support"),
            "regression_support": result.get("regression_support"),
            "lineup_status": result.get("lineup_status"),
            "why_market_here": result.get("why_market_here"),
            # worker-written answer pack (Python-generated answers;
            # novaChat renders these — zero Base44 AI credits)
            "thesis_json": answers.pack_json(g, result, result,
                                              news_by_league.get(league_l),
                                              context=_pack_ctx(g),
                                              research=research_by_game.get(g.get("id")),
                                              meta=_meta),
            "thesis": result.get("thesis"),
            "nova_read": result.get("nova_read"),
            "risk": result.get("risk"),
            "buy_to": result.get("buy_to"),
            "regression_notes": result.get("regression_notes"),
            "market_tape": result.get("market_tape"),
            "true_line_basis": result.get("true_line_basis"),
            "value_window": result.get("value_window"),
            "confidence": result.get("confidence"),
        }
        if result.get("status") != "AWAITING DATA":
            if league_l in props.SPORT_SLUGS and str(g.get("status") or "").lower() == "scheduled":
                try:
                    if league_l not in props_ctx:
                        lg_last = props.team_last_game(games, league_l)
                        lg_ctx = props.load_baselines(league_l, lg_last, log)
                        props_ctx[league_l] = (lg_last, lg_ctx)
                    prop_payloads.extend(props.build_props(
                        g, league_l, result.get("true_line"), None, lg_last, lg_ctx))
                except Exception as e:
                    log("props", f"build failed {g.get('id')}: {e}")
            try:
                analysis_upserts.append(enrich.analysis_payload(
                    g, result, reads.resolve_form(g, forms),
                    enrich.movement_context(rows), weather_by_game.get(g["id"]),
                    rest_ctx))
            except Exception as e:
                log("enrich", f"analysis build failed {g.get('id')}: {e}")
            try:
                tp = enrich.travel_payload(g, rest_maps.get(league_l), league_l)
                if tp:
                    travel_upserts.append(tp)
            except Exception as e:
                log("enrich", f"travel build failed {g.get('id')}: {e}")
        cur = current_reads.get(g["id"])
        if cur is None:
            log("read", f"NEW read for {g.get('away_team')}@{g.get('home_team')}: {result['status']}")
            read_patches.append(("__create__", g["id"], desired_read))
        else:
            patch = changed_fields(cur, desired_read, [
                "status", "marketnova_true_line", "raw_edge", "market_line",
                "what_the_book_is_doing", "one_line_read",
                # price-locked fields change in lockstep with the numbers —
                # no extra write churn, but keep the page current
                "edge_value", "edge_score_display", "edge_direction", "market_odds",
                "minimum_playable_price", "playable_until",
                "key_number_quality", "line_movement_direction", "sharp_resistance", "public_bias",
                "game_state_support", "regression_support", "weather", "lineup_status",
                "why_market_here", "thesis", "nova_read", "risk", "buy_to", "thesis_json",
                "regression_notes", "market_tape", "true_line_basis",
                "value_window", "data_confidence"])
            # self-heal: reads written before answer packs existed carry no
            # thesis_json; if fields are otherwise unchanged the read would
            # never be rewritten — force the pack in so no read is pack-less
            if not patch and not cur.get("thesis_json") and desired_read.get("thesis_json"):
                patch = {"thesis_json": desired_read["thesis_json"]}
            if patch:
                read_patches.append((cur["id"], g["id"], patch))
    # first-cycle reads for games created THIS run — they'd otherwise sit
    # read-less on the board for 30 minutes until the next cycle
    for li, (gc, lu, sup, nr) in enumerate(league_plans):
        rows_by_ref = {}
        for row in nr:
            kind, val = row.get("game_ref", ("id", None))
            if kind == "create":
                rows_by_ref.setdefault(val, []).append(row)
        for ci, payload in enumerate(gc):
            rows = rows_by_ref.get(ci) or []
            markets = {}
            for row in rows:
                mt = str(row.get("market_type", "")).lower()
                line = (row.get("home_line") if mt == "spread"
                        else row.get("over_line") if mt == "total"
                        else row.get("home_price"))
                markets[mt] = {"line": line, "market_line": line,
                               "home_price": row.get("home_price"),
                               "away_price": row.get("away_price"),
                               "book": row.get("book")}
            if "spread" not in markets or markets["spread"].get("market_line") is None:
                continue
            pair = reads.resolve_ratings(payload, ratings)
            if not pair:
                pair = {}   # no ratings yet -> AWAITING DATA stub, not a skip
            result = reads.compute_read(payload, pair, markets,
                                        form=reads.resolve_form(payload, forms))
            doing, one_line = reads.template_narrative(result, payload)
            richer = llm.narrate(payload, result)   # new reads only
            if richer and all(richer):
                doing, one_line = richer
            log("read", f"NEW read for {payload.get('away_team')}@{payload.get('home_team')}: {result['status']}")
            lg_code = str(payload.get("league_code") or payload.get("sport") or "").upper()
            read_patches.append(("__new_game__", (li, ci), {
                "league_code": lg_code,
                "sport": lg_code,
                "status": result["status"],
                "marketnova_true_line": result.get("true_line"),
                "raw_edge": result.get("raw_edge"),
                "data_quality_json": json.dumps(result.get("telemetry"))
                    if result.get("telemetry") else None,
                "market_line": result.get("market_line"),
                "true_line_value": result.get("true_line"),
                "fair_price": result.get("true_line"),
                "edge_value": result.get("edge_value"),
                "market_odds": result.get("market_odds"),
                "edge_score_display": result.get("edge_score_display"),
                "edge_direction": result.get("edge_direction"),
                "minimum_playable_price": result.get("minimum_playable"),
                "playable_until": result.get("playable_until"),
                "next_trigger": result.get("next_trigger"),
                "invalidators": json.dumps(result.get("invalidators"))
                    if result.get("invalidators") else None,
                "market_psychology": result.get("market_psychology"),
                "data_confidence": result.get("data_confidence"),
                "what_the_book_is_doing": doing,
                "one_line_read": one_line,
                "thesis_json": answers.pack_json(
                    payload, result, result,
                    web.league_news(lg_code.lower()), context=_pack_ctx(payload),
                    meta=versioning.next_meta(
                        None, versioning.input_fingerprint(
                            payload, pair, markets))),
            }))
    log("read", f"{len(read_patches)} reads to write")

    # ---------- postgame grading (learning loop: calibration + CLV) ----------
    try:
        grade_pairs = grades.grade_finals(games, existing_reads, odds_by_game)
        if grade_pairs:
            summary = grades.calibration_summary(
                [p for _, p in grade_pairs])
            log("grades", f"{len(grade_pairs)} reads graded: "
                  f"model closer {summary.get('model_closer', 0)}, "
                  f"market closer {summary.get('market_closer', 0)}, "
                  f"avg CLV {summary.get('avg_clv_points')} pts")
            client.bulk_put("MarketNovaRead",
                            [(rid, {"final_read_json": json.dumps(p)})
                             for rid, p in grade_pairs])
        else:
            log("grades", "no ungraded final-game reads in window")
    except Exception as e:
        log("grades", f"grading failed (non-fatal): {e}")

    # ---------- Write phase ----------
    if dry:
        log("plan", f"would write: {len(all_game_creates)} game creates, "
              f"{len(all_link_updates)} odds links, {len(game_updates)} game updates, "
              f"{len(read_patches)} reads, {len(weather_by_game)} weather, "
              f"{len(injury_upserts)} injuries, {len(travel_upserts)} travel, "
              f"{len(analysis_upserts)} analyses, {len(prop_payloads)} props")
        return
    write_plan(client, league_plans, game_updates, read_patches, stale_read_ids)
    # ---------- enrichment upserts (delta-only) ----------
    wlatest, tlatest, alatest = {}, {}, {}
    for r in existing_weather:
        wlatest[r.get("game_id")] = r
    for r in existing_travel:
        tlatest[r.get("game_id")] = r
    for r in existing_analysis:
        alatest[r.get("game_id")] = r
    n_new_w = n_new_t = n_new_a = 0
    weather_new, travel_new, analysis_new = [], [], []
    weather_updates, travel_updates, analysis_updates, injury_updates = [], [], [], []
    for gid, payload in weather_by_game.items():
        cur = wlatest.get(gid)
        if cur:
            patch = changed_fields(cur, payload, [
                "temp_f", "feels_like_f", "wind_speed_mph", "precipitation_pct",
                "conditions", "weather_read", "humidity_pct"])
            if patch:
                weather_updates.append((cur["id"], patch))
        else:
            weather_new.append(payload)
    if weather_updates:
        client.bulk_put("WeatherSnapshot", weather_updates)
    if weather_new:
        client.bulk_create("WeatherSnapshot", weather_new)
        n_new_w = len(weather_new)
    for payload in travel_upserts:
        cur = tlatest.get(payload.get("game_id"))
        if cur:
            patch = changed_fields(cur, payload, ["days_rest", "is_back_to_back", "previous_venue"])
            if patch:
                patch["updated_at"] = payload["updated_at"]
                travel_updates.append((cur["id"], patch))
        else:
            travel_new.append(payload)
    if travel_updates:
        client.bulk_put("TravelContext", travel_updates)
    if travel_new:
        client.bulk_create("TravelContext", travel_new)
        n_new_t = len(travel_new)
    for payload in analysis_upserts:
        cur = alatest.get(payload.get("game_id"))
        if cur:
            patch = changed_fields(cur, payload, [
                "situational_summary", "h2h_summary", "public_bias_narrative",
                "monitor_trigger", "sharp_money_indicators_json"])
            if patch:
                analysis_updates.append((cur["id"], patch))
        else:
            analysis_new.append(payload)
    if analysis_updates:
        client.bulk_put("Analysis", analysis_updates)
    if analysis_new:
        client.bulk_create("Analysis", analysis_new)
        n_new_a = len(analysis_new)
    inj_key = {}
    for r in existing_injuries:
        inj_key[(str(r.get("league_code") or "").lower(), r.get("team_abbr"),
                 r.get("player_name"))] = r
    n_new_i = n_upd_i = 0
    injury_new = []
    seen_keys = set()
    for p in injury_upserts:
        key = (p["league_code"], p["team_abbr"], p["player_name"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        cur = inj_key.get(key)
        if cur:
            patch = changed_fields(cur, p, ["status", "severity", "injury_type", "impact_rating"])
            if patch:
                injury_updates.append((cur["id"], patch))
                n_upd_i += 1
        else:
            injury_new.append(p)
    if injury_updates:
        client.bulk_put("Injury", injury_updates)
    if injury_new:
        client.bulk_create("Injury", injury_new)
        n_new_i = len(injury_new)
    log("done", f"enrichment writes: {client.writes} total | weather new {n_new_w}, "
          f"travel new {n_new_t}, analysis new {n_new_a}, injuries new {n_new_i}/upd {n_upd_i}")
    # ---- Props (entity created by the builder; stage until then) ----
    prop_repo = {}
    prop_entity_ok = True
    try:
        for r in client.list_all("Prop", fields=["id", "game_id", "player_espn_id", "prop_type"]):
            prop_repo[(r.get("game_id"), r.get("player_espn_id"), r.get("prop_type"))] = r
    except Exception:
        prop_entity_ok = False
        log("props", "Prop entity not present — staging payloads to props_staging.json")
    n_new_p = n_upd_p = 0
    prop_new, prop_updates = [], []
    seen_p = set()
    for p in prop_payloads:
        key = (p["game_id"], p["player_espn_id"], p["prop_type"])
        if key in seen_p:
            continue
        seen_p.add(key)
        if not prop_entity_ok:
            continue
        cur = prop_repo.get(key)
        if cur:
            patch = changed_fields(cur, p, [
                "nova_fair_low", "nova_fair_high", "nova_read", "baseline_last_game",
                "status", "data_confidence", "headshot_url"])
            if patch:
                prop_updates.append((cur["id"], patch))
                n_upd_p += 1
        else:
            prop_new.append(p)
    if prop_updates:
        client.bulk_put("Prop", prop_updates)
    if prop_new and prop_entity_ok:
        created = client.bulk_create("Prop", prop_new)
        n_new_p = len(created)
        if len(created) < len(prop_new):
            log("props", f"bulk create partial: {len(created)}/{len(prop_new)}")
    if not prop_entity_ok and prop_payloads:
        with open("props_staging.json", "w") as f:
            json.dump(prop_payloads, f, indent=1)
    log("props", f"{len(prop_payloads)} payloads | new {n_new_p}, upd {n_upd_p}"
        + ("" if prop_entity_ok else " — STAGED, entity missing"))
    log("done", f"cycle complete: {client.writes} writes, {client.reads} bulk reads")

    # ---------- identity janitor: sweep legacy dup game copies ----------
    # (the app-side legacy ESPN sync still creates v14-schema duplicates;
    #  unattached copies are deleted, attached ones are logged for merge)
    try:
        odds_ids = set(odds_by_game.keys())
        # a fresh odds view when the league loop never loaded one
        candidates = [g for g in games
                      if not g.get("provider_game_id") and g.get("external_id")]
        if candidates and not odds_ids:
            odds_ids = set(o.get("game_id") for o in client.list_all(
                "OddsSnapshot", fields=["id", "game_id"]))
        # refresh games: the plan may have linked/created games this cycle
        fresh_games = client.list_all("Game", fields=[
            "id", "provider_game_id", "external_id", "home_team",
            "away_team", "game_date", "status", "created_date"])
        n_del, n_man = janitor.sweep(client, fresh_games, existing_reads,
                                     odds_ids, log=lambda m: log("janitor", m))
        if n_del or n_man:
            log("janitor", f"swept {n_del} duplicate games "
                  f"({n_man} attached dups left for manual merge)")
    except Exception as e:
        log("janitor", f"sweep failed (non-fatal): {e}")



def supersede_finals(games, current_reads, terminal=("final",) + tuple(config.TERMINAL_EXEMPT)):
    """Lifecycle rule: a read on a FINAL/terminal game is superseded the
    cycle after the game ends. Grades keep the full read record
    (final_read_json), but is_current=false so the board, top-edges and
    novaChat only ever serve the live slate. Returns (ids_to_retire,
    cleaned_current_reads)."""
    terminal_ids = {g.get("id") for g in games
                    if str(g.get("status") or "").lower() in terminal}
    retire, cleaned = [], dict(current_reads)
    for gid, r in list(cleaned.items()):
        if gid in terminal_ids:
            retire.append(r["id"])
            del cleaned[gid]
    return retire, cleaned


def write_plan(client, league_plans, game_updates, read_patches, stale_read_ids=()):
    """Dispatch the planned writes. league_plans is [(gc, lu, sup, nr), ...]
    IN LEAGUE ORDER — game_ref create indices are LOCAL to their league's
    game_creates list, so games are created and their odds rows attached
    within the same per-league pass. Superseding happens BEFORE inserting
    so two current rows never coexist."""
    # ESPN patches for planned creates merge into the create payload FIRST,
    # so a game born live/final is created correct the first time.
    for g, patch in game_updates:
        if not g.get("id") and "__create" in g:
            g.update(patch)

    snapshot_time = datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    created_ids_per_league = []
    for gc, lu, sup, nr in league_plans:
        local_ids = []
        for payload in gc:
            clean = {k: v for k, v in payload.items() if not k.startswith("__")}
            created = client.create("Game", clean)
            local_ids.append(created.get("id") if created else None)
        created_ids_per_league.append(local_ids)
        sup_batch = [(oid, {"is_current": False}) for oid in sup]
        if sup_batch:
            client.bulk_put("OddsSnapshot", sup_batch)
        odds_batch = []
        for row in nr:
            kind, val = row.pop("game_ref", ("id", None))
            gid = local_ids[val] if kind == "create" else val
            if gid:
                odds_batch.append({"game_id": gid, "snapshot_time": snapshot_time, **row})
        if odds_batch:
            client.bulk_create("OddsSnapshot", odds_batch)
        if lu:
            client.bulk_put("Game", lu)

    live_updates = [(g["id"], patch) for g, patch in game_updates if g.get("id")]
    if live_updates:
        client.bulk_put("Game", live_updates)

    if stale_read_ids:
        client.bulk_put("MarketNovaRead",
                        [(rid, {"is_current": False}) for rid in stale_read_ids])
    read_creates, read_updates = [], []
    for rid, gid, patch in read_patches:
        if rid == "__new_game__":
            li, ci = gid
            ids = created_ids_per_league[li]
            if ci < len(ids) and ids[ci]:   # game create failed -> next cycle heals
                read_creates.append({
                    "game_id": ids[ci], "is_current": True,
                    "market_type": "spread", **patch})
        elif rid == "__create__":
            read_creates.append({
                "game_id": gid, "is_current": True, "market_type": "spread", **patch})
        else:
            read_updates.append((rid, patch))
    if read_creates:
        client.bulk_create("MarketNovaRead", read_creates)
    if read_updates:
        client.bulk_put("MarketNovaRead", read_updates)


if __name__ == "__main__":
    run(dry="--dry-run" in sys.argv)
