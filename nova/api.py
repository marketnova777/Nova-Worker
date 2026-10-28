"""Base44 entity API client with delta-only writes.

Credit-saving doctrine: every call to the app backend is metered, so this
client exists to make the MINIMUM number of calls that keeps the app true:
  - read entities once per cycle, in bulk (limit=500 pagination)
  - write only records whose tracked fields actually changed
  - write only the changed fields (PATCH semantics via PUT)
  - sleep briefly between writes to stay a polite client

Never touches bet_* recommendation fields on MarketNovaRead (frontend maps them).
"""
import json
import time
import urllib.request
import urllib.error

from nova import config


class Base44Client:
    def __init__(self):
        self.writes = 0
        self.reads = 0

    def _req(self, method, path, payload=None, retries=2):
        url = f"{config.BASE44_API}/{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, method=method, data=data, headers={
            "Authorization": f"Bearer {config.BASE44_SERVICE_TOKEN}",
            "Content-Type": "application/json",
        })
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    body = r.read()
                    return r.status, json.loads(body) if body else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503) and attempt < retries:
                    time.sleep(2 ** attempt * 2)
                    continue
                return e.code, e.read().decode(errors="replace")[:300]
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < retries:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise
        raise RuntimeError("unreachable")

    def list_all(self, entity, fields=None, query=None):
        """Fetch every record of an entity with pagination. Uses the
        maximum page size the API honors (5000, verified 2026-10-13) so
        large entities like OddsSnapshot cost ~7 calls instead of ~60."""
        page = 5000
        out, skip, attempt = [], 0, 0
        while True:
            path = f"{entity}?limit={page}&skip={skip}"
            if fields:
                path += "&fields=" + ",".join(fields)
            if query:
                path += "&query=" + urllib.request.quote(json.dumps(query))
            status, body = self._req("GET", path)
            if status == 500 and attempt < 3:   # transient DB timeouts
                attempt += 1
                time.sleep(15 * attempt)
                continue
            if status == 429 and attempt < 5:   # app entity read-traffic cap
                attempt += 1
                hint = 24
                try:                             # honor "Retry after N seconds"
                    import re as _re
                    m = _re.search(r"[Rr]etr(?:y|ies) after (\d+)", str(body))
                    if m:
                        hint = min(60, int(m.group(1)))
                except Exception:
                    pass
                time.sleep(hint + 2 * attempt)
                continue
            if status != 200:
                raise RuntimeError(f"list {entity} failed {status}: {body}")
            self.reads += 1
            self.rows_read = getattr(self, "rows_read", 0) + len(recs)
            recs = body if isinstance(body, list) else body.get("data", [])
            out.extend(recs)
            if len(recs) < page:
                return out
            skip += page
            attempt = 0

    def put(self, entity, record_id, patch):
        status, body = self._req("PUT", f"{entity}/{record_id}", patch)
        self.writes += 1
        if status not in (200, 201):
            print(f"  WRITE FAILED {entity}/{record_id} {status}: {body}")
            return False
        time.sleep(config.WRITE_DELAY_S)
        return True

    def delete(self, entity, record_id):
        status, body = self._req("DELETE", f"{entity}/{record_id}")
        self.writes += 1
        if status not in (200, 201, 204):
            print(f"  DELETE FAILED {entity}/{record_id} {status}: {body}")
            return False
        time.sleep(config.WRITE_DELAY_S)
        return True

    def create(self, entity, payload):
        status, body = self._req("POST", entity, payload)
        self.writes += 1
        if status not in (200, 201):
            print(f"  CREATE FAILED {entity} {status}: {body}")
            return None
        time.sleep(config.WRITE_DELAY_S)
        return body

    def bulk_put(self, entity, updates, chunk=100):
        """Batch-update records via PUT {entity}/bulk (verified PATCH
        semantics: fields not supplied are preserved). One metered call per
        `chunk` updates instead of one per record. `updates` is a list of
        (record_id, patch_dict) pairs; patches for the same record id are
        merged (later patches win per field) before the call, since the bulk
        endpoint rejects duplicate ids. Returns distinct records updated."""
        merged = {}
        for rid, patch in updates:
            merged.setdefault(rid, {}).update(patch)
        updates = list(merged.items())
        done = 0
        for i in range(0, len(updates), chunk):
            batch = [{"id": rid, **patch} for rid, patch in updates[i:i + chunk]]
            status, body = self._req("PUT", f"{entity}/bulk", batch)
            if status in (200, 201):
                done += len(batch)
                self.writes += 1
            else:
                print(f"  BULK PUT FAILED {entity} {status}: {str(body)[:150]}")
                for rid, patch in updates[i:i + chunk]:   # fallback singles
                    if self.put(entity, rid, patch):
                        done += 1
        return done

    def bulk_create(self, entity, records, chunk=100):
        """Batch-create records via the /bulk endpoint (create-only — records
        carrying an id get a NEW record, so patches still go through put()).
        One metered call per `chunk` records instead of one per record.
        Returns the list of created records (with ids) in submission order."""
        out = []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for i in range(0, len(records), chunk):
            batch = [dict(r) for r in records[i:i + chunk]]
            for r in batch:
                r.setdefault("updated_at", now)
            status, body = self._req("POST", f"{entity}/bulk", batch)
            if status in (200, 201):
                created = body if isinstance(body, list) else []
                out.extend(created)
                self.writes += 1
            else:
                print(f"  BULK CREATE FAILED {entity} {status}: {str(body)[:150]}")
                for r in batch:   # graceful fallback: single creates
                    one = self.create(entity, r)
                    if one is not None:
                        out.append(one)
        return out


def changed_fields(current, desired, tracked):
    """Return {field: new_value} for tracked fields that differ, or None.

    Numeric comparison uses a small tolerance so float round-trips
    (scores/lines persisting as 3.0 vs 3) never cause phantom writes.
    """
    patch = {}
    for f in tracked:
        if f not in desired:
            continue
        old, new = current.get(f), desired[f]
        if isinstance(new, (int, float)) and isinstance(old, (int, float)):
            if abs(float(new) - float(old)) < 1e-9:
                continue
        elif old == new:
            continue
        elif f.endswith("_json") and (isinstance(old, dict) or isinstance(new, dict)):
            # Base44 roundtrips JSON strings as parsed objects — compare
            # canonically so a pack written as text vs read back as an
            # object never looks like a change (no phantom write churn)
            try:
                a = json.loads(old) if isinstance(old, str) else old
                b = json.loads(new) if isinstance(new, str) else new
                if json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True):
                    continue
            except Exception:
                pass
        patch[f] = new
    return patch or None
