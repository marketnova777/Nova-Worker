/**
 * publishCanonicalReads — the ONLY trusted publisher of current canonical
 * MarketNovaRead assessments on behalf of the external worker.
 *
 * Deploy this in the MarketNova app (Backend functions). Runtime secrets:
 *   NOVA_PUBLISH_SECRET   shared secret the worker sends in X-Nova-Publish-Secret
 *
 * Security posture:
 *  - secret compared with a constant-time compare, never logged, never returned
 *  - writer provenance is stamped server-side; caller-supplied writer_id ignored
 *  - no anonymous writes: entity stays closed, this function is the door
 *  - rejects payloads that violate the canonical convention instead of clipping
 *  - idempotent: one current row per (game_id, market_type); prior currents are
 *    superseded in the same pass, so repeated publication cannot double-write
 *  - bounded batch (<=100 reads per call) so the caller paces under quota
 */
import { createClientFromRequest } from 'npm:@base44/sdk@0.8.49';

const WRITER_ID = 'nova-worker-github';
const POINT_MARKETS = new Set(['spread', 'total', '1h_spread', '1h_total', '2h_spread', '2h_total']);
const PRICE_MARKETS = new Set(['moneyline', '1h_moneyline', '2h_moneyline']);
const MAX_BATCH = 100;
const PROB_MIN = 0.005, PROB_MAX = 0.995;

function timingSafeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function validateRead(r) {
  const errs = [];
  const mt = String(r.market_type || '').toLowerCase();
  const isPrice = PRICE_MARKETS.has(mt);
  if (!r.game_id) errs.push('missing game_id');
  if (!mt) errs.push('missing market_type');
  if (isPrice) {
    const p = r.marketnova_true_probability;
    if (p == null) errs.push('moneyline requires marketnova_true_probability');
    else if (!(p >= PROB_MIN && p <= PROB_MAX)) errs.push(`probability ${p} out of bounds`);
    if (r.marketnova_true_line != null) errs.push('moneyline must carry null marketnova_true_line');
    if (r.fair_price == null) errs.push('moneyline requires fair_price');
  } else if (POINT_MARKETS.has(mt)) {
    if (r.marketnova_true_line == null) errs.push(`${mt} requires marketnova_true_line`);
    if (r.fair_price != null) errs.push(`${mt} must carry null fair_price`);
  } else {
    errs.push(`unknown market_type ${mt}`);
  }
  const unit = String(r.edge_unit || '').toUpperCase();
  if (!['POINTS', 'PROBABILITY', 'EV'].includes(unit)) errs.push('edge_unit invalid');
  if (isPrice && unit === 'POINTS') errs.push('moneyline edge_unit cannot be POINTS');
  if (!isPrice && unit === 'PROBABILITY') errs.push('point market edge_unit cannot be PROBABILITY');
  return errs;
}

Deno.serve(async (req) => {
  if (req.method !== 'POST') return Response.json({ ok: false, error: 'method' }, { status: 405 });

  const expected = Deno.env.get('NOVA_PUBLISH_SECRET') || '';
  if (!expected) return Response.json({ ok: false, error: 'publisher not configured' }, { status: 503 });
  const supplied = req.headers.get('X-Nova-Publish-Secret') || '';
  if (!timingSafeEqual(supplied, expected)) {
    return Response.json({ ok: false, error: 'unauthorized' }, { status: 401 });
  }

  let body;
  try { body = await req.json(); } catch { return Response.json({ ok: false, error: 'bad json' }, { status: 400 }); }
  const reads = Array.isArray(body?.reads) ? body.reads : [];
  if (!reads.length) return Response.json({ ok: true, accepted: 0, rejected: [] });
  if (reads.length > MAX_BATCH) {
    return Response.json({ ok: false, error: `batch too large (max ${MAX_BATCH})` }, { status: 413 });
  }

  // Server-side provenance and contract enforcement. A caller-supplied
  // writer_id or is_current is never trusted.
  const clean = [], rejected = [];
  for (const r of reads) {
    const errs = validateRead(r);
    if (errs.length) { rejected.push({ id: r.id || null, game_id: r.game_id || null, errors: errs }); continue; }
    const { writer_id, is_current, ...rest } = r;
    clean.push({ ...rest, writer_id: WRITER_ID, is_current: true });
  }

  const base44 = createClientFromRequest(req);
  let accepted = 0;

  for (const rec of clean) {
    try {
      // Idempotent slot: one current row per (game_id, market_type).
      const prior = await base44.asServiceRole.entities.MarketNovaRead.filter(
        { game_id: rec.game_id, market_type: rec.market_type, is_current: true }, '-updated_date', 20,
      );
      const keep = rec.id || null;
      const stale = prior.filter((p) => p.id !== keep).map((p) => p.id);

      if (stale.length) {
        await base44.asServiceRole.entities.MarketNovaRead.bulkUpdate(
          stale.map((id) => ({ id, is_current: false })),
        );
      }
      if (keep) {
        await base44.asServiceRole.entities.MarketNovaRead.update(keep, rec);
      } else {
        await base44.asServiceRole.entities.MarketNovaRead.create(rec);
      }
      accepted += 1;
    } catch (e) {
      rejected.push({ id: rec.id || null, game_id: rec.game_id, errors: [`write failed: ${e.message || e}`] });
    }
  }

  const result = { ok: accepted > 0 || rejected.length === 0, accepted, rejected, writer_id: WRITER_ID };
  return Response.json(result, { status: result.ok ? 200 : 422 });
});
