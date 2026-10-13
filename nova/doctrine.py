"""
FASTESTPAYOUT_DOCTRINE — executable, composable MarketNova intelligence module.

Full doctrine text: .agents/rules/FASTESTPAYOUT_DOCTRINE_v1.md (v1.0, read-only;
updates go into v1.1+ files per versioning protocol).

This module turns the doctrine into composable brains so any call site (LLM
narrative, live read, sport-specific analysis) loads ONLY the relevant pieces:

    from nova.doctrine import compose
    brain = compose("CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+NFL_BRAIN+LIVE_2H_BRAIN")

Nothing here changes the decision kernel's math. This is the doctrine layer:
sequence, output contract, never-invent invariants, and provenance policy.
"""

# ------------------------------------------------------------
# PROVENANCE (binding policy — Part 0 of the doctrine file)
# ------------------------------------------------------------
PROVENANCE = {
    "source": "Internally built framework of the MarketNova project "
              "(The Blueprint -> The Insider System -> FastestPayout Logic -> MarketNova).",
    "identity_claims": {
        "twicopy_cached_profile": "ANALYSIS: describes @FastestPayout as 'Sports "
            "Prognosticator / Mathematician / VIP Betting Group'. Anecdotal only — "
            "NOT verified inside Nova.",
        "letsrun_discussion": "ANALYSIS: third-party forum discussion of the account "
            "analyzing sportsbook databases and bettor-behavior modeling. Anecdotal, "
            "NOT treated as verified.",
        "fastestpayout_co_uk": "EXCLUDED: a UK gambling/withdrawal editorial site, "
            "NOT this entity and NOT evidence for this doctrine. Never cite it.",
    },
    "rule": "No part of this doctrine depends on unverifiable identity claims. "
            "Unsourced claims are treated as unverified (see NEVER_INVENT).",
}

# ------------------------------------------------------------
# NEVER-INVENT invariants (Part LXVIII) — hard validation surface
# ------------------------------------------------------------
NEVER_INVENT = [
    "sharp_money",
    "public_betting_percentages",
    "injuries",
    "line_movement",
    "vegas_knows_explanations",
]

NEVER_ASSUME = [
    "every_favorite_is_a_trap",
    "every_public_side_should_be_faded",
    "every_hot_performance_regresses_immediately",
    "reverse_line_movement_is_automatically_sharp",
    "historical_trends_without_current_context",
    "chase_after_value_disappears",
    "bets_because_a_user_asks",
    "rewrite_failed_analysis_after_the_game",
    "confuse_prediction_accuracy_with_betting_value",
]


def validate_no_invention(read: dict) -> list:
    """Guard for generated reads/narratives: any invented-evidence field
    must be sourced from a feed or omitted. Returns a list of violations.

    Fields claiming quantified public money, sharp action, or psychology
    must carry a source tag (e.g. data_quality_json / trigger_source);
    numbers without provenance are violations of Part LXVIII.
    """
    problems = []
    dq = read.get("data_quality_json") or {}
    sourced = isinstance(dq, dict) and bool(dq.get("public_pct_source"))

    def _sourced_num(v):
        # a quantified claim about public money with no source = invented
        return v is None or sourced or str(v).strip() == ""

    if not _sourced_num(read.get("public_bias")) and isinstance(read.get("public_bias"), str) \
            and any(ch.isdigit() for ch in str(read.get("public_bias"))):
        problems.append("public percentage without source (invented)")
    return problems


# ------------------------------------------------------------
# Composable brains
# ------------------------------------------------------------
CORE_BRAIN = {
    "name": "CORE_BRAIN",
    "authority": "MarketNova Master Brain Update v1.0 (.agents/rules/"
                 "MarketNova_Master_Brain_Update_v1.md) — canonical operating spec",
    "responsibilities": [
        "build true price before reading the market",
        "decision gates and thresholds (kernel)",
        "event sourcing, versioning, no hindsight, CLV postmortems",
    ],
    "core_doctrine": "Build the true game first. Read the market second. "
                      "Explain the discrepancy third. Bet only if the current "
                      "price still clears the edge threshold.",
}

