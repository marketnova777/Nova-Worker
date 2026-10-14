"""Generic online lookup layer for the Nova worker.

The worker runs on GitHub Actions with unrestricted network egress —
no Base44 integration credits are involved in ANY fetch here. This
module is the single place for "look anything up online" helpers:

  fetch_url(url)      — resilient GET returning parsed JSON (or None)
  league_news(slug)   — recent ESPN headlines for a league slug

Every helper fails HONEST: on error it returns None / [] and the caller
must render an explicit unavailable answer — never invented content
(FASTESTPAYOUT never-invent invariants).
"""

import json
import time
import urllib.request
import urllib.error

UA = ("Mozilla/5.0 (compatible; MarketNovaWorker/1.0; "
      "+https://github.com/marketnova777/Nova-Worker)")

# league_code -> ESPN sport path (site.api.espn.com/apis/site/v2/sports/<path>)
SPORT_PATHS = {
    "nfl": "football/nfl",
    "ncaaf": "football/college-football",
    "nba": "basketball/nba",
    "nhl": "hockey/nhl",
    "mlb": "baseball/mlb",
    "wnba": "basketball/wnba",
}

_news_cache = {}


def fetch_url(url, timeout=20, retries=2, backoff=1.5):
    """GET url, return parsed JSON, or None after retries. Never raises.

    Retries transient 5xx / network errors — the same posture as the
    Base44 REST client. A hard 4xx returns None immediately (no point
    retrying a bad path).
    """
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                return None
            last = e
        except Exception as e:  # network, timeout, parse
            last = e
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    return None


def league_news(league, limit=5):
    """Recent ESPN headlines for a league. Cached per-process (one call
    per cycle). Returns [] on any failure — the answer layer renders an
    honest 'headlines unavailable this cycle' line, never a stale or
    invented one.

    NOTE: league-level headlines, not team-filtered. Labeled as such
    downstream so nothing masquerades as team-specific research.
    """
    lg = str(league or "").lower()
    path = SPORT_PATHS.get(lg)
    if not path:
        return []
    if lg in _news_cache:
        return _news_cache[lg]
    data = fetch_url(
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/news?limit={limit}")
    items = []
    try:
        for a in (data or {}).get("articles", []):
            head = a.get("headline")
            if not head:
                continue
            desc = (a.get("description") or "").strip()
            if desc and len(desc) > 220:
                desc = desc[:217] + "..."
            items.append({"headline": head, "description": desc or None,
                          "published": a.get("published")})
    except Exception:
        items = []
    _news_cache[lg] = items[:limit]
    return _news_cache[lg]
