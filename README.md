# Nova External Worker

MarketNova's intelligence cycle, running **off the Base44 meter**.

## Why this exists

Every call into the Base44 backend is metered (integration credits). The old
on-platform ingestion burned the monthly budget on volume: hundreds of small
API calls per cycle. This worker does the heavy lifting externally — ESPN
fetches, odds polling, true-line computation, read generation — and touches
Base44 **only with batched delta writes**: a quiet board costs single-digit
API calls per cycle.

## What one cycle does

1. **Odds sync first** — the market source is **ESPN's scoreboard odds
   (DraftKings lines, free, no API account needed)**; an odds-api key is now
   OPTIONAL and only adds multi-book depth when set. This is the source of
   record for what belongs on the board: **games exist because markets
   exist**. New games are created only from events carrying real book
   prices; a game no book prices is not board material. Snapshot writes
   are upsert-keyed on (game, book, market) in the production OddsSnapshot
   schema (`book`, `home_line`/`away_line`, `over_line`/`under_line`,
   `home_price`/`away_price`) — both outcomes live inside one row, so the
   historic double-insert bug is structurally impossible. If the line is
   unchanged, zero rows are written. When a price moves, the old row is
   superseded (`is_current=false`) and one new current row inserted — line
   history is preserved and grows every cycle.
2. **ESPN sync second** — links games to ESPN identity (`provider_game_id`)
   and keeps status/scores live. ESPN never creates games. Matching:
   provider_game_id first, then canonical-abbr/name + kickoff-proximity
   fallback (league-aware alias map, accent-safe), only against games with
   no ESPN id. Terminal games (CANCELED / POSTPONED / SUSPENDED / DELAYED)
   are exempt from score reconciliation; final games never regress. One-side
   overlaps are refused and logged rather than guessed — duplicates are the
   disease this repo exists to prevent.
3. **Read generation** — resolves both teams' power ratings through the
   alias map, builds the independent true price via the embedded MarketNova
   kernel (`nova/kernel.py`, 638 lines encoding the Master Brain spec,
   53-check self-test run before every cycle), then applies the canonical
   decision gates. Hard sanity gate: a computed edge over 8 points is a
   data conflict, not an edge — the read goes to AWAITING DATA with the
   reason. Status taxonomy: PLAY / LEAN / MONITOR / PASS / NUMBER GONE /
   AWAITING DATA.
4. **Delta writes** — reads and games are only written when tracked fields
   actually changed (float-tolerant comparison, so API round-trip noise
   never causes phantom writes). `bet_*` recommendation fields are never
   touched (the frontend maps them).

A quiet board costs single-digit API calls per cycle.

## Deploy (GitHub Actions, ~5 minutes)

1. Create a **public** GitHub repo (public = free unlimited Actions minutes;
   a private repo also works on the free tier, ~2000 min/month, which a
   30-min cycle uses ~2000 — a public repo is recommended, or reduce to
   hourly).
