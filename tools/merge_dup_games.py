"""One-off repair: merge duplicate Game records, repoint children, heal
duplicate current odds rows.

Steps:
1. Group games by (league, start date, home[:12], away[:12]).
2. Canonical = record WITH provider_game_id (newest wins ties).
3. Repoint children: OddsSnapshot, MarketNovaRead, Prop, Analysis,
   TravelContext, WeatherSnapshot -> canonical id.
4. Delete dup Game records (backup dumped first).
5. Heal odds: per (game, book, market) multiple is_current rows ->
   keep newest, supersede rest.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, ".")
from nova.api import Base44Client

client = Base44Client()
BACKUP = f"backup_dupgame_merge_{datetime.utcnow():%Y%m%d_%H%M}.jsonl"

games = client.list_all("Game", fields=["id", "home_team", "away_team",
    "home_team_abbr", "away_team_abbr", "league_code", "status", "start_time",
    "provider_game_id", "created_date", "created_by"])

groups = defaultdict(list)
for g in games:
    day = str(g.get("start_time"))[:10]
    key = (str(g.get("league_code") or g.get("sport") or "").lower(), day,
           str(g.get("home_team") or "").lower()[:12],
           str(g.get("away_team") or "").lower()[:12])
    groups[key].append(g)

dups = {k: v for k, v in groups.items() if len(v) > 1 and k[2] and k[3]}
print(f"dup groups: {len(dups)}")

odds = client.list_all("OddsSnapshot", fields=["id", "game_id", "book",
    "market_type", "is_current", "created_date"])
reads = client.list_all("MarketNovaRead", fields=["id", "game_id", "is_current"])
try:
    props = client.list_all("Prop", fields=["id", "game_id"])
except Exception:
    props = []
try:
    analyses = client.list_all("Analysis", fields=["id", "game_id"])
except Exception:
    analyses = []
try:
    travels = client.list_all("TravelContext", fields=["id", "game_id", "team_abbr", "days_rest"])
except Exception:
    travels = []
try:
    weathers = client.list_all("WeatherSnapshot", fields=["id", "game_id", "created_date"])
except Exception:
    weathers = []

backup_rows = []
n_merged = n_repointed = n_deleted = 0
for key, grp in dups.items():
    grp.sort(key=lambda g: (0 if g.get("provider_game_id") else 1,
                            str(g.get("created_date") or "")), reverse=False)
    # stable: provider-id first, then newest created
    with_pid = [g for g in grp if g.get("provider_game_id")]
    without = [g for g in grp if not g.get("provider_game_id")]
    with_pid.sort(key=lambda g: str(g.get("created_date") or ""), reverse=True)
    without.sort(key=lambda g: str(g.get("created_date") or ""), reverse=True)
    ordered = with_pid + without
    canonical, dups_of = ordered[0], ordered[1:]
    backup_rows.append({"canonical": canonical["id"], "dups": [
        {"id": d["id"], "created_by": d.get("created_by"),
         "provider_game_id": d.get("provider_game_id")} for d in dups_of]})
    dup_ids = {d["id"] for d in dups_of}
    # repoint (simple, typed)
    for row in odds:
        if row.get("game_id") in dup_ids:
            client.put("OddsSnapshot", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in reads:
        if row.get("game_id") in dup_ids:
            client.put("MarketNovaRead", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in props:
        if row.get("game_id") in dup_ids:
            client.put("Prop", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in analyses:
        if row.get("game_id") in dup_ids:
            client.put("Analysis", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in travels:
        if row.get("game_id") in dup_ids:
            client.put("TravelContext", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for row in weathers:
        if row.get("game_id") in dup_ids:
            client.put("WeatherSnapshot", row["id"], {"game_id": canonical["id"]})
            n_repointed += 1
    for d in dups_of:
        try:
            client.delete("Game", d["id"])
            n_deleted += 1
        except Exception as e:
            print("delete failed", d["id"], str(e)[:80])
    n_merged += 1

with open(BACKUP, "w") as f:
    for r in backup_rows:
        f.write(json.dumps(r) + "\n")

# --- heal duplicate current odds ---
odds2 = client.list_all("OddsSnapshot", fields=["id", "game_id", "book",
    "market_type", "is_current", "home_line", "over_line", "home_price",
    "away_price", "over_price", "created_date"])
by_key = defaultdict(list)
for o in odds2:
    if o.get("is_current"):
        by_key[(o.get("game_id"), o.get("book"), str(o.get("market_type")).lower())].append(o)
n_super = 0
for k, grp in by_key.items():
    if len(grp) < 2:
        continue
    grp.sort(key=lambda r: str(r.get("created_date") or ""), reverse=True)
    keep = grp[0]
    for r in grp[1:]:
        kl = (keep.get("home_line"), keep.get("over_line"), keep.get("home_price"))
        rl = (r.get("home_line"), r.get("over_line"), r.get("home_price"))
        if kl == rl:
            # exact duplicate row — supersede older
            client.put("OddsSnapshot", r["id"], {"is_current": False})
            n_super += 1
        else:
            # same key, different numbers — keep newest current, supersede
            # older as history (tape)
            client.put("OddsSnapshot", r["id"], {"is_current": False})
            n_super += 1
print(f"MERGE DONE: groups {n_merged}, repointed {n_repointed}, deleted {n_deleted}, "
      f"odds superseded {n_super}. backup: {BACKUP}")
