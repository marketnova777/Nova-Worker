"""Canonical publication proof for MarketNovaRead.

1. Coverage report over current reads (per market type, fair line present).
2. Controlled create -> update -> read-back -> delete using the worker's
   own credential, verifying the canonical fields survive persistence.

Exit 1 if create/update/read-back fails or a required field is dropped.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict

def read_visibility(c):
    """Which collections can the worker's own credential actually see?"""
    print("VISIBILITY (worker credential):")
    for ent in ("Game", "TeamRating", "TeamForm", "OddsSnapshot", "Injury",
                "MarketNovaRead", "Analysis", "Prop", "WeatherSnapshot"):
        try:
            rows = c.list_all(ent, fields=["id"])
            print(f"  {ent:16s} rows={len(rows)}")
        except Exception as e:
            print(f"  {ent:16s} ERROR {str(e)[:60]}")


APP = os.environ.get("BASE44_APP_ID", "")
TOK = os.environ.get("BASE44_SERVICE_TOKEN", "")
BASE = f"https://base44.app/api/apps/{APP}/entities"
REQUIRED = ["marketnova_true_line", "true_line_value", "fair_price",
            "marketnova_true_probability", "buy_to", "minimum_playable_price",
            "thesis_json", "one_line_read", "what_the_book_is_doing",
            "market_psychology", "invalidators"]


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}/{path}", data=data, method=method,
                               headers={"Authorization": f"Bearer {TOK}",
                                        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:220]


# ---------- 1. coverage ----------
rows, skip = [], 0
while True:
    st, page = req("GET", f"MarketNovaRead?limit=1000&skip={skip}"
                          "&fields=id,market_type,status,is_current,marketnova_true_line,"
                          "marketnova_true_probability,fair_price,buy_to,writer_id,updated_date")
    if st != 200 or not isinstance(page, list) or not page:
        break
    rows.extend(page)
    if len(page) < 1000:
        break
    skip += 1000
cur = [r for r in rows if r.get("is_current")]
print(f"COVERAGE total_rows={len(rows)} current={len(cur)}")
try:
    read_visibility(c)
except Exception as _e:
    print("visibility check failed:", _e)
print("  writers:", dict(Counter(str(r.get("writer_id")) for r in cur)))
per = defaultdict(lambda: {"n": 0, "line": 0, "prob": 0, "fair": 0, "buy": 0})
for r in cur:
    mt = str(r.get("market_type"))
    b = per[mt]
    b["n"] += 1
    b["line"] += r.get("marketnova_true_line") is not None
    b["prob"] += r.get("marketnova_true_probability") is not None
    b["fair"] += r.get("fair_price") is not None
    b["buy"] += r.get("buy_to") is not None
for mt in sorted(per):
    b = per[mt]
    print(f"  {mt:10s} n={b['n']:3d} fair_line={b['line']:3d} prob={b['prob']:3d} "
          f"fair_price={b['fair']:3d} buy_to={b['buy']:3d} "
          f"missing_line={b['n'] - b['line']:3d}")

# ---------- 2. controlled write ----------
probe = {"game_id": "__schema_probe__", "market_type": "spread",
         "league_code": "probe", "sport": "probe", "status": "PASS",
         "is_current": False, "is_sample": True, "market_line": -5.5,
         "marketnova_true_line": -6.5, "true_line_value": -6.5,
         "fair_price": -118, "marketnova_true_probability": 0.54,
         "buy_to": "PROBE -4.5", "minimum_playable_price": -4.5,
         "one_line_read": "probe read", "what_the_book_is_doing": "probe book",
         "market_psychology": "probe psych",
         "thesis_json": json.dumps({"probe": 1}), "invalidators": ["probe inv"],
         "edge_value": 1.0, "edge_unit": "POINTS", "writer_id": "nova-worker-github",
         "analysis_version": "V3", "model_version": "probe"}
st, created = req("POST", "MarketNovaRead", probe)
print(f"CREATE status={st} ok={isinstance(created, dict) and bool(created.get('id'))}")
if not isinstance(created, dict) or not created.get("id"):
    print("  body:", str(created)[:200])
    st_b, bulk = req("POST", "MarketNovaRead/bulk", [probe])
    print(f"  bulk fallback status={st_b} ok={bool(bulk)}")
    print("::error title=probe::publishing identity cannot create canonical reads")
    sys.exit(1)
rid = created["id"]
print("  created id:", rid)

fails = 0
for field in REQUIRED + ["market_line", "market_odds", "edge_value", "edge_unit",
                         "status", "model_version", "writer_id"]:
    want = probe.get(field)
    got = created.get(field)
    same = (got == want) or (str(got) == str(want)) or (want is None and got is None)
    if not same and field in probe:
        print(f"  DROPPED/COERCED {field}: want={want!r} got={got!r}")
        fails += 1

# update path
st_u, upd = req("PUT", f"MarketNovaRead/{rid}", {"marketnova_true_line": -7.25,
                                                 "status": "MONITOR"})
print(f"UPDATE status={st_u} ok={st_u in (200, 201)}")
st_r, fresh = req("GET", f"MarketNovaRead/{rid}")
line = (fresh or {}).get("marketnova_true_line")
stat = (fresh or {}).get("status")
print(f"READ-BACK marketnova_true_line={line} status={stat} "
      f"persisted={line == -7.25 and stat == 'MONITOR'}")
if st_r != 200 or line != -7.25:
    fails += 1

st_d, _ = req("DELETE", f"MarketNovaRead/{rid}")
print(f"DELETE status={st_d} (probe record removed)")

print(f"RESULT fails={fails}")
sys.exit(1 if fails else 0)
