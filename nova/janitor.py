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
