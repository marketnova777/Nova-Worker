"""One-off: recompute all current reads on scheduled games with the
sophisticated narrative engine; fix legacy-taxonomy reads; heal stale ones.

- Scheduled-game current reads: recompute from live data, patch every
  schema-accepted field (market_psychology upgrades the UI TODAY).
- Legacy-taxonomy reads on scheduled games: recomputed => correct status.
- Duplicate current reads per (game, market): keep newest, supersede rest.
"""
import json
import sys

sys.path.insert(0, ".")
from nova.api import Base44Client, changed_fields
from nova import reads, enrich

client = Base44Client()
games = client.list_all("Game", fields=["id", "home_team", "away_team",
    "home_team_abbr", "away_team_abbr", "league_code", "status", "start_time",
    "provider_game_id"])
ratings = client.list_all("TeamRating")
forms = client.list_all("TeamForm")
odds = client.list_all("OddsSnapshot", fields=["id", "game_id", "book",
    "market_type", "is_current", "home_line", "over_line", "home_price",
    "away_price", "over_price", "created_date"])
injuries = client.list_all("Injury", fields=["id", "league_code", "team_abbr",
                                             "player_name", "status", "injury_type"])
all_reads = client.list_all("MarketNovaRead")

sched = {g["id"]: g for g in games if str(g.get("status")).lower() == "scheduled"}

# --- 1) duplicate current reads per (game, market): keep newest ---
from collections import defaultdict
by_key = defaultdict(list)
for r in all_reads:
    if r.get("is_current"):
        by_key[(r.get("game_id"), str(r.get("market_type")).lower())].append(r)
dup_groups = {k: v for k, v in by_key.items() if len(v) > 1}
n_super = 0
for k, group in dup_groups.items():
    group.sort(key=lambda r: str(r.get("updated_date") or r.get("created_date") or ""), reverse=True)
    for r in group[1:]:
        client.put("MarketNovaRead", r["id"], {"is_current": False})
        n_super += 1
print(f"superseded dup current reads: {n_super} (of {len(dup_groups)} groups)")

# reload current reads after supersede
current_reads = {}
for r in all_reads:
    if r.get("is_current") and r.get("game_id") in sched:
        current_reads.setdefault(r["game_id"], []).append(r)

n_upd = n_new = n_err = 0
for gid, g in sched.items():
    rows = [o for o in odds if o.get("game_id") == gid]
    markets = reads.freshest_market(rows)
    if not markets or not markets.get("spread") or markets["spread"].get("market_line") is None:
        continue
    pair = reads.resolve_ratings(g, ratings)
    if not pair:
        continue
    tape = [(o.get("created_date"), o.get("home_line"), o.get("home_price"))
            for o in rows
            if str(o.get("market_type")).lower() == "spread"
            and o.get("home_line") is not None]
    from nova import matching as _m
    lg = str(g.get("league_code") or g.get("sport") or "").upper()
    details = {"home": [], "away": []}
    for side in ("home", "away"):
        target = _m.canon(g.get(f"{side}_team") or g.get(f"{side}_team_name") or "", lg)
        for i in injuries:
            il = str(i.get("league_code") or "").upper()
            if il and il != lg:
                continue
            if _m.canon(i.get("team_abbr") or i.get("team_name") or "", lg) == target:
                details[side].append(i)
    try:
        result = reads.compute_read(g, pair, markets,
                                   form=reads.resolve_form(g, forms),
                                   movement=enrich.movement_context(rows),
                                   injuries=len(details.get("home", [])) + len(details.get("away", [])),
                                   tape=tape, injury_details=details)
    except Exception as e:
        n_err += 1
        print("compute failed", g.get("away_team"), "@", g.get("home_team"), str(e)[:100])
        continue
    if result.get("status") == "AWAITING DATA":
        continue  # honest stub; keep existing read
    desired = {
        "league_code": lg, "sport": str(g.get("sport") or g.get("league_code") or "").upper(),
        "status": result["status"],
        "marketnova_true_line": result.get("true_line"),
        "raw_edge": result.get("raw_edge"),
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
        "invalidators": json.dumps(result.get("invalidators")) if result.get("invalidators") else None,
        "market_psychology": result.get("why_market_here") or result.get("market_psychology"),
        "data_confidence": result.get("data_confidence"),
        "key_number_quality": result.get("key_number_quality"),
        "weather": result.get("weather"),
        "line_movement_direction": result.get("line_movement_direction"),
        "sharp_resistance": result.get("sharp_resistance"),
        "public_bias": result.get("public_bias"),
        "game_state_support": result.get("game_state_support"),
        "regression_support": result.get("regression_support"),
        "lineup_status": result.get("lineup_status"),
        "what_the_book_is_doing": result.get("why_market_here"),
        "one_line_read": result.get("nova_read"),
        "why_market_here": result.get("why_market_here"),
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
    PATCH_FIELDS = ["status", "marketnova_true_line", "raw_edge", "market_line",
        "true_line_value", "fair_price", "edge_value", "market_odds",
        "edge_score_display", "edge_direction", "minimum_playable_price",
        "playable_until", "next_trigger", "invalidators", "market_psychology",
        "data_confidence", "key_number_quality", "weather",
        "line_movement_direction", "sharp_resistance", "public_bias",
        "game_state_support", "regression_support", "lineup_status",
        "what_the_book_is_doing", "one_line_read", "why_market_here", "thesis",
        "nova_read", "risk", "buy_to", "regression_notes", "market_tape",
        "true_line_basis", "value_window", "confidence"]
    existing = current_reads.get(gid) or []
    spread_cur = next((r for r in existing
                       if str(r.get("market_type")).lower() == "spread"), None)
    if spread_cur:
        patch = changed_fields(spread_cur, desired, PATCH_FIELDS)
        if patch:
            try:
                client.put("MarketNovaRead", spread_cur["id"], patch)
                n_upd += 1
            except Exception as e:
                n_err += 1
                print("put failed", gid, str(e)[:100])
    else:
        desired["game_id"] = gid
        desired["market_type"] = "spread"
        desired["is_current"] = True
        try:
            client.create("MarketNovaRead", desired)
            n_new += 1
        except Exception as e:
            n_err += 1
            print("create failed", gid, str(e)[:100])

print(f"READ BACKFILL DONE: updated {n_upd}, created {n_new}, errors {n_err}, "
      f"superseded {n_super}")
