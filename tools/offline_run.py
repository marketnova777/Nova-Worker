#!/usr/bin/env python3
"""Full-cycle run with ZERO Base44 API calls.

Uses LIVE ESPN data (free) + a local stand-in client backed by cached
entity dumps. All reads come from *.cache.json files in the cache dir;
all writes are recorded to offline_run_output.json and never leave the
machine. Exercises every phase of the cycle — odds plan, ESPN reconcile,
read computation, write phase — against real live data with no credits.

Usage:
  python tools/offline_run.py <cache_dir>

Cache files (any missing entity is treated as empty):
  Game.cache.json            — list of Game records
  TeamRating.cache.json      — list of TeamRating records
  MarketNovaRead.cache.json — list of MarketNovaRead records
  OddsSnapshot.cache.json   — list of OddsSnapshot records (optional)
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nova import cycle  # noqa: E402


class LocalClient:
    """Same interface as nova.api.Base44Client, but local-only."""

    def __init__(self, cache_dir):
        self.cache = cache_dir
        self.reads = 0
        self.writes = 0
        self.written = {"Game": [], "OddsSnapshot": [], "MarketNovaRead": []}
        self.puts = []
        self._n = 0

    def list_all(self, entity, fields=None, query=None):
        self.reads += 1
        path = os.path.join(self.cache, f"{entity}.cache.json")
        if os.path.exists(path):
            recs = json.load(open(path))
            print(f"  [offline] {entity}: {len(recs)} cached records")
            return recs
        print(f"  [offline] {entity}: no cache, using []")
        return []

    def bulk_put(self, entity, updates, chunk=100):
        n = 0
        for rid, patch in updates:
            self.put(entity, rid, patch)
            n += 1
        return n

    def bulk_create(self, entity, records, chunk=100):
        out = []
        for r in records:
            out.append(self.create(entity, dict(r)))
        return out

    def create(self, entity, payload):
        self.writes += 1
        self._n += 1
        rid = f"local-{entity}-{self._n}"
        self.written.setdefault(entity, []).append({"id": rid, **payload})
        return {"id": rid}

    def put(self, entity, record_id, patch):
        self.writes += 1
        self.puts.append({"entity": entity, "id": record_id, "patch": patch})
        return True


def check_invariants(client):
    """Structural checks on the simulated write plan."""
    problems = []

    games = client.written["Game"]
    espn_ids = [g.get("provider_game_id") for g in games if g.get("provider_game_id")]
    if len(espn_ids) != len(set(espn_ids)):
        problems.append("DUPLICATE provider_game_id among created games!")
    ext_ids = [g.get("external_id") for g in games if g.get("external_id")]
    if len(ext_ids) != len(set(ext_ids)):
        problems.append("DUPLICATE external_id among created games!")

    snaps = client.written["OddsSnapshot"]
    # odds rows may legitimately reference games that already exist in the DB
    # (loaded from cache), not just games created in this run
    cached_games = client.list_all("Game", fields=["id"])
    game_ids = {g["id"] for g in games} | {g["id"] for g in cached_games}
    for s in snaps:
        if not s.get("game_id") or s["game_id"] not in game_ids:
            if not str(s.get("game_id", "")).startswith("local-Game-"):
                problems.append(f"odds row with bad game ref: {s.get('game_id')}")

    reads = client.written["MarketNovaRead"]
    for r in reads:
        if not r.get("game_id"):
            problems.append(f"read with no game ref: {r}")

    if not any(g.get("provider_game_id") for g in games) and games:
        problems.append("created games carry no ESPN ids (link phase inert?)")
    return problems


def main():
    cache_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    client = LocalClient(cache_dir)
    # swap the client BEFORE run() constructs it
    cycle.Base44Client = lambda: client

    print("=== NOVA OFFLINE RUN (live ESPN, zero Base44 calls) ===")
    cycle.run(dry=False)

    out = {
        "summary": {
            "game_creates": len(client.written["Game"]),
            "odds_creates": len(client.written["OddsSnapshot"]),
            "read_creates": len(client.written["MarketNovaRead"]),
            "puts": len(client.puts),
            "simulated_writes": client.writes,
            "simulated_reads": client.reads,
        },
        "problems": check_invariants(client),
        "written": client.written,
        "puts": client.puts,
    }
    path = os.path.join(cache_dir, "offline_run_output.json")
    json.dump(out, open(path, "w"), indent=1)
    print(f"\n=== OFFLINE RUN DONE: {json.dumps(out['summary'])}")
    if out["problems"]:
        print("INVARIANT PROBLEMS:")
        for p in out["problems"]:
            print("  !!", p)
        sys.exit(1)
    print("invariants: OK — plan written to", path)


if __name__ == "__main__":
    main()
