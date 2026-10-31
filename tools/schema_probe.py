"""Controlled write/read-back probe for MarketNovaRead canonical fields.

Writes ONE clearly-marked sample record with the worker's own authorized
identity, reads it back from the database, compares every canonical field,
then deletes the probe. Exits non-zero when the schema drops a field.
"""
import json
import sys

from nova.api import Base44Client

PROBE = {
    "game_id": "__schema_probe_20261031__", "market_type": "spread",
    "league_code": "nfl", "sport": "football", "is_current": False, "is_sample": True,
    "status": "MONITOR", "writer_id": "nova-worker-github", "analysis_version": "V3",
    "model_version": "schema-probe-1",
    "marketnova_true_line": -6.5, "true_line_value": -6.5, "fair_price": -118,
    "marketnova_true_probability": 0.54, "buy_to": -4.5,
    "minimum_playable_price": -4.5, "market_line": -5.5, "market_odds": -110,
    "edge_value": 1.0, "edge_unit": "POINTS", "raw_edge": 1.0,
    "edge_direction": "NOVA_FAVORS_HOME", "one_line_read": "probe line",
    "what_the_book_is_doing": "probe book", "market_psychology": "probe psych",
    "thesis_json": '{"probe": true}', "invalidators": ["probe risk"],
    "data_quality_json": '{"odds": "fresh"}',
}


def main():
    c = Base44Client()
    created = c.create("MarketNovaRead", PROBE)
    if not created:
        print("::error title=probe::create refused — writer identity cannot publish")
        return 1
    rid = created.get("id")
    print(f"probe created: {rid}")
    rows = c.list_all("MarketNovaRead", fields=list(PROBE.keys()) + ["id"])
    back = next((r for r in rows if r.get("id") == rid), None)
    if back is None:
        single = f"MarketNovaRead/{rid}"
        status, body = c._req("GET", single)
        back = body if isinstance(body, dict) else {}
    drops, diffs, ok = [], [], []
    for f, want in PROBE.items():
        got = back.get(f, "<ABSENT>")
        if got == "<ABSENT>":
            drops.append(f)
        elif str(got) != str(want):
            diffs.append((f, want, got))
        else:
            ok.append(f)
    print(f"\npersisted {len(ok)}/{len(PROBE)} fields | dropped {len(drops)} | coerced {len(diffs)}")
    if drops:
        print("DROPPED (schema does not carry them):", ", ".join(sorted(drops)))
    for f, w, g in diffs:
        print(f"  coerced {f}: wrote {w!r} read {g!r}")
    print("SURVIVED:", ", ".join(sorted(ok)) or "none")
    # cleanup: the probe must never linger in the store
    c.delete("MarketNovaRead", rid)
    print("probe deleted:", rid)
    return 0 if not drops else 2


if __name__ == "__main__":
    sys.exit(main())