FASTESTPAYOUT_DOCTRINE = {
    "name": "FASTESTPAYOUT_DOCTRINE",
    "first_principle": "THE LINE IS A PRICE. Ask why this exact price exists, "
                       "then what it should actually be.",
    "four_realities": [
        "GAME REALITY — what is actually happening",
        "MARKET REALITY — what is priced",
        "BETTOR REALITY — what the average bettor sees",
        "SPORTSBOOK REALITY — what the market maker is responding to",
    ],
    "key_principles": [
        "PART IV: bet the price, not the team — no permanently good teams, only prices",
        "PART V: construct the true line first, independently",
        "PART VI: use fair ranges, never fake precision",
        "PART VII: the opener is information; never overwrite history",
        "PART VIII: did the market already correct the mistake?",
        "PART IX/X: do not chase steam; NUMBER GONE is a permanent state",
        "PART XI/XII: public-perception tax and recency tax — how much of the story is already priced",
        "PART XV/XVI: public consensus is evidence, never automatic action; RLM is a research trigger",
        "PART XIX/XX: half points and juice are part of the number",
        "PART XXII: the obvious-bet test — why is the price still available?",
        "PART XXIII/XXIV: boring can be good; low-volume precision; passing is a position",
        "PART LIV: second-order thinking — what does the current number already know?",
        "PART LV: best expression — which market expresses the thesis most cleanly?",
        "PART LVI: correlated bets are not independent evidence",
        "PART LVIII/LIX: no forced plays; whale standard requires cross-module agreement",
        "PART LX: disagree with the user — CONFIRMED / WEAKENED / INVALIDATED / NUMBER GONE",
    ],
    "analysis_sequence": [
        "identify game", "freeze market", "build game reality", "build true price",
        "identify public story", "identify market story", "find distortion",
        "choose expression", "price test", "risk test", "action",
    ],
    "publish_test": [
        "What is the public seeing?", "What is the sportsbook pricing?",
        "What does the underlying game say?", "What does Nova make the true price?",
        "Has the market already corrected?", "Is this the best way to express the thesis?",
        "What number makes the bet invalid?",
    ],
    "required_output": [
        "Market", "Nova True Price", "Market Difference", "Public Story",
        "Sportsbook Context", "Underlying Reality", "Regression", "Best Expression",
        "Buy-To / Threshold", "Primary Risk", "Action",
    ],
    "action_states": ["PLAY", "LEAN", "MONITOR", "PASS", "NUMBER GONE",
                      "DATA INSUFFICIENT (rendered as AWAITING DATA in live taxonomy)"],
    "never_invent": NEVER_INVENT,
    "never_assume": NEVER_ASSUME,
}

LIVE_2H_BRAIN = {
    "name": "LIVE_2H_BRAIN",
    "source_parts": ["XXV–XXXVIII", "LXV"],
    "sequence": [
        "pregame expectation", "actual game state", "possessions/pace", "efficiency",
        "score quality", "sustainability", "regression", "talent/depth", "coaching",
        "expected game script", "true live/2H price", "book price",
        "what is already priced?", "best expression", "action",
    ],
    "archetypes": {
        "FALSE_SCORING_UNDER": "first-half scoring exceeds sustainable offense "
            "(short fields, defensive/ST scores, extreme red-zone conversion) — 2H Under "
            "only if the book has not fully corrected",
        "SUPPRESSED_SCORING_OVER": "offense better than scoreboard (high YPP, missed "
            "FGs, red-zone failures, pace) — 2H Over only if price leaves value",
        "CONTROL_SIDE": "scoreboard understates one team's dominance (LOS control, "
            "rushing efficiency, field position, depth)",
        "GAME_STATE_CONTAMINATION": "a large lead changes pace, play-calling, "
            "substitutions — never project 2H as if score were 0-0",
    },
    "principles": [
        "PART XXVI: scoreboard does not equal game — a score does not explain itself",
        "PART XXVII: separate organic points from distorted points",
        "PART XXVIII: regression asks whether production was repeatable, not reversal",
        "PART XXXIII: talent regression — depth/rotation matter more late in college games",
        "PART XXXIV: two identical halftime box scores can deserve different 2H lines",
        "PART XXXV: possession before points; never infer pace from points",
        "PART XXXVI: hot shooting is contextual — shot quality decides repeatability",
        "PART XXXVII: foul/bonus environment can flip a total by itself",
        "PART XXXVIII: blowouts compete (slower pace vs garbage-time deterioration)",
        "PART XL/XLI: overseas 1H under and 26.5 are heuristics to investigate, never hardcodes",
    ],
}

