"""Identity janitor — sweeps legacy-automation duplicate Game copies.

A legacy app-side ESPN sync still creates Game records in the old v14
shape (home_team/away_team + odds_event_id, no provider_game_id) that
duplicate canonical worker games. Because the canonical copy carries the
same ESPN event id in provider_game_id, the duplicates are detectable by
signature and are swept automatically every cycle.

Conservative by design: a copy is only deleted when
  1. it has external_id == some canonical game's provider_game_id
     (and itself has no provider_game_id), AND
  2. it has NO reads and NO odds attached.
Anything with attachments is reported for manual merge, never deleted.
Deleted records are printed to the run log for auditability.
"""
import json
from concurrent.futures import ThreadPoolExecutor


def find_junk(games, read_game_ids, odds_game_ids):
    """(safe_to_delete, needs_manual_merge) lists of game records."""
    by_pid = {}
    for g in games:
        if g.get("provider_game_id"):
            by_pid.setdefault(g["provider_game_id"], []).append(g)
    safe, manual = [], []
    for g in games:
        if g.get("provider_game_id") or not g.get("external_id"):
            continue
        if not by_pid.get(g["external_id"]):
            continue  # not a duplicate of a canonical game
        if g["id"] in read_game_ids or g["id"] in odds_game_ids:
            manual.append(g)
        else:
            safe.append(g)
    return safe, manual


def sweep(client, games, reads, odds_game_ids, log=print):
    """Delete unattached duplicate copies; log attached ones for manual
    merge. Returns (deleted_count, manual_count)."""
    read_ids = set(r.get("game_id") for r in reads if r.get("game_id"))
    safe, manual = find_junk(games, read_ids, odds_game_ids)
    if manual:
        log(f"janitor: {len(manual)} attached duplicate games need manual "
            f"merge (left in place): "
            f"{[g['id'] for g in manual]}")
    if not safe:
        return 0, len(manual)
    for g in safe:
        log(f"janitor: deleting duplicate Game {g['id']} "
            f"(espn {g.get('external_id')}, created "
            f"{str(g.get('created_date', ''))[:10]}): "
            f"{json.dumps({k: g.get(k) for k in ('external_id', 'home_team', 'away_team', 'game_date', 'status')})}")

    def _delete(g):
        for _ in range(4):
            try:
                client.delete("Game", g["id"])
                return True
            except Exception:
                continue
        return False

    with ThreadPoolExecutor(max_workers=6) as ex:
        done = list(ex.map(_delete, safe))
    return sum(1 for d in done if d), len(manual)


# ---------------------------------------------------------------------------
# attached-duplicate MERGE (Oct 16 2026)
# The legacy app-side ESPN sync (service id ffd88b1e, still live in the
# Base44 editor) keeps creating v14-shape Game copies that sometimes get
# odds/reads attached before the sweeper sees them. Unattached copies are
# deleted by sweep(); attached ones are MERGED here every cycle:
#   canonical = the copy carrying provider_game_id (worker identity),
#               newest first; else the newest created record
#   children (odds / reads / props / analyses / travel / weather) are
#   repointed to the canonical, duplicate current odds and current reads
#   are superseded (newest wins), then the dup Game is deleted.
# ---------------------------------------------------------------------------

def _dup_groups(games):
    """Group games by (league-lower, start day, canon home, canon away)."""
    from collections import defaultdict
    from nova import matching
    groups = defaultdict(list)
    for g in games:
        lg = str(g.get("league_code") or g.get("sport") or "").lower()
        day = str(g.get("start_time") or "")[:10] or g.get("game_date")
        home = g.get("home_team") or g.get("home_team_abbr") or ""
        away = g.get("away_team") or g.get("away_team_abbr") or ""
        if not day or not home or not away:
            continue
        groups.setdefault(
            (lg, str(day), matching.canon(home, lg), matching.canon(away, lg)),
            []).append(g)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _order_group(grp):
    """Canonical first: provider_game_id-bearing and newest."""
    with_pid = sorted([g for g in grp if g.get("provider_game_id")],
                      key=lambda g: str(g.get("created_date") or ""), reverse=True)
    without = sorted([g for g in grp if not g.get("provider_game_id")],
                     key=lambda g: str(g.get("created_date") or ""), reverse=True)
    return with_pid + without


