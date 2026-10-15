"""ANALYSIS VERSIONING — immutable read chains (doctrine sections 34 + 79).

Every material analysis is a VERSION. The canonical MarketNovaRead keeps
one current read per game/market, but its answer pack now carries a meta
chain: analysis_version, input_hash, supersedes_version, research_version,
created_at. Never silently rewritten history:

- input_hash  — deterministic fingerprint of the MATERIAL analysis inputs
                (identity, ratings, market prices, juice, injuries, weather,
                rest, form, movement path). Same inputs -> same hash,
                forever (replayability). Volatile bookkeeping (odds-row
                timestamps, fetched_at stamps) is excluded so quiet cycles
                do not mint versions.
- analysis_version — increments ONLY when input_hash changes.
- supersedes_version — the version this read replaces (audit chain).
- created_at  — when THIS version was minted (stable across quiet cycles).
- research_version — the MODE 2 layer version that fed this read.

The chain rides thesis_json (answer_pack.meta) — the same canonical
carrier every surface consumes. Postgame grades (final_read_json) stamp
the version they graded, so postmortems reference an immutable read.
"""

import hashlib
import json

from nova.research import _iso, _now

META_KEYS = ("analysis_version", "supersedes_version", "input_hash",
             "research_version", "created_at")


# --------------------------------------------------------------------------
# INPUT FINGERPRINT
# --------------------------------------------------------------------------

def input_fingerprint(game, ratings_pair, markets, juice=None, form=None,
                      movement=None, weather=None, rest=None, injuries=None,
                      injury_details=None, tape=None):
    """Deterministic hash of the material analysis inputs. Only stable,
    decision-relevant values participate; cycle-noise is excluded."""
    def rating(r):
        if not r:
            return None
        for f in ("rating", "power_rating"):
            if r.get(f) is not None:
                return float(r[f])
        return None

    spread = (markets or {}).get("spread") or {}
    total = (markets or {}).get("total") or {}
    market = {
        "spread_line": spread.get("market_line"),
        "spread_home_price": spread.get("home_price"),
        "spread_away_price": spread.get("away_price"),
        "total_line": total.get("market_line"),
    }
    form_c = {}
    for side in ("home", "away"):
        row = (form or {}).get(side)
        form_c[side] = row.get("last_5_json") if row else None
    # movement path: the SEQUENCE of (line, price) — bookkeeping timestamps
    # stripped so a quiet odds table does not churn the hash
    path = [[row[1], row[2]] for row in (tape or [])
            if len(row) >= 3 and row[1] is not None]

    payload = {
        "identity": {k: game.get(k) for k in
                     ("provider_game_id", "home_team", "away_team",
                      "start_time")},
        "ratings": {"home": rating((ratings_pair or {}).get("home")),
                    "away": rating((ratings_pair or {}).get("away"))},
        "market": market,
        "juice": juice,
        "weather": weather,
        "rest": rest,
        "injuries": {"count": injuries,
                     "details": injury_details or []},
        "form": form_c,
        "movement": movement,
        "movement_path": path,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str,
                   ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# VERSION STAMPING
# --------------------------------------------------------------------------

def next_meta(prior_meta, input_hash, now=None, research_version=None):
    """Mint the meta chain for this cycle. Unchanged inputs keep the prior
    version and created_at (idempotent cycles never churn the chain)."""
    now = now or _iso(_now())
    prior_v = (prior_meta or {}).get("analysis_version")
    if not prior_v:
        return {"analysis_version": 1, "supersedes_version": None,
                "input_hash": input_hash, "created_at": now,
                "research_version": research_version}
    if (prior_meta or {}).get("input_hash") == input_hash:
        meta = dict(prior_meta)
        if research_version is not None:
            meta["research_version"] = research_version
        return meta
    return {"analysis_version": int(prior_v) + 1,
            "supersedes_version": int(prior_v),
            "input_hash": input_hash, "created_at": now,
            "research_version": research_version}


def pack_meta(pack):
    """Pull the meta chain off a stored answer pack (handles the Base44
    string-vs-object roundtrip)."""
    if isinstance(pack, str):
        try:
            pack = json.loads(pack)
        except (ValueError, TypeError):
            return None
    if not isinstance(pack, dict):
        return None
    ap = pack.get("answer_pack")
    meta = ap.get("meta") if isinstance(ap, dict) else None
    return meta if isinstance(meta, dict) else None


# --------------------------------------------------------------------------
# SYNTHESIS CACHE GATE (MODE 1 cached / MODE 2 fresh)
# --------------------------------------------------------------------------

def cached_synthesis(prior_pack, input_hash, max_age_hours, now=None):
    """Doctrine MODE 1: reuse the stored research snapshot when nothing
    material changed and it is still fresh. Returns the prior analysis
    pack to re-apply, or None when MODE 2 must run."""
    if not isinstance(prior_pack, dict):
        return None
    if prior_pack.get("validation") != "accepted":
        return None
    if prior_pack.get("input_hash") != input_hash:
        return None
    if prior_pack.get("effective_true_line") is None:
        return None
    created = _parse_hours(prior_pack.get("created_at"), now)
    if created is None or created > max_age_hours:
        return None
    return prior_pack


def _parse_hours(iso, now=None):
    from nova.research import _parse_dt
    t = _parse_dt(iso)
    if not t:
        return None
    now = now or _now()
    return max(0.0, (now - t).total_seconds()) / 3600.0
