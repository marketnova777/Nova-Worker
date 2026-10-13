"""One-off: rebuild props with roster positions + backfill live Prop records."""
import json
import sys

sys.path.insert(0, ".")
from nova import props
from nova.api import Base44Client
from nova.api import changed_fields

client = Base44Client()
games = client.list_all("Game", fields=["id", "home_team", "away_team",
    "home_team_abbr", "away_team_abbr", "league_code", "status", "start_time",
    "provider_game_id"])
reads = client.list_all("MarketNovaRead", fields=["game_id", "is_current",
    "market_type", "marketnova_true_line", "true_line_value", "status"])
read_map = {r["game_id"]: r for r in reads
            if r.get("is_current") and str(r.get("market_type")).lower() == "spread"}

repo = {}
for r in client.list_all("Prop", fields=["id", "game_id", "player_espn_id",
                                          "prop_type", "position", "headshot_url"]):
    repo[(r.get("game_id"), r.get("player_espn_id"), r.get("prop_type"))] = r

totals = {"leagues": 0, "built": 0, "new": 0, "upd": 0, "miss_pos": 0}
for league in ("nfl", "ncaaf", "nba", "nhl"):
    lg_games = [g for g in games
                if str(g.get("league_code") or "").lower() == league
                and str(g.get("status") or "").lower() == "scheduled"
                and g["id"] in read_map]
    if not lg_games:
        continue
    last = props.team_last_game(games, league)
    ctx = props.load_baselines(league, last)
    totals["leagues"] += 1
    for g in lg_games:
        true_line = (read_map[g["id"]].get("marketnova_true_line")
                     or read_map[g["id"]].get("true_line_value"))
        built = props.build_props(g, league, true_line, None, last, ctx)
        totals["built"] += len(built)
        for p in built:
            key = (p["game_id"], p["player_espn_id"], p["prop_type"])
            cur = repo.get(key)
            if cur:
                patch = changed_fields(cur, p, ["position", "headshot_url",
                    "nova_fair_low", "nova_fair_high", "nova_read",
                    "baseline_last_game", "data_confidence"])
                if patch:
                    client.put("Prop", cur["id"], patch)
                    totals["upd"] += 1
            else:
                try:
                    client.create("Prop", p)
                    totals["new"] += 1
                except Exception as e:
                    print("create failed:", str(e)[:120])
            if not p.get("position"):
                totals["miss_pos"] += 1
print("BACKFILL DONE:", json.dumps(totals))