2. Copy this folder's contents into the repo (the `.github/workflows/
   nova-cycle.yml` schedule comes with it — every 30 minutes, plus a
   kernel self-test before every cycle).
3. Add repo secrets (**Settings → Secrets and variables → Actions**):

   | Secret | Required | Value |
   |---|---|---|
   | `BASE44_SERVICE_TOKEN` | **yes** | In the Base44 editor: **Dashboard → API → Create access token** (personal access token — it signs calls to your app's entities REST API). If it's ever exposed, delete it and create a new one. |
   | `ODDS_API_KEY` | no | Optional multi-book depth via The Odds API. Without it, ESPN's free DraftKings odds are the sole market source. |
   | `LLM_API_KEY` | no | Optional richer narratives. Without it, reads use deterministic kernel-derived templates. |

4. Run it once by hand (**Actions → Nova cycle → Run workflow**) and watch
   the log — each phase prints its counts. Then it runs itself every 30 min.

Optional tuning via repo **variables** (env vars, not secrets):

| Variable | Default | Meaning |
|---|---|---|
| `NOVA_LEAGUES` | `nfl,ncaaf,nhl,nba,ncaab,wnba,mlb` | Comma-separated leagues to cycle |
| `NOVA_LOOKAHEAD_DAYS` | `2` | Board window ahead of now |
| `NOVA_DRY_RUN` | `0` | `1` = compute and log, write nothing |

## Local dry run

```bash
BASE44_SERVICE_TOKEN=... python3 run_cycle.py --dry-run
```

Computes the full plan against live data and writes nothing — use it to
verify changes before deploying.

## Game theory layer (juice pressure + line lifecycle)

Two doctrine-driven refinements in the read engine:

- **Juice pressure (Part XX):** books probe demand with juice before
  crossing numbers. When the number holds but the price moves against
  the read's value side, the read records `juice_pressure:
  against_value_side`, passes `sharp_resistance=1` into the kernel, and
  a quiet PASS upgrades to MONITOR (a repricing trigger per doctrine).
  Juice on the opposite side changes nothing.
- **Line lifecycle (Part XVII, timing is an edge):** every new read
  records `stage` (early >48h / mid 24-48h / late <24h) and
  `hours_to_close` in `data_quality_json`, so Performance can later
  measure whether edges taken early beat close more often.

## Offline full-cycle run (ZERO Base44 credits)

`tools/offline_run.py` runs the ENTIRE cycle — live ESPN fetches, odds
plan, ESPN reconcile, read generation, and the full write phase — against
local cached entity dumps instead of the Base44 API. Writes are recorded
to `offline_run_output.json` and never leave the machine. Use it to
verify code changes end-to-end when credits are exhausted.

```bash
# stage caches (any missing entity is treated as empty)
mkdir offline_cache
cp Game.dump.json offline_cache/Game.cache.json
BASE44_SERVICE_TOKEN=offline-noop python3 tools/offline_run.py offline_cache
```

Cache files: `Game.cache.json`, `TeamRating.cache.json`,
`MarketNovaRead.cache.json`, `OddsSnapshot.cache.json`. The runner also
validates structural invariants on the simulated plan (duplicate ids,
orphaned odds/read refs).

## Cutover (after 24h green)

The on-platform odds ingestion (Bovada writer, ~every 30 min) is still
active. Once this worker has run green for a day, the platform ingestion
**must be deactivated** so the two pipelines can't race each other
creating games — duplicates are the disease this repo exists to prevent.

## Status taxonomy

Reads publish the V3 standard taxonomy: PLAY / LEAN / MONITOR / PASS /
NUMBER GONE / AWAITING DATA. Missing ratings or odds, sanity-bound edges
(> 8 points = data conflict, not an edge), and unresolvable data all
degrade to AWAITING DATA with a reason — Nova never invents numbers.

## Kernel usage note

`kernel.decide()` defaults to `direction="under"`, which is for TOTALS.
For spread reads pass `"favorite"` or `"dog"` — the side the true line says
has value. Passing the default makes every spread verdict come out
NUMBER GONE (verified and fixed Oct 2026; don't regress it).

Two sanity gates run in the reads layer, both verified against live data:
1. Signed-edge gate (before the kernel): |true − market| on the same
   home-perspective scale. Over 8 points → AWAITING DATA as a
   ratings/market data conflict. Never use |abs(market)| − |abs(true)| —
   it collapses sign flips (a −6.5 market vs +14 true is a 20.5-point
   disagreement, not 7.5). This gate caught 22 stale/mis-rated games on
   first live audit.
2. Post-decide gate: any kernel verdict with an over-8 computed edge is
   forced to AWAITING DATA with the reason.

## Doctrine (from the Master Brain spec)

- Build the true price before reading the market; the market may explain,
  confirm, or invalidate a discrepancy, never create a wager by itself.
- Silence is not confirmation; missing data yields AWAITING DATA, never
  invented numbers.
- Historical states are immutable; corrections create new versions.
