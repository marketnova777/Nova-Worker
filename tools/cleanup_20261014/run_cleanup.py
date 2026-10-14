"""One-time cleanup (Oct 14 2026): delete stale/duplicate data.
Targets (all backed up to cleanup_20261014/):
  - 23 duplicate games created by a one-shot repair script (no reads/odds)
  - 408 orphan TravelContext rows (dead game refs)
  - 1,733 DataHealthLog rows (old ingestion feed, dead since Oct 12)
  - 9,822 OddsSnapshot rows on finals older than 14 days (incl. is_current)
"""
import sys, json, time, threading, queue
from datetime import datetime, timezone, timedelta
sys.path.insert(0, 'marketnova-worker')
from nova.api import Base44Client
from nova import config
import urllib.request, urllib.error

BASE = config.BASE44_API
TOK = config.BASE44_SERVICE_TOKEN

def hard_delete(entity, rid, retries=3):
    for attempt in range(retries + 1):
        req = urllib.request.Request(f"{BASE}/{entity}/{rid}", method="DELETE",
            headers={"Authorization": f"Bearer {TOK}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return True  # already gone
            if e.code in (429, 502, 503) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return False
        except Exception:
            if attempt < retries:
                time.sleep(1)
                continue
            return False

def worker(entity, q, results, label):
    while True:
        try:
            rid = q.get_nowait()
        except queue.Empty:
            return
        ok = hard_delete(entity, rid)
        if not ok:
            results[label + "_fail"] = results.get(label + "_fail", 0) + 1
        results[label] = results.get(label, 0) + 1
        q.task_done()

def run_deletes(entity, ids, label, threads=8):
    q = queue.Queue()
    for i in ids:
        q.put(i)
    results = {}
    ts = [threading.Thread(target=worker, args=(entity, q, results, label)) for _ in range(threads)]
    for t in ts: t.start()
    for t in ts: t.join()
    print(f"{label}: {results.get(label, 0)} deleted, {results.get(label+'_fail', 0)} FAILED", flush=True)

c = Base44Client()
now = datetime.now(timezone.utc)
def pd(s):
    try: return datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except: return None

games = c.list_all('Game')
gids = {g['id'] for g in games}
key = lambda g: (str(g.get('league_code') or '').lower(), str(g.get('start_time'))[:10], str(g.get('away_team')).lower(), str(g.get('home_team')).lower())
groups = {}
for g in games: groups.setdefault(key(g), []).append(g)
dup_ids = [g['id'] for v in groups.values() if len(v) > 1 for g in v if not g.get('provider_game_id')]
print(f"deleting {len(dup_ids)} dup games", flush=True)
run_deletes('Game', dup_ids, 'dup_games')

tc = c.list_all('TravelContext', fields=['id','game_id'])
gids2 = {g['id'] for g in c.list_all('Game', fields=['id'])}
orphan_tc = [t['id'] for t in tc if t.get('game_id') and t['game_id'] not in gids2]
print(f"deleting {len(orphan_tc)} orphan TravelContext", flush=True)
run_deletes('TravelContext', orphan_tc, 'orphan_travel')

dhl = c.list_all('DataHealthLog', fields=['id'])
print(f"deleting {len(dhl)} DataHealthLog", flush=True)
run_deletes('DataHealthLog', [r['id'] for r in dhl], 'health_logs')

old_final = {g['id'] for g in games if str(g.get('status')).lower()=='final'
             and pd(g.get('start_time')) and pd(g['start_time']) < now - timedelta(days=14)}
odds = c.list_all('OddsSnapshot', fields=['id','game_id'])
stale_odds = [o['id'] for o in odds if o.get('game_id') in old_final]
print(f"deleting {len(stale_odds)} stale odds", flush=True)
run_deletes('OddsSnapshot', stale_odds, 'stale_odds', threads=10)

print("CLEANUP COMPLETE", flush=True)
