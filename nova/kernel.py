"""
MarketNova Execution Kernel — implements MarketNova_Master_Brain_Update_v1.md (sections 3-28, 32).

Canonical spec: .agents/rules/MarketNova_Master_Brain_Update_v1.md (read-only, versioned).
This module is the code-level encoding of that spec. Every rule carries its spec section
reference so behavior is auditable against the document.

Architecture:
  MarketNova   = independent game + true-price engine        (sections 3, 8-12)
  FastestPayout = sportsbook/market-intelligence engine       (sections 2, 4-6)
  Vegas Danny  = execution + price-discipline engine          (sections 7, 13, 14)

CORE_INVARIANT (sec 0): market behavior may explain/confirm/weaken/invalidate an independently
derived true-price discrepancy, but public percentages, RLM, sportsbook psychology, or "trap"
interpretation may never independently create a wager.
"""

import uuid
from dataclasses import dataclass, field as dfield
from typing import Any, Optional


# ============================================================
# Section 15 — State Machine + Section 26 — Rejection Codes
# ============================================================

STATES = ["OPEN", "CURRENT", "LIVE", "HALFTIME", "SECOND_HALF", "REPRICE", "FINAL"]
ALLOWED_TRANSITIONS = {
    "OPEN": ["CURRENT", "REPRICE"],
    "CURRENT": ["CURRENT", "REPRICE", "LIVE", "FINAL"],
    "LIVE": ["LIVE", "REPRICE", "HALFTIME", "FINAL"],
    "HALFTIME": ["HALFTIME", "REPRICE", "SECOND_HALF", "FINAL"],
    "SECOND_HALF": ["SECOND_HALF", "REPRICE", "FINAL"],
    "REPRICE": ["CURRENT", "LIVE", "HALFTIME", "SECOND_HALF", "FINAL"],
    "FINAL": [],
}

REJECTION_SEVERITY = {
    "STATE_TRANSITION_NOT_ALLOWED": "CRITICAL",
    "FINAL_STATE_LOCKED": "CRITICAL",
    "EVENT_ID_MISMATCH": "CRITICAL",
    "DUPLICATE_EVENT": "INFO",
    "OUT_OF_ORDER_EVENT": "WARNING",
    "OPEN_MARKET_IMMUTABLE": "CRITICAL",
    "HISTORICAL_STATE_IMMUTABLE": "CRITICAL",
    "REPRICE_WITHOUT_CAUSE": "CRITICAL",
    "MARKET_ONLY_REPRICE_ATTEMPT": "WARNING",
    "SCORE_REGRESSION_WITHOUT_CORRECTION": "CRITICAL",
    "TRUE_PRICE_REQUIRED": "CRITICAL",
    "PLAY_GATE_NOT_SATISFIED": "WARNING",
    "NUMBER_NO_LONGER_PLAYABLE": "INFO",
    "CROSS_SOURCE_CONFLICT": "CRITICAL",
    "HINDSIGHT_MUTATION_ATTEMPT": "CRITICAL",
    "FUTURE_DATA_LEAKAGE": "CRITICAL",
    "CONFIRM_VALUE_MISMATCH": "WARNING",
    "EXCESSIVE_REPRICE_DELTA": "WARNING",
}


class GameStateMachine:
    """Section 15. FINAL is terminal; every rejection is logged with its section-26 code."""

    def __init__(self, trace: Optional[list] = None):
        self.state = "OPEN"
        self.history = [("OPEN", None)]
        self.trace = trace if trace is not None else []

    def transition(self, new_state: str, cause_event_id: Optional[str] = None) -> bool:
        if self.state == "FINAL":
            self.trace.append(("REJ:FINAL_STATE_LOCKED", f"attempted {self.state} -> {new_state}; FINAL is terminal"))
            self.trace.append(("REJ:STATE_TRANSITION_NOT_ALLOWED", f"{self.state} -> {new_state} rejected"))
            return False
        if new_state not in ALLOWED_TRANSITIONS[self.state]:
            self.trace.append(("REJ:STATE_TRANSITION_NOT_ALLOWED", f"{self.state} -> {new_state} not in allowed set"))
            return False
        if new_state == "REPRICE" and not cause_event_id:
            self.trace.append(("REJ:REPRICE_WITHOUT_CAUSE", "REPRICE requires cause_event_id (section 17)"))
            return False
        self.state = new_state
        self.history.append((new_state, cause_event_id))
        return True