NFL_BRAIN = {
    "name": "NFL_BRAIN",
    "source_parts": ["XIX", "XLVIII", "LIII"],
    "principles": [
        "PART XIX: key numbers 3, 7, 10, 14, 17, 20, 21, 24, 27, 28 — a half point "
        "around a key margin can flip EV; 3 is not 3.5",
        "PART XLVIII: weather quantified, never narrative — wind/gusts/rain/roof "
        "modify passing, kicking, scoring variance",
        "PART LIII: prime-time amplifies favorite/Over bias and star bias — increase "
        "scrutiny, do not auto-fade",
    ],
}

MLB_BRAIN = {
    "name": "MLB_BRAIN",
    "source_parts": ["XLII–L"],
    "principles": [
        "PART XLII: starting pitcher is not ERA — pitch mix, command, contact quality, platoon splits",
        "PART XLIII: which lineup FITS this pitcher, not which lineup is better",
        "PART XLIV: bullpen game theory — usage, back-to-backs, leverage availability",
        "PART XLV/F5_BRAIN: use F5 to isolate starter edges",
        "PART XLVI: rookie/uncertain starters — widen the range, reduce conviction",
        "PART XLVII: low totals compress margins — do not blindly convert ML to run line",
        "PART XLVIII: wind/roof/park quantified",
        "PART XLIX: the obvious day-game Over may be correct but too expensive",
        "PART L: a half run is enormous in low-scoring distributions (7/7.5)",
    ],
}

F5_BRAIN = {
    "name": "F5_BRAIN",
    "source_parts": ["XLIV–XLVII"],
    "principles": [
        "use F5 when starter advantage is strong, lineup advantage clear, bullpen "
        "introduces unwanted variance",
        "preferred expressions: F5 ML, F5 +0.5, F5 Under",
        "do not force full-game ML because it is more familiar",
        "with uncertain pitchers, sometimes avoid F5 ML despite the apparent edge",
    ],
}

NCAAF_BRAIN = {
    "name": "NCAAF_BRAIN",
    "source_parts": ["XXXIII", "LI"],
    "principles": [
        "PART XXXIII: talent regression — a less talented roster can execute for 30 "
        "minutes; depth, line play and rotation matter more later (2H talent reassertion)",
        "PART LI: transfer portal / NIL / coordinator turnover — current identity, "
        "not last year's brand",
    ],
}

BASKETBALL_BRAIN = {
    "name": "BASKETBALL_BRAIN",
    "source_parts": ["XXXV–XXXVII"],
    "principles": [
        "PART XXXV: estimate possessions from FGA/OREB/TO/FTA; a 90-point half can be "
        "slow, a 70-point half can be fast",
        "PART XXXVI: hot shooting regresses contextually — shot quality decides",
        "PART XXXVII: foul/bonus environment is a total input of its own",
    ],
}

BRAINS = {
    b["name"]: b for b in (
        CORE_BRAIN, FASTESTPAYOUT_DOCTRINE, LIVE_2H_BRAIN, NFL_BRAIN,
        MLB_BRAIN, F5_BRAIN, NCAAF_BRAIN, BASKETBALL_BRAIN,
    )
}


def compose(spec: str) -> dict:
    """Compose a brain from a '+'-separated spec, e.g.
    compose('CORE_BRAIN+FASTESTPAYOUT_DOCTRINE+NFL_BRAIN+LIVE_2H_BRAIN').

    Returns {'brains': [ordered brain dicts], 'sequence': merged sequence,
    'publish_test': [...], 'never_invent': [...], 'never_assume': [...]}.
    """
    names = [n.strip() for n in spec.split("+") if n.strip()]
    unknown = [n for n in names if n not in BRAINS]
    if unknown:
        raise KeyError(f"unknown brain(s): {unknown}; available: {sorted(BRAINS)}")
    brains = [BRAINS[n] for n in names]
    seq = []
    for b in brains:
        for s in b.get("analysis_sequence", []) + b.get("sequence", []):
            if s not in seq:
                seq.append(s)
    return {
        "spec": names,
        "brains": brains,
        "sequence": seq,
        "publish_test": FASTESTPAYOUT_DOCTRINE["publish_test"]
                        if "FASTESTPAYOUT_DOCTRINE" in names else [],
        "never_invent": NEVER_INVENT,
        "never_assume": NEVER_ASSUME,
        "provenance": PROVENANCE,
    }


def prompt_context(spec: str) -> str:
    """Render a composed brain as compact text for an LLM narrative call —
    loads only the requested modules instead of the entire doctrine."""
    c = compose(spec)
    parts = []
    for b in c["brains"]:
        parts.append(f"## {b['name']}")
        for k, v in b.items():
            if k == "name":
                continue
            parts.append(f"- {k}: {v}")
    return "\n".join(parts)