def merge_duplicates(client, games, log=print):
    """Merge attached duplicate games. Returns (groups_merged, dups_deleted,
    children_repointed, currents_superseded)."""
    from collections import defaultdict

    dup_groups = _dup_groups(games)
    if not dup_groups:
        return 0, 0, 0, 0

    dup_ids = set()
    canon_by_dup = {}
    for key, grp in dup_groups.items():
        ordered = _order_group(grp)
        canonical, losers = ordered[0], ordered[1:]
        for d in losers:
            dup_ids.add(d["id"])
            canon_by_dup[d["id"]] = canonical["id"]

    # ---- load children (typed, small projections) ----
    odds = client.list_all("OddsSnapshot", fields=["id", "game_id", "book",
        "market_type", "is_current", "created_date"])
    reads = client.list_all("MarketNovaRead", fields=["id", "game_id",
        "market_type", "is_current", "updated_date"])
    children = [("OddsSnapshot", odds), ("MarketNovaRead", reads)]
    for entity in ("Prop", "Analysis", "TravelContext", "WeatherSnapshot"):
        try:
            children.append((entity, client.list_all(entity,
                                                      fields=["id", "game_id"])))
        except Exception:
            pass

    # ---- repoint children in bulk (FAILURE = NO DELETE: a dup game is
    # only removed after every child row is confirmed repointed) ----
    n_repointed = 0
    failed_entities = set()
    for entity, rows in children:
        puts = [(r["id"], {"game_id": canon_by_dup[r["game_id"]]})
                for r in rows if r.get("game_id") in canon_by_dup]
        if puts:
            try:
                done = client.bulk_put(entity, puts)
                n_repointed += done
                if done < len(puts):
                    failed_entities.add(entity)
            except Exception as e:
                failed_entities.add(entity)
                log(f"merge: {entity} repoint failed: {e}")
    if failed_entities:
        log(f"merge: repoint incomplete for {failed_entities} — "
            f"keeping ALL dup games this cycle (no deletes)")
        return 0, 0, 0, 0

    # ---- supersede duplicate CURRENT reads (one per game+market) ----
    n_super = 0
    by_rm = defaultdict(list)
    for r in reads:
        if not r.get("is_current"):
            continue
        # resolve to the post-merge game id: dup rows already repointed
        gid = canon_by_dup.get(r.get("game_id"), r.get("game_id"))
        if gid in set(canon_by_dup.values()):
            by_rm[(gid, str(r.get("market_type") or "").lower())].append(r)
    sup_puts = []
    for (gid, mt), grp in by_rm.items():
        if len(grp) < 2:
            continue
        grp.sort(key=lambda r: str(r.get("updated_date") or ""), reverse=True)
        for r in grp[1:]:
            sup_puts.append((r["id"], {"is_current": False}))
    if sup_puts:
        try:
            client.bulk_put("MarketNovaRead", sup_puts)
            n_super += len(sup_puts)
        except Exception as e:
            log(f"merge: read supersede failed: {e}")

    # ---- supersede duplicate CURRENT odds rows on canonical games ----
    by_ok = defaultdict(list)
    canon_ids = set(canon_by_dup.values())
    for o in odds:
        if not o.get("is_current"):
            continue
        gid = canon_by_dup.get(o.get("game_id"), o.get("game_id"))
        if gid in canon_ids:
            by_ok[(gid, o.get("book"),
                   str(o.get("market_type") or "").lower())].append(o)
    odds_sup = []
    for k, grp in by_ok.items():
        if len(grp) < 2:
            continue
        grp.sort(key=lambda r: str(r.get("created_date") or ""), reverse=True)
        for r in grp[1:]:
            odds_sup.append((r["id"], {"is_current": False}))
    if odds_sup:
        try:
            client.bulk_put("OddsSnapshot", odds_sup)
            n_super += len(odds_sup)
        except Exception as e:
            log(f"merge: odds supersede failed: {e}")

    # ---- delete dup games ----
    n_deleted = 0
    def _del(g):
        for _ in range(4):
            try:
                client.delete("Game", g["id"])
                return True
            except Exception:
                import time
                time.sleep(0.5 * (_ + 1))
        return False
    with ThreadPoolExecutor(max_workers=6) as ex:
        for ok in ex.map(_del, [g for g in games if g["id"] in dup_ids]):
            n_deleted += 1 if ok else 0

    for key, grp in dup_groups.items():
        ordered = _order_group(grp)
        losers = ordered[1:]
        log(f"merge: {key[0]} {key[3]} @ {key[2]} -> canonical "
            f"{ordered[0]['id']} (kept), merged {len(losers)} copy(ies)")
    return len(dup_groups), n_deleted, n_repointed, n_super