# ============================================================
# Sections 18-22 — Event Sourcing, Corrections, Authority, Replay
# ============================================================

FIELD_AUTHORITY = {  # section 20, rank 0 = highest authority
    "score": {"OFFICIAL_LEAGUE": 0, "PRIMARY_STATS_PROVIDER": 1},
    "game_clock": {"OFFICIAL_LEAGUE": 0, "STADIUM_FEED": 1},
    "possession": {"PLAY_BY_PLAY_PROVIDER": 0, "OFFICIAL_LEAGUE": 1},
    "sportsbook_price": {"SPORTSBOOK_DIRECT_FEED": 0},
    "injury_status": {"OFFICIAL_TEAM": 0, "LEAGUE_INJURY_REPORT": 1},
}
DEFAULT_AUTHORITY_RANK = 90  # unranked source for a field


@dataclass
class Patch:
    field_name: str
    operation: str          # SET | CONFIRM | CLEAR | NO_CLAIM  (section 19)
    value: Any = None
    source_id: str = ""
    source_sequence: int = 0


@dataclass
class GameEvent:
    event_type: str
    source_id: str
    source_sequence: int
    event_time: str
    patches: list
    received_at: str = ""
    event_id: str = dfield(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str = ""


class EventStore:
    """
    Sections 18-22. Original events immutable; corrections append-only; effective value =
    claim with highest source_sequence (ties broken by authority rank per field).
    SILENCE_IS_NOT_CONFIRMATION: fields absent from an event leave state untouched.
    """

    def __init__(self, trace: Optional[list] = None):
        self.events: list[GameEvent] = []
        self.claims: dict[str, list[dict]] = {}   # field -> ordered claim log
        self.effective: dict[str, dict] = {}       # field -> effective value record
        self.decision_versions: list[dict] = []
        self._seen_keys: set = set()
        self.trace = trace if trace is not None else []

    def _rank(self, field_name: str, source_id: str) -> int:
        return FIELD_AUTHORITY.get(field_name, {}).get(source_id, DEFAULT_AUTHORITY_RANK)

    def append(self, event: GameEvent) -> bool:
        key = event.idempotency_key or event.event_id
        if key in self._seen_keys:
            self.trace.append(("REJ:DUPLICATE_EVENT", f"idempotency key {key} seen; ignored"))
            return False
        self._seen_keys.add(key)
        self.events.append(event)
        for p in event.patches:
            if p.operation == "NO_CLAIM":
                continue  # silence is not confirmation
            self.claims.setdefault(p.field_name, []).append({
                "value": p.value, "source": p.source_id, "seq": p.source_sequence,
                "op": p.operation, "authority": self._rank(p.field_name, p.source_id),
                "event_id": event.event_id, "received_at": event.received_at,
            })
            self._resolve(p.field_name)
        return True

    def _resolve(self, field_name: str):
        claims = self.claims[field_name]
        latest = claims[-1]
        # CROSS_SOURCE_CONFLICT: same sequence, conflicting values from different sources
        for c in claims[:-1]:
            if c["seq"] == latest["seq"] and c["value"] != latest["value"] and c["source"] != latest["source"]:
                self.trace.append(("REJ:CROSS_SOURCE_CONFLICT",
                                   f"{field_name}: same-seq conflict {c['source']}={c['value']} vs {latest['source']}={latest['value']}; authority decides"))
        best = max(claims, key=lambda c: (c["seq"], -c["authority"]))
        cur = self.effective.get(field_name)
        if cur and latest["seq"] < cur["seq"] and latest is not best:
            # older sequence arrived late: replay history only (SOURCE_SEQUENCE_DOMINATES_ARRIVAL)
            self.trace.append(("RULE:SELECTIVE_REPLAY",
                               f"{field_name}: late seq {latest['seq']} from {latest['source']} modifies historical reconstruction, not effective claim (seq {cur['seq']})"))
            return
        if latest["op"] == "CONFIRM":
            if cur is None or latest["value"] != cur["value"]:
                self.trace.append(("REJ:CONFIRM_VALUE_MISMATCH",
                                   f"{field_name}: CONFIRM {latest['value']} != effective {cur['value'] if cur else 'unset'}"))
            else:
                cur.setdefault("confirmed_by", []).append(latest["source"])
            return
        if cur and best["seq"] < cur["seq"]:
            # older sequence arrived late: replay history only (SOURCE_SEQUENCE_DOMINATES_ARRIVAL)
            self.trace.append(("RULE:SELECTIVE_REPLAY",
                               f"{field_name}: late seq {best['seq']} modifies historical reconstruction, not effective claim (seq {cur['seq']})"))
            return
        if cur and field_name == "score" and best["value"] is not None and cur["value"] is not None \
                and best["value"] < cur["value"] and best["op"] == "SET":
            self.trace.append(("REJ:SCORE_REGRESSION_WITHOUT_CORRECTION",
                               f"{field_name} {cur['value']} -> {best['value']} without correction event"))
        self.effective[field_name] = {
            "value": None if best["op"] == "CLEAR" else best["value"],
            "source": best["source"], "seq": best["seq"], "authority": best["authority"],
            "confirmed_by": list(cur.get("confirmed_by", [])) if cur and cur["value"] == best["value"] else [],
        }

    def get(self, field_name: str, default=None):
        e = self.effective.get(field_name)
        return default if e is None else e["value"]

    def record_decision(self, decision: dict) -> str:
        """Section 22: decisions are versioned; actual vs replayed kept separate."""
        d = dict(decision)
        d["historical_status"] = "ACTUALLY_EMITTED"
        self.decision_versions.append(d)
        return f"decision-v{len(self.decision_versions)}"

    def record_replayed_decision(self, decision: dict) -> str:
        d = dict(decision)
        d["historical_status"] = "COUNTERFACTUAL_RECONSTRUCTION"
        self.decision_versions.append(d)
        return f"replay-v{len(self.decision_versions)}"


# ============================================================
# Section 3 — True-Price Construction (built BEFORE market read)
# ============================================================

@dataclass
class TruePrice:
    spread: float            # home-perspective (negative = home favorite)
    total: float
    home_win_prob: float = 0.5
    projected_home: float = 0.0
    projected_away: float = 0.0
    spread_sigma: float = 1.5
    total_sigma: float = 2.5
    calculated_before_market_comparison: bool = True


def build_true_price(team_power_gap: float, expected_pace_points: float, **adjustments) -> TruePrice:
    """
    Independent construction (section 3). team_power_gap: positive = home stronger.
    expected_pace_points: neutral-environment combined expected score.
    adjustments: home_field, weather_delta, injury_delta (points).
    """
    spread = -(team_power_gap + adjustments.get("home_field", 2.5)
               + adjustments.get("injury_delta", 0.0))
    total = expected_pace_points + adjustments.get("weather_delta", 0.0)
    half = total / 2.0
    margin = -spread
    return TruePrice(spread=round(spread, 2), total=round(total, 2),
                     projected_home=round(half + margin / 2, 2),
                     projected_away=round(half - margin / 2, 2))


# ============================================================
# Section 7 — Price Thresholds (Vegas Danny)
# ============================================================

PRICE_THRESHOLDS = {
    "pregame_spread": {"pass_lt": 1.0, "monitor_gte": 1.0, "lean_gte": 1.5, "play_gte": 2.0, "strong_gte": 3.0},
    "first_half":     {"pass_lt": 1.0, "lean_gte": 1.0, "play_gte": 1.5, "strong_gte": 2.0},
    "second_half":    {"pass_lt": 1.5, "monitor_gte": 1.5, "lean_gte": 2.0, "play_gte": 3.0, "strong_gte": 4.0},
    "moneyline":      {"pass_lt": 2.0, "monitor_gte": 2.0, "lean_gte": 3.0, "play_gte": 4.0, "strong_gte": 6.0},
}


def classify_threshold(raw_edge: float, market: str) -> str:
    t = PRICE_THRESHOLDS[market]
    if raw_edge >= t["strong_gte"]:
        return "STRONG_DISCREPANCY"
    if raw_edge >= t["play_gte"]:
        return "PLAY"
    if raw_edge >= t["lean_gte"]:
        return "LEAN"
    if raw_edge >= t.get("monitor_gte", t["pass_lt"]):
        return "MONITOR"
    return "PASS"


def minimum_playable_price(true_line: float, market: str, direction: str = "under") -> float:
    """Vegas Danny: the worst number we would still execute at.
    under/favorite: worse number is HIGHER -> min playable = true + threshold
    over/dog: worse number is LOWER -> min playable = true - threshold"""
    thr = PRICE_THRESHOLDS[market]["play_gte"]
    return true_line + thr if direction in ("under", "favorite") else true_line - thr


# ============================================================
# Section 4 — Market Psychology + Public Bias (FastestPayout)
# ============================================================

PUBLIC_BIAS_THRESHOLDS = [(80, 4), (70, 3), (65, 2), (60, 1), (50, 0)]


def public_bias_score(public_ticket_pct: float) -> int:
    for threshold, score in PUBLIC_BIAS_THRESHOLDS:
        if public_ticket_pct >= threshold:
            return score
    return 0


def psychology_score(narrative_flags: list, true_price_agrees_with_public: bool = False,
                     market_already_corrected: bool = False) -> int:
    """Score -5..5. Positive = narrative inflating the public side. EVIDENCE ONLY (section 4)."""
    score = 0
    for flag in narrative_flags:
        score += 1
    if market_already_corrected:
        score -= 2
    if true_price_agrees_with_public:
        score -= 3   # fundamentals validate narrative
    return max(-5, min(5, score))


# ============================================================
# Section 5 — Line Movement + Sharp Resistance (FastestPayout)
# ============================================================

MOVEMENT_ENUM = ["FUNDAMENTAL_REPRICE", "PUBLIC_PRESSURE", "SHARP_CONFIRMATION", "REVERSE_LINE_MOVEMENT",
                "LINE_FREEZE", "BUYBACK", "KEY_NUMBER_DEFENSE", "LIQUIDITY_NOISE", "UNKNOWN"]


def classify_movement(open_line: float, current_line: float, public_pct: float,
                      move_toward_model: Optional[bool] = None, fundamental_news: bool = False) -> tuple:
    """Returns (classification, movement_score -5..5 relative to OUR position)."""
    moved = round(abs(current_line - open_line), 2)
    direction_sign = 1 if current_line >= open_line else -1
    if fundamental_news:
        cls, score = "FUNDAMENTAL_REPRICE", 0
    elif public_pct >= 65 and moved <= 0.5:
        cls, score = "LINE_FREEZE", (1 if move_toward_model else (-1 if move_toward_model is False else 0))
    elif public_pct >= 65 and direction_sign < 0:
        cls, score = "REVERSE_LINE_MOVEMENT", (-3 if moved >= 1.0 else -1) if move_toward_model is False else 2
    elif moved <= 0.5:
        cls, score = "LIQUIDITY_NOISE", 0
    elif move_toward_model:
        cls = "SHARP_CONFIRMATION" if moved >= 1.0 else "BUYBACK"
        score = 5 if moved >= 1.0 else 1
    else:
        cls, score = "PUBLIC_PRESSURE", -3 if moved >= 1.0 else -1
    return cls, score


def sharp_resistance_score(public_pct: float, spread_move: float, juice_move: bool = False,
                            move_against_public: bool = False, fundamental_news: bool = False,
                            key_number_defense: bool = False, multi_book: bool = False) -> tuple:
    """Section 5 SHARP_RESISTANCE_RULES. Returns (type, score 0-5, fired_rule)."""
    if key_number_defense:
        return "KEY_NUMBER_DEFENSE", 4, "key_number_defense"
    if public_pct >= 75 and move_against_public and abs(spread_move) >= 1.0 and multi_book:
        return "REVERSE_LINE_MOVEMENT", 4, "strong_reverse_move"
    if public_pct >= 65 and move_against_public and not fundamental_news:
        return "REVERSE_LINE_MOVEMENT", 2, "reverse_line_move"
    if public_pct >= 65 and abs(spread_move) <= 0.5 and juice_move:
        return "JUICE_ONLY_MOVE", 2, "heavy_public_juice_only"
    if public_pct >= 65 and abs(spread_move) <= 0.5:
        return "LINE_FREEZE", 1, "heavy_public_no_move"
    return "NONE", 0, ""


# ============================================================
# Section 6 — Key Numbers
# ============================================================

NFL_KEY_NUMBERS = {3: 5, 7: 4, 10: 2, 14: 2, 4: 1, 6: 1}
CFB_KEY_NUMBERS = {3: 5, 7: 4, 10: 2, 14: 2}
SECOND_HALF_TOTAL_KEYS = {20: 5, 21: 4, 24: 5, 17: 2, 27: 2, 14: 1, 16: 1, 19: 1, 22.5: 1}


def key_number_quality(number: float, market: str = "spread", league: str = "nfl") -> int:
    if market == "second_half_total":
        return max((v for k, v in SECOND_HALF_TOTAL_KEYS.items() if abs(number - k) < 0.01), default=0)
    table = NFL_KEY_NUMBERS if league == "nfl" else CFB_KEY_NUMBERS
    return table.get(int(round(number)), 0) if float(number).is_integer() else 0


def key_number_crossing(entry_number: float, current_number: float, market: str = "spread",
                       league: str = "nfl", direction: str = "under") -> tuple:
    """Section 6 KEY_NUMBER_UPDATE. Returns (crossed, favorable, keys_crossed)."""
    keys = list(NFL_KEY_NUMBERS if league == "nfl" else CFB_KEY_NUMBERS) if market == "spread" \
        else list(SECOND_HALF_TOTAL_KEYS)
    lo, hi = sorted((entry_number, current_number))
    crossed = [k for k in keys if lo < k < hi]
    if not crossed:
        return False, False, []
    favorable = (direction == "under" and current_number > entry_number) or \
                (direction != "under" and current_number < entry_number)
    return True, favorable, crossed


# ============================================================
# Sections 9-12 — Sustainability, Archetypes, Game Theory, 2H Regression
# ============================================================

DISTORTION_SOURCES = ["PICK_SIX", "FUMBLE_RETURN_TD", "SPECIAL_TEAMS_TD", "SHORT_FIELD",
                      "BUSTED_EXPLOSIVE", "RED_ZONE_VARIANCE", "THIRD_DOWN_VARIANCE",
                      "MISSED_FIELD_GOALS", "FAILED_FOURTH_DOWN", "PENALTY_ERASED_SCORE", "OTHER"]


@dataclass
class FirstHalfState:
    points: int
    drive_ypp: float                # normal-drive yards per play
    possessions: int
    distortion_points: int          # points created by DISTORTION_SOURCES
    missed_fg: int = 0
    rz_trips: int = 0
    rz_tds: int = 0
    lead: int = 0                   # home-perspective lead at half
    ypp_margin: float = 0.0         # home YPP minus away YPP


def classify_sustainability(fh: FirstHalfState) -> str:
    """Section 9. Low score != Under env; high score != Over env."""
    share = fh.distortion_points / fh.points if fh.points else 0.0
    if share >= 0.45:
        return "VERY_LOW"
    if share >= 0.30:
        return "LOW"
    if share <= 0.10 and fh.drive_ypp >= 5.0:
        return "VERY_HIGH"
    if share <= 0.20 and fh.drive_ypp >= 4.5:
        return "HIGH"
    return "NEUTRAL"


def classify_halftime_archetype(fh: FirstHalfState, market_corrected: bool = False,
                               info_conflict: bool = False) -> str:
    """Section 10, priority order."""
    if market_corrected or info_conflict:
        return "NO_PLAY"
    if abs(fh.lead) >= 17:
        return "GAME_STATE_CONTAMINATION"
    if fh.points >= 24 and fh.distortion_points / max(fh.points, 1) >= 0.35:
        return "FALSE_SCORING_UNDER"
    if fh.points <= 14 and fh.drive_ypp >= 5.0 and (fh.missed_fg + max(0, fh.rz_trips - fh.rz_tds)) >= 2:
        return "SUPPRESSED_SCORING_OVER"
    if abs(fh.ypp_margin) >= 1.8:
        return "CONTROL_SIDE_HOME" if fh.ypp_margin > 0 else "CONTROL_SIDE_AWAY"
    return "UNCLASSIFIED"


def game_theory_read(lead: int) -> dict:
    """Section 11."""
    if abs(lead) >= 17:
        script, pace = "BLOWOUT", "MUCH_SLOWER"
    elif lead >= 10:
        script, pace = "CLOCK_KILL", "SLOWER"
    elif lead <= -10:
        script, pace = "COMEBACK_MODE", "FASTER"
    elif abs(lead) <= 3:
        script, pace = "NEUTRAL", "SAME"
    else:
        script, pace = ("FAVORITE_CONTROL" if lead > 0 else "UNDERDOG_CONTROL"), "SAME"
    return {
        "script": script,
        "expected_2h_pace": pace,
        "trailing_team_urgency": ("EXTREME" if lead <= -14 else "HIGH" if lead <= -7 else "MEDIUM" if lead < 0 else "LOW"),
        "leading_team_conservatism": ("HIGH" if lead >= 14 else "MEDIUM" if lead >= 7 else "LOW"),
        "substitution_risk": "HIGH" if abs(lead) >= 17 else "MEDIUM" if abs(lead) >= 10 else "LOW",
        "possession_advantage_2h": "UNKNOWN",
    }


def true_2h_total(baseline_remaining_scoring: float, pace_adj: float = 0.0, regression_adj: float = 0.0,
                 game_state_adj: float = 0.0, turnover_distortion: float = 0.0,
                 explosive_distortion: float = 0.0, rz_adj: float = 0.0, weather_adj: float = 0.0,
                 injury_adj: float = 0.0, substitution_adj: float = 0.0, possession_adj: float = 0.0) -> float:
    """Section 12 formula, verbatim structure."""
    return round(baseline_remaining_scoring + pace_adj + regression_adj + game_state_adj
                 - turnover_distortion - explosive_distortion + rz_adj + weather_adj
                 + injury_adj + substitution_adj + possession_adj, 2)


# ============================================================
# Sections 13-14, 32 — Decision Gates + Output Contract (kernel apex)
# ============================================================

@dataclass
class DecisionOutput:
    marketnova_true_line: float
    market_line: float
    raw_edge: float
    key_number_quality: int
    market_psychology: int
    public_bias: int
    sharp_resistance: int
    game_state_support: str
    regression_support: str
    model_uncertainty: str
    status: str
    minimum_playable_price: float
    fair_price: float
    ideal_entry_price: float
    invalidators: list
    gates_fired: list
    note: str = ""


def decide(*, true_price: TruePrice, market_line: float,
           market: str = "pregame_spread", direction: str = "under",
           information_quality: str = "OK",           # OK | DEGRADED | CONFLICTED
           public_pct: float = 50.0, psychology: int = 0,
           movement_score: int = 0, sharp_resistance: int = 0,
           supporting_modules: Optional[list] = None,
           key_crossed_unfavorably: bool = False,
           prior_playable_edge: bool = False,
           invalidators: Optional[list] = None,
           trace: Optional[list] = None) -> DecisionOutput:
    """
    Canonical Execution Kernel (section 32) with Decision Gates (section 13).
    supporting_modules: subset of ["psychology","sharp_resistance","regression","game_theory",
    "key_number","sustainability","game_state","talent"] that AGREE with the position.
    """
    gates = []
    t = trace if trace is not None else []
    mods = supporting_modules or []

    # GATE 1 — TRUE_PRICE_REQUIRED
    if true_price is None or not getattr(true_price, "calculated_before_market_comparison", False):
        gates.append("TRUE_PRICE_REQUIRED")
        t.append(("GATE:TRUE_PRICE_REQUIRED", "no independent true price -> NO_MODEL"))
        return DecisionOutput(0.0, market_line, 0.0, 0, 0, 0, 0, "NONE", "NONE",
                              "HIGH", "NO_MODEL", 0.0, 0.0, 0.0, [], gates)
    # GATE 2 — CRITICAL_INFORMATION
    if information_quality in ("DEGRADED", "CONFLICTED"):
        gates.append("CRITICAL_INFORMATION")
        t.append(("GATE:CRITICAL_INFORMATION", f"information_quality={information_quality} -> INFORMATION_INCOMPLETE"))
        return DecisionOutput(0.0, market_line, 0.0, 0, 0, 0, 0, "NONE", "NONE",
                              "HIGH", "INFORMATION_INCOMPLETE", 0.0, 0.0, 0.0, [], gates)
    # raw edge (points)
    if market in ("pregame_spread",):
        raw_edge = round(abs(abs(market_line) - abs(true_price.spread)), 2)
    else:
        raw_edge = round(abs(market_line - true_price.total), 2)
    edge_score = raw_edge * 2          # composite 0-10 (MINIMUM_EDGE gate operates on this)
    # GATE 4/5 — PUBLIC/PSYCHOLOGY_CANNOT_CREATE_PLAY
    fundamental_mods = [m for m in mods if m in ("regression", "game_theory", "sustainability", "talent", "game_state")]
    only_public = (psychology >= 2 or public_pct >= 65) and not fundamental_mods and edge_score < 4
    if only_public:
        gates.append("PUBLIC_CANNOT_CREATE_PLAY")
        t.append(("GATE:PUBLIC_CANNOT_CREATE_PLAY", "public/psychology is the only positive signal -> PASS"))
    # GATE 6 — STRONG_MARKET_CONTRADICTION
    if movement_score <= -3:
        gates.append("STRONG_MARKET_CONTRADICTION")
        t.append(("GATE:STRONG_MARKET_CONTRADICTION", f"movement_score {movement_score} <= -3 -> REPRICE_REQUIRED"))
    # GATE 7 — KEY_NUMBER_CROSSED
    if key_crossed_unfavorably:
        gates.append("KEY_NUMBER_CROSSED")
        t.append(("GATE:KEY_NUMBER_CROSSED", "unfavorable key crossing -> RECOMPUTE_EV, INVALIDATE_OLD_RECOMMENDATION"))
    # threshold band + minimum playable price (Vegas Danny)
    band = classify_threshold(raw_edge, market)
    base_line = true_price.total if direction in ("under", "over") else true_price.spread
    mpp = minimum_playable_price(base_line, market, direction)
    current_playable = (market_line >= mpp) if direction in ("under", "favorite") else (market_line <= mpp)
    # status precedence
    if movement_score <= -3:
        status = "REPRICE_REQUIRED"
    elif only_public:
        status = "PASS"
    elif not current_playable and prior_playable_edge:
        # spec sec 13 NUMBER_GONE + sec 38: thesis may remain correct but the market removed the value
        status = "NUMBER_GONE"
        gates.append("NUMBER_GONE")
        t.append(("GATE:NUMBER_GONE", f"market {market_line} worse than minimum playable {mpp}; prior playable edge existed -> NUMBER_GONE"))
    elif edge_score < 4:
        status = "PASS"
        gates.append("MINIMUM_EDGE")
        t.append(("GATE:MINIMUM_EDGE", f"edge_score {edge_score} < 4 -> PASS"))
    elif not current_playable:
        status = "NUMBER_GONE"
        gates.append("NUMBER_GONE")
        t.append(("GATE:NUMBER_GONE", f"market {market_line} worse than minimum playable {mpp} -> NUMBER_GONE"))
    elif band in ("PLAY", "STRONG_DISCREPANCY"):
        # v1.1 amendment (Oct 9 2026, Liu 2026 finding: markets price
        # momentum/streak info efficiently; its signal value does not
        # overcome the vig): psychology is context, never conviction —
        # it does NOT count toward the PLAY support requirement.
        credit_mods = [m for m in mods if m != "psychology"]
        if len(credit_mods) < 2:                              # GATE 8 — PLAY_REQUIRES_SUPPORT
            status = "LEAN"
            gates.append("PLAY_REQUIRES_SUPPORT")
            t.append(("GATE:PLAY_REQUIRES_SUPPORT", f"only {len(credit_mods)} creditable supporting module(s); PLAY downgraded to LEAN"))
        elif edge_score >= 8 and len(credit_mods) >= 3 and movement_score > -3:   # GATE 9 — STRONG_DISCREPANCY
            status = "STRONG_DISCREPANCY"
            gates.append("STRONG_DISCREPANCY")
        else:
            status = "PLAY"
    else:
        status = band
    kpq = key_number_quality(market_line, "second_half_total" if market == "second_half" else "spread")
    return DecisionOutput(
        marketnova_true_line=base_line, market_line=market_line, raw_edge=raw_edge,
        key_number_quality=kpq, market_psychology=psychology,
        public_bias=public_bias_score(public_pct), sharp_resistance=sharp_resistance,
        game_state_support="SUPPORTED" if "game_state" in mods else "NONE",
        regression_support="SUPPORTED" if "regression" in mods else "NONE",
        model_uncertainty="LOW" if true_price.total_sigma <= 2 else "MODERATE",
        status=status, minimum_playable_price=mpp,
        fair_price=base_line,
        ideal_entry_price=round(base_line + PRICE_THRESHOLDS[market]["strong_gte"], 2) if direction in ("under", "favorite")
        else round(base_line - PRICE_THRESHOLDS[market]["strong_gte"], 2),
        invalidators=invalidators or [], gates_fired=gates,
    )


# ============================================================
# Section 17 — Reprice Rules
# ============================================================

REPRICE_CAUSES = ["MAJOR_INJURY", "QB_CHANGE", "STARTER_CHANGE", "WEATHER_CHANGE", "SCORE_EVENT",
                  "TURNOVER", "RED_ZONE_RESULT", "MAJOR_FOURTH_DOWN", "EJECTION",
                  "SUBSTITUTION_CHANGE", "DATA_CORRECTION", "MARKET_CONTRADICTION_REVIEW",
                  "MATERIAL_STATISTICAL_UPDATE", "POSSESSION_STATE_CHANGE", "GAME_STATE_CHANGE"]


def reprice(machine: GameStateMachine, cause_event_id: str, cause_type: str,
            affected_inputs: list) -> bool:
    """REPRICE_RULE: requires cause_event_id, cause_type, affected_model_inputs."""
    if cause_type not in REPRICE_CAUSES:
        machine.trace.append(("REJ:REPRICE_WITHOUT_CAUSE", f"cause_type {cause_type} not a valid reprice cause"))
        return False
    if machine.state == "FINAL":
        machine.trace.append(("REJ:FINAL_STATE_LOCKED", "cannot reprice a FINAL game"))
        return False
    ok = machine.transition("REPRICE", cause_event_id=cause_event_id)
    if ok:
        machine.trace.append(("RULE:REPRICE", f"cause={cause_type} affected_inputs={affected_inputs}"))
    return ok


def attempt_market_only_reprice(machine: GameStateMachine, market_move_only: bool = True) -> bool:
    """Market movement alone triggers contradiction review, never an automatic true-price change."""
    if market_move_only:
        machine.trace.append(("REJ:MARKET_ONLY_REPRICE_ATTEMPT",
                               "market movement alone may trigger MARKET_CONTRADICTION_REVIEW, not a true-price reprice"))
        return False
    return True


# ============================================================
# Section 28 — CLV + Postmortem
# ============================================================

def postmortem(entry_price: float, closing_price: float, result: str,
               process_ok: bool) -> dict:
    clv = round(entry_price - closing_price, 2)  # under-perspective
    if process_ok and result == "WIN":
        grade, tag = "GOOD_PROCESS_GOOD_RESULT", "POSITIVE_TRAINING_EXAMPLE"
    elif process_ok and result == "LOSS":
        grade, tag = "GOOD_PROCESS_BAD_RESULT", "DO_NOT_PENALIZE_FOR_RESULT_ALONE"
    elif (not process_ok) and result == "WIN":
        grade, tag = "BAD_PROCESS_GOOD_RESULT", "FLAG_FALSE_POSITIVE"
    else:
        grade, tag = "BAD_PROCESS_BAD_RESULT", "NEGATIVE_TRAINING_EXAMPLE"
    return {"closing_line_value": clv, "result": result, "process_grade": grade, "action": tag}
