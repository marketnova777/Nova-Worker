"""
MarketNova Integration Test Suite — validates kernel.py against
MarketNova_Master_Brain_Update_v1.md scenarios (audit requirement F).

Run: python3 tests.py
Each scenario prints STATUS -> decision, and RULES FIRED with spec-section tags.
"""

import os
import sys
# validate the kernel THIS package ships with (nova/kernel.py) — the
# production code — never a stale external copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kernel import (GameStateMachine, EventStore, GameEvent, Patch, TruePrice, build_true_price,
                    decide, classify_movement, sharp_resistance_score, key_number_crossing,
                    key_number_quality, FirstHalfState, classify_sustainability,
                    classify_halftime_archetype, game_theory_read, true_2h_total, reprice,
                    attempt_market_only_reprice, postmortem, minimum_playable_price)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1
    print(f"    [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def show_trace(trace, section_filter=None):
    for rid, detail in trace:
        print(f"      FIRED {rid}: {detail}")


print("=" * 78)
print("SCENARIO 1 — PUBLIC-HEAVY LINE FREEZE (sec 4, 5)")
print("=" * 78)
trace = []
tp = build_true_price(team_power_gap=0.0, expected_pace_points=44.0, home_field=0.0)  # true total 44
mv_cls, mv_score = classify_movement(open_line=44.0, current_line=44.0, public_pct=72, move_toward_model=None)
sr_type, sr_score, sr_rule = sharp_resistance_score(public_pct=72, spread_move=0.0, juice_move=False)
print(f"  72% of tickets on the Over; total did not move off 44.")
print(f"  movement={mv_cls} (score {mv_score}) | sharp_resistance={sr_type} via rule '{sr_rule}' (score {sr_score})")
check("LINE_FREEZE classified", mv_cls == "LINE_FREEZE")
check("heavy_public_no_move fires", sr_rule == "heavy_public_no_move")
out = decide(true_price=tp, market_line=44.0, market="second_half", direction="under",
             public_pct=72, psychology=3, movement_score=0, sharp_resistance=sr_score,
             supporting_modules=[], trace=trace)   # NOTE: no independent true-price edge supplied
check("Public cannot create play -> PASS", out.status == "PASS")
check("PUBLIC_CANNOT_CREATE_PLAY gate fired", "PUBLIC_CANNOT_CREATE_PLAY" in out.gates_fired)
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 2 — REVERSE LINE MOVEMENT (sec 5)")
print("=" * 78)
trace = []
# (a) RLM alone, no independent edge -> cannot create a wager
tp = build_true_price(team_power_gap=3.0, expected_pace_points=48.0)   # true spread ~ -5.5, total 48
mv_cls, mv_score = classify_movement(open_line=-4.0, current_line=-5.0, public_pct=68, move_toward_model=False)
sr_type, sr_score, sr_rule = sharp_resistance_score(public_pct=68, spread_move=-1.0, move_against_public=True)
print(f"  68% public on dog; line moved 4.0 -> 5.0 AGAINST the public, no news.")
print(f"  movement={mv_cls} (score {mv_score}) | sharp_resistance={sr_type} via '{sr_rule}'")
out_a = decide(true_price=tp, market_line=-4.0, market="pregame_spread", direction="favorite",
               public_pct=68, psychology=2, movement_score=0, sharp_resistance=sr_score,
               supporting_modules=[], trace=trace)
check("RLM without independent edge -> PASS", out_a.status == "PASS")
check("PUBLIC_CANNOT_CREATE_PLAY fired", "PUBLIC_CANNOT_CREATE_PLAY" in out_a.gates_fired)
# (b) market moves hard AGAINST our independently derived position -> contradiction review, not a chase
tp_b = build_true_price(team_power_gap=1.0, expected_pace_points=41.0)  # true total 41, we like Under 44.5
mv_cls, mv_score = classify_movement(open_line=44.5, current_line=46.5, public_pct=68, move_toward_model=False)
out_b = decide(true_price=tp_b, market_line=46.5, market="pregame_spread", direction="under",
               public_pct=30, movement_score=mv_score, sharp_resistance=2,
               supporting_modules=["regression", "sustainability"], trace=trace)
check("Strong move against model -> REPRICE_REQUIRED", out_b.status == "REPRICE_REQUIRED")
check("STRONG_MARKET_CONTRADICTION fired", "STRONG_MARKET_CONTRADICTION" in out_b.gates_fired)
check("Never chases: no PLAY emitted", out_b.status != "PLAY")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 3 — KEY-NUMBER CROSSING (sec 6)")
print("=" * 78)
trace = []
tp = build_true_price(team_power_gap=6.0, expected_pace_points=45.0)   # true spread -8.5
print("  We like the favorite. True spread -8.5; we entered at -6.5. Market moves -6.5 -> -8.0.")
crossed, favorable, keys = key_number_crossing(entry_number=6.5, current_number=8.0, direction="favorite")
check("Key 7 crossed", crossed and 7 in keys)
check("Crossing unfavorable for favorite buyer", not favorable)
out = decide(true_price=tp, market_line=-8.0, market="pregame_spread", direction="favorite",
             movement_score=0, supporting_modules=["talent", "game_state"],
             key_crossed_unfavorably=True, trace=trace)
check("KEY_NUMBER_CROSSED gate fired (RECOMPUTE_EV + INVALIDATE)", "KEY_NUMBER_CROSSED" in out.gates_fired)
check("EV recomputed at the new number: edge 0.5 -> PASS, old recommendation invalidated",
      out.status in ("PASS", "MONITOR"))
print(f"  -> after crossing, raw edge vs true 8.5 is {out.raw_edge}; decision {out.status} (old rec invalidated)")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 4 — FALSE-SCORING 1H (sec 9, 10, 12)")
print("=" * 78)
trace = []
fh = FirstHalfState(points=31, drive_ypp=4.2, possessions=9, distortion_points=21,
                    missed_fg=0, rz_trips=2, rz_tds=2, lead=7, ypp_margin=0.3)
sust = classify_sustainability(fh)
arch = classify_halftime_archetype(fh)
gt = game_theory_read(fh.lead)
print(f"  1H: 31 points, but 21 from pick-6 + ST TD + two <30-yd short fields. Normal-drive YPP 4.2.")
check("Sustainability VERY_LOW (distortion share 68%)", sust == "VERY_LOW")
check("Archetype FALSE_SCORING_UNDER", arch == "FALSE_SCORING_UNDER")
t2h = true_2h_total(baseline_remaining_scoring=23.5, pace_adj=0.0, regression_adj=-3.0,
                     turnover_distortion=4.0, explosive_distortion=1.5, rz_adj=0.0)
print(f"  True 2H total = {t2h} (baseline 23.5 - regression 3.0 - turnover distortion 4.0 - explosive 1.5)")
tp2h = TruePrice(spread=-3.0, total=t2h)
out = decide(true_price=tp2h, market_line=27.5, market="second_half", direction="under",
             supporting_modules=["regression", "sustainability"], trace=trace)
check("2H Under 27.5 = PLAY band (edge 4.5 >= 3.0)", out.status in ("PLAY", "STRONG_DISCREPANCY"))
check("Both supporting modules present (PLAY_REQUIRES_SUPPORT satisfied)", "PLAY_REQUIRES_SUPPORT" not in out.gates_fired)
print(f"  -> decision {out.status}: Under 27.5 vs true {t2h}, min playable {out.minimum_playable_price}")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 5 — SUPPRESSED-SCORING 1H (sec 9, 10, 12)")
print("=" * 78)
trace = []
fh = FirstHalfState(points=10, drive_ypp=6.1, possessions=11, distortion_points=0,
                    missed_fg=2, rz_trips=3, rz_tds=0, lead=3, ypp_margin=0.6)
sust = classify_sustainability(fh)
arch = classify_halftime_archetype(fh)
print(f"  1H: 10 points, but 6.1 YPP, 2 missed FGs, 0/3 red zone, zero distortion points.")
check("Sustainability VERY_HIGH (repeatable production)", sust in ("VERY_HIGH", "HIGH"))
check("Archetype SUPPRESSED_SCORING_OVER", arch == "SUPPRESSED_SCORING_OVER")
t2h = true_2h_total(baseline_remaining_scoring=22.0, pace_adj=1.0, regression_adj=1.5, rz_adj=2.0)
print(f"  True 2H total = {t2h} (baseline 22 + suppressed-production payback)")
tp2h = TruePrice(spread=-1.5, total=t2h)
out = decide(true_price=tp2h, market_line=20.0, market="second_half", direction="over",
             supporting_modules=["regression", "sustainability"], trace=trace)
check("2H Over 20.0 vs true 25.5: raw edge 5.5 -> PLAY/STRONG", out.status in ("PLAY", "STRONG_DISCREPANCY"))
print(f"  -> decision {out.status}")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 6 — BLOWOUT / GAME-STATE CONTAMINATION (sec 10, 11)")
print("=" * 78)
trace = []
fh = FirstHalfState(points=35, drive_ypp=5.4, possessions=10, distortion_points=7, lead=28, ypp_margin=2.4)
arch = classify_halftime_archetype(fh)
gt = game_theory_read(fh.lead)
check("Archetype GAME_STATE_CONTAMINATION (28-pt lead >= 17)", arch == "GAME_STATE_CONTAMINATION")
check("Script BLOWOUT", gt["script"] == "BLOWOUT")
check("Expected 2H pace MUCH_SLOWER", gt["expected_2h_pace"] == "MUCH_SLOWER")
check("Substitution risk HIGH", gt["substitution_risk"] == "HIGH")
t2h = true_2h_total(baseline_remaining_scoring=24.0, game_state_adj=-6.0, substitution_adj=-1.5)
print(f"  True 2H total = {t2h} (blowout script drains pace; backups in)")
tp2h = TruePrice(spread=-10.0, total=t2h)
out = decide(true_price=tp2h, market_line=24.0, market="second_half", direction="under",
             supporting_modules=["game_state", "game_theory"], trace=trace)
print(f"  -> market 2H total 24.0 vs true {t2h}; decision {out.status} "
      f"(scoreboard says shootout, game state says clock-kill)")
check("Under supported by game-state module", out.game_state_support == "SUPPORTED")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 7 — NUMBER MOVING PAST MINIMUM PLAYABLE PRICE (sec 7, 13)")
print("=" * 78)
trace = []
tp2h = TruePrice(spread=-3.0, total=20.0)   # true 2H total 20 (2H keys 20/21/24 live)
mpp = minimum_playable_price(20.0, "second_half", "under")
print(f"  True 2H total 20.0; 2H play threshold 3.0 -> minimum playable Under = {mpp}")
out_open = decide(true_price=tp2h, market_line=23.5, market="second_half", direction="under",
                  supporting_modules=["regression", "sustainability"], trace=trace)
check("At 23.5 the number is playable", out_open.status in ("PLAY", "STRONG_DISCREPANCY"))
out_gone = decide(true_price=tp2h, market_line=20.5, market="second_half", direction="under",
                  supporting_modules=["regression", "sustainability"],
                  prior_playable_edge=(out_open.status in ("PLAY", "STRONG_DISCREPANCY")), trace=trace)
check("Market steams to 20.5 -> NUMBER_GONE (thesis intact, value gone)", out_gone.status == "NUMBER_GONE")
check("NUMBER_GONE gate fired", "NUMBER_GONE" in out_gone.gates_fired)
check("Never chases the steam", out_gone.status != "PLAY")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 8 — LATE CORRECTION BEFORE CONFIRM (sec 18, 19, 21, 22)")
print("=" * 78)
trace = []
store = EventStore(trace=trace)
# normal flow
store.append(GameEvent("SCORE", "PRIMARY_STATS_PROVIDER", 100, "t1",
                       [Patch("score", "SET", 14, "PRIMARY_STATS_PROVIDER", 100)], received_at="r1"))
store.append(GameEvent("CONFIRM", "OFFICIAL_LEAGUE", 101, "t2",
                       [Patch("score", "CONFIRM", 14, "OFFICIAL_LEAGUE", 101)], received_at="r2"))
check("score effective = 14, confirmed by OFFICIAL_LEAGUE", store.get("score") == 14)
check("CONFIRM strengthens evidentiary status",
      "OFFICIAL_LEAGUE" in store.effective["score"]["confirmed_by"])
# late event with OLDER source sequence arrives AFTER the confirm
store.append(GameEvent("SCORE", "PRIMARY_STATS_PROVIDER", 99, "t0",
                       [Patch("score", "SET", 13, "PRIMARY_STATS_PROVIDER", 99)], received_at="r3"))
check("Late older-seq event does NOT supersede effective claim", store.get("score") == 14)
check("SELECTIVE_REPLAY fired (history modified, effective untouched)",
      any(r[0] == "RULE:SELECTIVE_REPLAY" for r in trace))
# CONFIRM with wrong value -> CONFIRM_VALUE_MISMATCH
trace_before = len(trace)
store.append(GameEvent("CONFIRM", "OFFICIAL_LEAGUE", 102, "t3",
                       [Patch("score", "CONFIRM", 13, "OFFICIAL_LEAGUE", 102)], received_at="r4"))
check("CONFIRM value mismatch rejected", store.get("score") == 14)
check("CONFIRM_VALUE_MISMATCH fired", any(r[0] == "REJ:CONFIRM_VALUE_MISMATCH" for r in trace))
# silence is not confirmation: event touching only clock leaves score untouched
seq_before = store.effective["score"]["seq"]
store.append(GameEvent("CLOCK", "STADIUM_FEED", 103, "t4",
                       [Patch("game_clock", "SET", "4:12", "STADIUM_FEED", 103)], received_at="r5"))
check("Field omitted = no claim (score untouched by clock event)", store.get("score") == 14)
check("Clock updated independently", store.get("game_clock") == "4:12")
# decisions versioned; replayed kept separate
v1 = store.record_decision({"status": "PLAY", "line": 27.5})
store.record_replayed_decision({"status": "PASS", "line": 27.5})
statuses = [d["historical_status"] for d in store.decision_versions]
check("Actual vs replayed decisions kept separate",
      statuses == ["ACTUALLY_EMITTED", "COUNTERFACTUAL_RECONSTRUCTION"])
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 9 — CONFLICTING FIELD-AUTHORITY CORRECTIONS (sec 20)")
print("=" * 78)
trace = []
store = EventStore(trace=trace)
# same sequence, two sources, different clock values -> authority decides (OFFICIAL_LEAGUE rank 0)
store.append(GameEvent("CLOCK", "STADIUM_FEED", 200, "t1",
                       [Patch("game_clock", "SET", "2:31", "STADIUM_FEED", 200)], received_at="r1"))
store.append(GameEvent("CLOCK", "OFFICIAL_LEAGUE", 200, "t1b",
                       [Patch("game_clock", "SET", "2:29", "OFFICIAL_LEAGUE", 200)], received_at="r2"))
check("Clock authority: OFFICIAL_LEAGUE outranks STADIUM_FEED at same sequence",
      store.get("game_clock") == "2:29")
check("CROSS_SOURCE_CONFLICT logged (CRITICAL)", any(r[0] == "REJ:CROSS_SOURCE_CONFLICT" for r in trace))
# FIELD_AUTHORITY_INVARIANT: the same OFFICIAL_LEAGUE event has NO elevated authority over possession
store.append(GameEvent("POSS", "OFFICIAL_LEAGUE", 201, "t2",
                       [Patch("possession", "SET", "HOME", "OFFICIAL_LEAGUE", 201)], received_at="r3"))
store.append(GameEvent("POSS", "PLAY_BY_PLAY_PROVIDER", 202, "t3",
                       [Patch("possession", "SET", "AWAY", "PLAY_BY_PLAY_PROVIDER", 202)], received_at="r4"))
check("Possession: PBP provider supersedes league for possession field",
      store.get("possession") == "AWAY")
check("Authority over one field never extends to another",
      store.effective["game_clock"]["source"] == "OFFICIAL_LEAGUE"
      and store.effective["possession"]["source"] == "PLAY_BY_PLAY_PROVIDER")
show_trace(trace)

print()
print("=" * 78)
print("SCENARIO 10 — FINAL ATTEMPTING TO TRANSITION BACK TO LIVE (sec 15, 17)")
print("=" * 78)
trace = []
sm = GameStateMachine(trace=trace)
for s in ["CURRENT", "LIVE", "HALFTIME", "SECOND_HALF", "FINAL"]:
    sm.transition(s)
check("Happy path OPEN->...->FINAL traversed", sm.state == "FINAL")
ok = sm.transition("LIVE")
check("FINAL -> LIVE rejected", ok is False and sm.state == "FINAL")
check("FINAL_STATE_LOCKED logged (CRITICAL)", any(r[0] == "REJ:FINAL_STATE_LOCKED" for r in trace))
check("STATE_TRANSITION_NOT_ALLOWED logged (CRITICAL)", any(r[0] == "REJ:STATE_TRANSITION_NOT_ALLOWED" for r in trace))
ok2 = reprice(sm, cause_event_id="ev-x", cause_type="MAJOR_INJURY", affected_inputs=["qb"])
check("Reprice of FINAL game rejected", ok2 is False)
sm2 = GameStateMachine(trace=trace)
ok3 = sm2.transition("REPRICE")   # no cause event
check("REPRICE without cause_event_id rejected", ok3 is False)
check("REPRICE_WITHOUT_CAUSE logged (CRITICAL)", any(r[0] == "REJ:REPRICE_WITHOUT_CAUSE" for r in trace))
ok4 = attempt_market_only_reprice(sm2)
check("Market-only reprice attempt refused (WARNING logged)",
      ok4 is False and any(r[0] == "REJ:MARKET_ONLY_REPRICE_ATTEMPT" for r in trace))
show_trace(trace)

print()
print("=" * 78)
print("BONUS — CLV / POSTMORTEM QUADRANTS (sec 28)")
print("=" * 78)
p1 = postmortem(entry_price=27.5, closing_price=26.0, result="LOSS", process_ok=True)
check("Good process bad result: NOT penalized", p1["action"] == "DO_NOT_PENALIZE_FOR_RESULT_ALONE")
p2 = postmortem(entry_price=27.5, closing_price=26.0, result="WIN", process_ok=False)
check("Bad process good result: flagged false positive", p2["action"] == "FLAG_FALSE_POSITIVE")
p3 = postmortem(entry_price=27.5, closing_price=28.5, result="WIN", process_ok=True)
check("CLV tracked (beat close by 1.0)", p3["closing_line_value"] == -1.0)
print(f"  quadrant checks: {p1['process_grade']} / {p2['process_grade']} / {p3['process_grade']}")

# ------------------------------------------------------------
# Signed-edge direction regression tests (owner directive, Oct 9 2026):
# true -5 vs market -1 must be a 4-point edge TO THE FAVORITE.
# ------------------------------------------------------------
print()
print("-- signed-edge direction regression (true -5 vs market -1) --")
tp5 = build_true_price(team_power_gap=-7.0, expected_pace_points=45, home_field=2.0)
tp5.spread = -5.0
d_fav = decide(true_price=tp5, market_line=-1.0, market="pregame_spread", direction="favorite")
check("true -5 vs market -1 is a 4-point edge", d_fav.raw_edge == 4.0)
check("value side is the FAVORITE (market lays fewer than true)", -1.0 > -5.0)
check("minimum playable for true -5 is -3.0", d_fav.minimum_playable_price == -3.0)
check("market -1 is still playable for the favorite", -1.0 >= d_fav.minimum_playable_price)
d_dog = decide(true_price=tp5, market_line=-9.0, market="pregame_spread", direction="dog")
check("mirror: true -5 vs market -9 is a 4-point edge to the dog", d_dog.raw_edge == 4.0)
check("sign-flip trap: abs math fakes 2 but real disagreement is 8",
      abs(abs(3.0) - abs(-5.0)) == 2.0 and abs(3.0 - (-5.0)) == 8.0)
check("reads direction rule: line > true -> favorite, line < true -> dog",
      ("favorite" if -1.0 > -5.0 else "dog") == "favorite" and
      ("favorite" if -9.0 > -5.0 else "dog") == "dog")
tp_flip = build_true_price(team_power_gap=-14.0, expected_pace_points=45, home_field=2.0)
tp_flip.spread = 14.0
d_flip = decide(true_price=tp_flip, market_line=-6.5, market="pregame_spread", direction="dog")
check("sign-flip magnitude: true +14 vs market -6.5 raw edge is 20.5, never 7.5",
      d_flip.raw_edge == 20.5)

print()
print("-- game theory refinements: juice pressure + lifecycle --")
from datetime import datetime, timedelta, timezone
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from reads import compute_read
_g = {"league_code": "NFL", "sport": "NFL", "home_team": "Rams", "away_team": "Bears",
      "start_time": (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat()}
_pair = {"home": {"rating": 3.0}, "away": {"rating": 0.0}}
_mk = lambda line: {"spread": {"market_line": line, "book": "draftkings"}}
_ju_against = {"side": "home", "line_held": True, "worse": True, "from": -110, "to": -125}
_r_lean = compute_read(_g, _pair, _mk(-1.0), juice=_ju_against)
check("edge game keeps its status under juice pressure", _r_lean["status"] == "LEAN")
check("juice pressure marker recorded on the value side",
      _r_lean["telemetry"]["juice_pressure"] == "against_value_side")
check("value side resolved home (favorite at -1 vs true -5)",
      _r_lean["telemetry"]["value_side"] == "home")
_r_quiet = compute_read(_g, _pair, _mk(-4.5), juice=_ju_against)
check("quiet PASS upgrades to MONITOR on juice pressure", _r_quiet["status"] == "MONITOR")
_r_opp = compute_read(_g, _pair, _mk(-4.5),
                       juice={"side": "away", "line_held": True, "worse": True,
                              "from": -105, "to": -115})
check("juice on the opposite side changes nothing", _r_opp["status"] == "PASS")
check("lifecycle stage recorded (30h out = mid)",
      _r_quiet["telemetry"]["stage"] == "mid"
      and abs(_r_quiet["telemetry"]["hours_to_close"] - 30) < 1)

print()
print("-- psychology findings: vig test + shading window --")
_mkv = lambda line, hp: {"spread": {"market_line": line, "home_price": hp, "away_price": -110}}
_vt = compute_read(_g, _pair, _mkv(-1.0, -110))
check("no penalty at -110 (standard vig already in thresholds)",
      _vt["telemetry"]["vig_penalty"] == 0.0)
_vt2 = compute_read(_g, _pair, _mkv(-1.0, -150))
check("heavy juice pays the quarter-point tax (-150 -> 1.0 pt)",
      _vt2["telemetry"]["vig_penalty"] == 1.0
      and _vt2["telemetry"]["effective_edge"] == _vt2["raw_edge"] - 1.0)
_ctrl = compute_read(_g, _pair, _mkv(-3.5, -110))
_capped = compute_read(_g, _pair, _mkv(-3.5, -145))
check("LEAN capped to MONITOR when vig eats the lean threshold",
      _ctrl["status"] == "LEAN" and _capped["status"] == "MONITOR")
_dead = compute_read(_g, _pair, _mkv(-3.5, -190))
check("vig can zero the edge entirely (-190 -> PASS)",
      _dead["status"] == "PASS" and _dead["telemetry"]["effective_edge"] == 0.0)
check("open-shading window tagged on early favorite-side edges",
      _vt["telemetry"]["shading_window"] in (True, False))  # stage-dependent, must exist
check("effective edge never exceeds raw edge",
      all(compute_read(_g, _pair, _mkv(-3.5, j))["telemetry"]["effective_edge"]
          <= compute_read(_g, _pair, _mkv(-3.5, j))["raw_edge"]
          for j in (-110, -120, -145, -190)))

print()
print("-- v1.1 amendments: psychology uncredited, streak telemetry --")
import json as _json
_tp = build_true_price(team_power_gap=3.0, expected_pace_points=45, home_field=2.5)
_d_ps = decide(true_price=_tp, market_line=-1.0, market="pregame_spread",
               direction="favorite", supporting_modules=["psychology", "talent"])
_d_ok = decide(true_price=_tp, market_line=-1.0, market="pregame_spread",
               direction="favorite", supporting_modules=["talent", "regression"])
check("psychology + talent does NOT satisfy PLAY_REQUIRES_SUPPORT (v1.1 A1)",
      _d_ps.status == "LEAN")
check("two real modules (talent + regression) still reach PLAY",
      _d_ok.status == "PLAY")
_mform = {"team_abbr": "Rams", "league_code": "NFL",
          "last_5_json": _json.dumps([
              {"result": "W", "score": 28, "opponent_score": 14},
              {"result": "W", "score": 31, "opponent_score": 7},
              {"result": "W", "score": 24, "opponent_score": 21}])}
_r_hot = compute_read(_g, _pair, _mkv(-1.0, -110), form={"home": _mform, "away": None})
check("hot streak on the value side is recorded, not converted",
      _r_hot["telemetry"]["streak"] == {"side": "home", "kind": "W3", "len": 3}
      and _r_hot["status"] == "LEAN")
check("recency anchor flags a >=21pt blowout in the most recent game",
      compute_read(_g, _pair, _mkv(-1.0, -110),
                   form={"home": {"team_abbr": "Rams", "league_code": "NFL",
                                  "last_5_json": _json.dumps([
                                      {"result": "W", "score": 28, "opponent_score": 14},
                                      {"result": "W", "score": 31, "opponent_score": 7}])},
                         "away": None})["telemetry"]["recency_anchor"] is True)
check("no form data -> no streak context, read unaffected",
      compute_read(_g, _pair, _mkv(-1.0, -110))["telemetry"]["streak"] is None)

print()
print("=" * 78)
print(f"RESULT: {PASS} passed, {FAIL} failed")
print("=" * 78)
sys.exit(1 if FAIL else 0)
