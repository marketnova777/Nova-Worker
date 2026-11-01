"""One documented representation for MarketNova price fields.

Convention (owner-approved Oct 31 2026), enforced here and re-validated
server-side by publishCanonicalReads:

  SPREAD / TOTAL (point markets)
    marketnova_true_line      float, home-perspective signed line
    true_line_value           float, same units, home-perspective
    marketnova_true_probability  None  (a point line is not a probability)
    fair_price                None  (a point line is not an American price)
    edge_value + edge_unit    POINTS

  MONEYLINE (selection-priced market)
    marketnova_true_line      None
    true_line_value           None
    marketnova_true_probability   float 0..1, specific selection side
    fair_price                int American odds converted from that probability
    edge_value + edge_unit    PROBABILITY (probability points) or EV
                              (expected value per unit staked) - never mixed

Any payload that cannot satisfy this is rejected, not clipped. Extreme
American prices are legitimate when the probability is legitimate, so the
guard is on probability bounds and on internal consistency, not on the odds.
"""

POINT_MARKETS = {"spread", "total", "1h_spread", "1h_total", "2h_spread", "2h_total"}
PRICE_MARKETS = {"moneyline", "1h_moneyline", "2h_moneyline"}

PROB_MIN, PROB_MAX = 0.005, 0.995     # rejects 0/1 and nonsense
MAX_ABS_AMERICAN = 20000             # flags (does not clip) beyond this


def american_from_prob(p):
    """Fair American odds for probability p (0<p<1)."""
    p = float(p)
    if p >= 0.5:
        return int(round(-100.0 * p / (1.0 - p)))
    return int(round(100.0 * (1.0 - p) / p))


def prob_from_american(a):
    a = float(a)
    if a == 0:
        raise ValueError("American odds cannot be 0")
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def market_family(market_type):
    mt = str(market_type or "").lower()
    if mt in PRICE_MARKETS:
        return "price"
    if mt in POINT_MARKETS:
        return "point"
    return "point"          # unknown markets are treated as point-based


def normalize(payload):
    """Return (payload, warnings) with the canonical convention applied."""
    out = dict(payload)
    mt = str(out.get("market_type") or "").lower()
    fam = market_family(mt)
    warnings = []

    if fam == "price":
        # probability is the source of truth; fair_price derives from it
        p = out.get("marketnova_true_probability")
        if p is None:
            legacy = out.get("fair_price") or out.get("marketnova_true_line")
            if legacy is not None:
                try:
                    p = prob_from_american(legacy)
                    warnings.append(f"probability derived from legacy price {legacy}")
                except Exception:
                    p = None
        if p is not None:
            p = float(p)
            if not (PROB_MIN <= p <= PROB_MAX):
                warnings.append(f"probability {p} outside sanity band; left unset")
                p = None
        if p is not None:
            fair = american_from_prob(p)
            if abs(fair) > MAX_ABS_AMERICAN:
                warnings.append(f"extreme fair price {fair} consistent with probability {p:.4f}")
            out["marketnova_true_probability"] = round(p, 6)
            out["fair_price"] = fair
        out["marketnova_true_line"] = None
        out["true_line_value"] = None
        unit = str(out.get("edge_unit") or "").upper()
        if unit not in ("PROBABILITY", "EV"):
            out["edge_unit"] = "PROBABILITY"
            warnings.append(f"edge_unit {unit or 'missing'} coerced to PROBABILITY")
        if out.get("edge_value") is None and out.get("raw_edge") is not None:
            out["edge_value"] = out["raw_edge"]
    else:
        line = out.get("marketnova_true_line")
        if line is None:
            line = out.get("true_line_value")
        out["marketnova_true_line"] = line
        out["true_line_value"] = line
        out["marketnova_true_probability"] = None
        out["fair_price"] = None
        unit = str(out.get("edge_unit") or "").upper()
        if unit not in ("POINTS", "EV"):
            out["edge_unit"] = "POINTS"
            warnings.append(f"edge_unit {unit or 'missing'} coerced to POINTS")
        if out.get("edge_value") is None and out.get("raw_edge") is not None:
            out["edge_value"] = out["raw_edge"]
    return out, warnings


def validate(payload):
    """Server-side contract check. Returns (ok, errors)."""
    errs = []
    mt = str(payload.get("market_type") or "").lower()
    fam = market_family(mt)
    if not payload.get("game_id"):
        errs.append("missing game_id")
    if not mt:
        errs.append("missing market_type")
    if fam == "price":
        p = payload.get("marketnova_true_probability")
        if p is None:
            errs.append("moneyline requires marketnova_true_probability")
        elif not (PROB_MIN <= float(p) <= PROB_MAX):
            errs.append(f"probability {p} out of bounds")
        if payload.get("marketnova_true_line") is not None:
            errs.append("moneyline must carry null marketnova_true_line")
        if payload.get("fair_price") is None:
            errs.append("moneyline requires fair_price")
    else:
        if payload.get("marketnova_true_line") is None:
            errs.append(f"{mt or 'point market'} requires marketnova_true_line")
        if payload.get("fair_price") is not None:
            errs.append(f"{mt} must carry null fair_price")
    if str(payload.get("edge_unit") or "").upper() not in ("POINTS", "PROBABILITY", "EV"):
        errs.append("edge_unit must be POINTS, PROBABILITY or EV")
    return (not errs), errs