# ------------------------------------------------------------------
# Data-level self-heal sweeps (run every cycle after the merge)

FORMS_KEEP = 1          # keep the newest TeamForm row per (league, abbr)

def dedupe_forms(client, log=print):
    """Legacy backfills left duplicate TeamForm rows per (league, abbr).
    Keep the newest row of each group, delete the rest."""
    from collections import defaultdict
    forms = client.list_all("TeamForm", fields=["id", "league_code",
                                                "team_abbr", "updated_date",
                                                "created_date"])
    key = defaultdict(list)
    for f in forms:
        k = (str(f.get("league_code") or "").lower(),
             str(f.get("team_abbr") or "").upper())
        key[k].append(f)
    doomed = []
    for grp in key.values():
        if len(grp) > FORMS_KEEP:
            grp.sort(key=lambda f: str(f.get("updated_date")
                                       or f.get("created_date") or ""), reverse=True)
            doomed += [f["id"] for f in grp[FORMS_KEEP:]]
    if doomed:
        _delete_ids(client, "TeamForm", doomed)
        log(f"forms dedupe: deleted {len(doomed)} duplicate TeamForm rows")
    return len(doomed)


def dedupe_current_odds(client, log=print):
    """Multiple odds writers leave 2+ is_current rows per (game, book,
    market). Keep the newest snapshot, supersede the rest (is_current
    false — history preserved, never deleted)."""
    from collections import defaultdict
    odds = client.list_all("OddsSnapshot", fields=["id", "game_id", "book",
                                                   "market_type", "is_current",
                                                   "snapshot_time", "updated_date"])
    grp = defaultdict(list)
    for o in odds:
        if not o.get("is_current") or not o.get("game_id"):
            continue
        grp[(o["game_id"], str(o.get("book")), str(o.get("market_type")))].append(o)
    superseded, keep = [], {}
    for rows in grp.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda o: (str(o.get("snapshot_time") or ""),
                                  str(o.get("updated_date") or "")), reverse=True)
        keep_row = rows[0]
        # never supersede the row we keep — write is_current=false on the rest
        superseded += [(o["id"], False) for o in rows[1:]]
        keep[keep_row["id"]] = True
    if superseded:
        try:
            client.bulk_put("OddsSnapshot", [(rid, {"is_current": False})
                                              for rid, _ in superseded])
            log(f"odds dedupe: superseded {len(superseded)} duplicate current odds rows")
        except Exception as e:
            log(f"odds dedupe failed: {e}")
    return len(superseded)


# Game-league abbr -> TeamRating-league abbr aliases (frontend joins)
RATING_ALIASES = {
    ("nba", "SAS"): ("nba", "SA"), ("nba", "NYK"): ("nba", "NY"),
    ("nba", "PHO"): ("nba", "PHX"), ("nba", "NOP"): ("nba", "NO"),
    ("nba", "UTA"): ("nba", "UTAH"), ("nhl", "LAK"): ("nhl", "LA"),
}

def ensure_rating_aliases(client, log=print):
    """Games carry ESPN abbrs (SAS/PHO/...); TeamRating uses its own
    convention (SA/PHX/...). Frontends join by abbr, so mirror each
    canonical rating row under the ESPN-style alias abbr."""
    have = {}
    for r in client.list_all("TeamRating"):
        k = (str(r.get("league_code") or "").lower(),
             str(r.get("team_abbr") or "").upper())
        if k not in have:
            have[k] = r
    creates = []
    for (lg, alias), (rlg, canon) in RATING_ALIASES.items():
        src = have.get((rlg, canon)) or have.get((lg, canon))
        if src and (lg, alias) not in have:
            row = {k: v for k, v in src.items()
                   if k not in ("id", "created_date", "updated_date", "created_by")}
            row["team_abbr"] = alias
            row["league_code"] = src.get("league_code")
            creates.append(row)
    if creates:
        try:
            client.bulk_create("TeamRating", creates)
            log(f"ratings: created {len(creates)} alias rows "
                f"({', '.join(c['team_abbr'] for c in creates)})")
        except Exception as e:
            log(f"ratings alias create failed: {e}")
    return len(creates)


def _delete_ids(client, entity, ids):
    def _del(rid):
        try:
            client.delete(entity, rid)
            return True
        except Exception:
            return False
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_del, ids))
    return sum(results)
