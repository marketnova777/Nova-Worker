"""Canonical publication path for MarketNovaRead.

normalize -> validate -> only-changed -> paced batches -> per-batch
checkpoint -> summary. When NOVA_PUBLISH_URL + NOVA_PUBLISH_SECRET are set
the batch goes to the trusted publishCanonicalReads function; otherwise the
entity path is used so the worker keeps operating, and that is reported.
"""
import json
import os
import time
import urllib.error
import urllib.request

from nova import canonical

BATCH = int(os.environ.get("NOVA_PUBLISH_BATCH", "100"))
MAX_WRITES_PER_MIN = int(os.environ.get("NOVA_PUBLISH_MAX_PER_MIN", "300"))
PUBLISH_URL = os.environ.get("NOVA_PUBLISH_URL", "").strip()
PUBLISH_SECRET = os.environ.get("NOVA_PUBLISH_SECRET", "").strip()
WRITER_ID = "nova-worker-github"
TOUCH = ("marketnova_true_line", "true_line_value", "fair_price",
         "marketnova_true_probability", "edge_unit", "edge_value")


class Pacer:
    """Keeps publication under the platform's per-caller write budget."""

    def __init__(self, per_min=MAX_WRITES_PER_MIN):
        self.per_min = max(1, per_min)
        self.stamps = []

    def wait(self):
        now = time.time()
        self.stamps = [t for t in self.stamps if now - t < 60]
        if len(self.stamps) >= self.per_min:
            gap = 60 - (now - self.stamps[0]) + 0.5
            if gap > 0:
                time.sleep(gap)
                self.stamps = []
        self.stamps.append(time.time())


def _unchanged(patch, stored):
    for k, v in patch.items():
        cur = stored.get(k)
        if isinstance(v, float) and isinstance(cur, (int, float)):
            if abs(float(v) - float(cur)) < 1e-9:
                continue
            return False
        if isinstance(v, (dict, list)) and isinstance(cur, str):
            try:
                if json.loads(cur) == v:
                    continue
            except Exception:
                pass
        if v != cur:
            return False
    return True


def _post_service(records):
    body = json.dumps({"writer_id": WRITER_ID, "reads": records}).encode()
    req = urllib.request.Request(PUBLISH_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Nova-Publish-Secret": PUBLISH_SECRET,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code,
                "detail": e.read().decode(errors="replace")[:300]}
    except Exception as e:
        return {"ok": False, "status": 0, "detail": f"{type(e).__name__}: {e}"[:200]}


def publish_reads(client, creates, updates, existing=None, log=print):
    """Publish canonical reads. Returns attempted/accepted/rejected counts."""
    s = {"attempted": 0, "accepted": 0, "rejected": 0, "retried": 0,
         "skipped_unchanged": 0, "warnings": 0, "path": "entity",
         "batches": [], "errors": []}
    index = {r.get("id"): r for r in (existing or [])}

    norm_c, norm_u = [], []
    for payload in creates or []:
        p, warns = canonical.normalize(payload)
        ok, errs = canonical.validate(p)
        s["warnings"] += len(warns)
        if not ok:
            s["rejected"] += 1
            s["errors"].append({"kind": "create", "game_id": p.get("game_id"),
                                "market_type": p.get("market_type"), "errors": errs})
            continue
        p["writer_id"] = WRITER_ID
        norm_c.append(p)

    for rid, patch in updates or []:
        base = index.get(rid, {})
        merged, warns = canonical.normalize({**base, **patch})
        ok, errs = canonical.validate({**base, **merged})
        s["warnings"] += len(warns)
        if not ok:
            s["rejected"] += 1
            s["errors"].append({"kind": "update", "id": rid, "errors": errs})
            continue
        if base and _unchanged(patch, base):
            s["skipped_unchanged"] += 1
            continue
        norm_u.append((rid, {**patch, "writer_id": WRITER_ID,
                             **{k: merged[k] for k in TOUCH if k in merged}}))

    s["attempted"] = len(norm_c) + len(norm_u)

    if PUBLISH_URL and PUBLISH_SECRET:
        s["path"] = "service"
        items = norm_c + [{"id": rid, **p} for rid, p in norm_u]
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            res = _post_service(chunk)
            ok = bool(res and res.get("ok"))
            acc = int((res or {}).get("accepted", 0))
            s["accepted"] += acc
            s["batches"].append({"sent": len(chunk), "accepted": acc, "ok": ok})
            if not ok:
                s["errors"].append({"kind": "batch", "detail": (res or {}).get("detail")})
                break
        return s

    pacer = Pacer()
    for i in range(0, len(norm_c), BATCH):
        chunk = norm_c[i:i + BATCH]
        pacer.wait()
        created = client.bulk_create("MarketNovaRead", chunk)
        s["accepted"] += len(created or [])
        s["batches"].append({"kind": "create", "sent": len(chunk),
                             "accepted": len(created or [])})
    for i in range(0, len(norm_u), BATCH):
        chunk = norm_u[i:i + BATCH]
        pacer.wait()
        done = client.bulk_put("MarketNovaRead", chunk)
        s["accepted"] += int(done or 0)
        s["batches"].append({"kind": "update", "sent": len(chunk),
                             "accepted": int(done or 0)})
    return s
