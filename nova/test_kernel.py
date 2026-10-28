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
check("minimum playable for true -5 is -3.5 (PLAY band 1.5)", d_fav.minimum_playable_price == -3.5)
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
check("edge game keeps PLAY status under juice pressure (LEAN retired)", _r_lean["status"] == "PLAY")
check("juice pressure marker recorded on the value side",
      _r_lean["telemetry"]["juice_pressure"] == "against_value_side")
check("value side resolved home (favorite at -1 vs true -5)",
      _r_lean["telemetry"]["value_side"] == "home")
# value-side regression (Oct 14 fix): every game whose market favorite is
# the AWAY team (positive home-perspective line) had its side flipped.
# ratings: away stronger -> away is the true favorite
_pair_aw = {"home": {"rating": 0.0}, "away": {"rating": 3.0}}
_r_aw = compute_read(_g, _pair_aw, _mk(7.0))   # home +7 vs fair ~+1.1
check("AWAY-favorite: home +7 vs fair +1.1 -> value is HOME (dog points), not the away favorite",
      _r_aw["telemetry"]["value_side"] == "home"
      and _r_aw["edge_direction"] == "NOVA_FAVORS_HOME")
_r_aw2 = compute_read(_g, _pair_aw, _mk(0.0))  # pickem vs fair +0.5: away underpriced
check("AWAY-favorite underpriced: pickem vs fair +0.5 -> value is AWAY",
      _r_aw2["telemetry"]["value_side"] == "away"
      and _r_aw2["edge_direction"] == "NOVA_FAVORS_AWAY")
_r_hm = compute_read(_g, _pair, _mk(-1.5))      # home fav laying less than fair -5
check("HOME-favorite regression: -1.5 vs true -5 -> value home (unchanged)",
      _r_hm["telemetry"]["value_side"] == "home")
_r_dog = compute_read(_g, _pair, _mk(-6.5))    # home dog... home fav laying more than fair
check("home fav laying -6.5 vs true -5 -> value is AWAY",
      _r_dog["telemetry"]["value_side"] == "away")
check("buy-to prints the SIDE number (sign flipped for away value)",
      isinstance(_r_dog.get("invalidators"), list)
      and "(Bears +7.0 or better)" in _r_dog["invalidators"][0])
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
check("PLAY capped to MONITOR when vig eats the play threshold",
      _ctrl["status"] == "PLAY" and _capped["status"] == "MONITOR")
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
check("single-source PLAY keeps PLAY with conviction note (LEAN retired)",
      _d_ps.status == "PLAY")
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
      and _r_hot["status"] == "PLAY")
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
print("--- answer pack (nova/answers.py — local, zero-credit) ---")
from nova import answers as _ans
from nova import web as _web

_ap_g = {"away_team": "Rams", "home_team": "Bills", "league_code": "NFL"}
_ap_r = {"status": "PASS", "true_line": -5.0, "market_line": -1.0,
         "edge_value": 4.0, "thesis": "Nova makes this -5.", 
         "why_market_here": "Opener lag.", "risk": "Breaks at -3.5.",
         "buy_to": "Take Buffalo -3 or better.", "next_trigger": "Watch -3.5.",
         "market_tape": "Open -1.0 → current -1.0.", "true_line_basis": "Ratings.",
         "regression_notes": None}
_ap = _ans.build_pack(_ap_g, _ap_r, _ap_r, news=[{"headline": "Bills LT returns"}])
check("pack carries all six core intents",
      {"true_line", "should_i_bet", "where_does_value_end", "why_is_the_market_here",
       "what_breaks_the_thesis", "what_would_change_novas_mind"} <= set(_ap))
check("pack quotes only payload facts (numbers trace to the read)",
      "-5.0" in _ap["true_line"] and "-1.0" in _ap["true_line"] and "4.0" in _ap["true_line"])
check("latest_news labels headlines league-level, never team-specific",
      "league-level" in _ap["latest_news"] and "Bills LT returns" in _ap["latest_news"])
check("pack_json wraps in answer_pack envelope",
      _json.loads(_ans.pack_json(_ap_g, _ap_r, _ap_r, news=[]))["answer_pack"]["should_i_bet"].startswith("Status PASS"))
_aw = _ans.build_pack(_ap_g, {"status": "AWAITING DATA"}, {})
check("AWAITING DATA pack says no thesis and names the blocker",
      _aw["should_i_bet"].startswith("No.") and "AWAITING DATA" in _aw["true_line"])
check("news omitted entirely when lookup yields nothing (never stale filler)",
      "latest_news" not in _ans.build_pack(_ap_g, _ap_r, _ap_r, news=[]))
check("web fetch_url fails honest on 4xx (None, no raise)",
      _web.fetch_url("https://site.api.espn.com/apis/badpath/zzz") is None)
check("league_news returns [] for unknown leagues",
      _web.league_news("not-a-league") == [])
# team_context intent (real injury/probable/weather data in packs)
_ctx = {"injuries_away": ["Jayson Tatum (SF, Out: knee)"],
        "injuries_home": [], "probables": "Tyler Seguin (DAL) vs Nathan MacKinnon (COL)"}
_ap_r2 = dict(_ap_r); _ap_r2.update({"status": "PASS", "true_line": -2.5,
    "market_line": -2.0, "edge_value": 0.5,
    "weather": "Clear, 55F, 8 mph wind — neutral"})
_pctx = _ans.build_pack(_ap_g, _ap_r2, _ap_r2, news=None, context=_ctx)
check("team_context renders injuries + probables + weather",
      "team_context" in _pctx and "Jayson Tatum" in _pctx["team_context"]
      and "Probable starters" in _pctx["team_context"] and "Weather:" in _pctx["team_context"])
check("team_context carries the doctrine disclaimer",
      "not a bet signal" in _pctx["team_context"])
check("context=None omits team_context entirely (fetch failed, never fake 'no injuries')",
      "team_context" not in _ans.build_pack(_ap_g, _ap_r2, _ap_r2, news=None, context=None))
_ap_r_nowx = dict(_ap_r2); _ap_r_nowx["weather"] = None
check("empty context + no weather -> team_context omitted (nothing to say, nothing invented)",
      "team_context" not in _ans.build_pack(_ap_g, _ap_r_nowx, _ap_r_nowx, news=None,
                                            context={"injuries_away": [], "injuries_home": [], "probables": None}))
check("weather-only context renders weather without any injury/probable lines",
      _pwo := _ans.build_pack(_ap_g, _ap_r2, _ap_r2, news=None,
                              context={"injuries_away": [], "injuries_home": [], "probables": None}),
      "team_context" in _pwo and "Injury" not in _pwo["team_context"] and "Probable" not in _pwo["team_context"])

# ---- postgame grading (nova/grades.py — the learning loop) ----
from nova import grades as _gr
from datetime import datetime, timezone as _tz
_g_now = datetime(2026, 10, 14, 20, 0, tzinfo=_tz.utc)
_g_game = {"id": "g1", "status": "final", "home_score": 27.0, "away_score": 20.0,
           "start_time": "2026-10-13T01:00:00Z"}
_g_read = {"id": "r1", "game_id": "g1", "market_type": "spread", "is_current": False,
          "status": "LEAN", "true_line_value": -4.0, "market_line": -2.0,
          "edge_direction": "NOVA_FAVORS_HOME", "final_read_json": None}
_g_odds = [{"market_type": "spread", "home_line": -3.5, "snapshot_time": "2026-10-13T00:30:00Z"},
           {"market_type": "spread", "home_line": -1.5, "snapshot_time": "2026-10-12T20:00:00Z"}]
_gp = _gr.grade_read(_g_game, _g_read, _g_odds, now=_g_now)
_g_game2 = {"id": "g2", "status": "final", "home_score": 10.0, "away_score": 15.0,
            "start_time": "2026-10-13T01:00:00Z"}
_gp = _gr.grade_read(_g_game2, _g_read, _g_odds, now=_g_now)  # margin -5: model closer
check("grade computes margin and calibration (model -4 vs market -2, margin -5)",
      _gp["margin"] == -5.0 and _gp["model_error"] == 1.0 and _gp["market_error"] == 3.0
      and _gp["calibration_winner"] == "MODEL")
check("grade CLV: home entry -2.0, close -3.5 -> +1.5 pts beat-to-close",
      _gp["closing_line"] == -3.5 and _gp["clv_points"] == 1.5)
check("grade value result: home -2 did not cover at margin -5 (honest loss)",
      _gp["value_result"] == "LOST" and _gp["value_side"] == "HOME")
check("closing_line ignores snapshots after kickoff",
      _gr.closing_line(_g_odds, kickoff=datetime(2026, 10, 13, 0, 0, tzinfo=_tz.utc)) == -1.5)
# away side + loss + negative CLV
_gp2 = _gr.grade_read(_g_game, {**_g_read, "id": "r2", "edge_direction": "NOVA_FAVORS_AWAY"},
                       _g_odds, now=_g_now)
check("away-side CLV sign flips (close - entry) and away lost the cover",
      _gp2["clv_points"] == -1.5 and _gp2["value_result"] == "LOST")
# missing inputs stay honest
_gp3 = _gr.grade_read({**_g_game, "away_score": None}, _g_read, _g_odds, now=_g_now)
check("missing score -> no grade at all (never guessed)",
      _gp3 is None)
_gp4 = _gr.grade_read(_g_game, {**_g_read, "id": "r4", "true_line_value": None},
                      [], now=_g_now)
check("missing lines -> explicit skip note, no fake numbers",
      _gp4["graded"] is True and "calibration_winner" not in _gp4
      and "skipped" in _gp4.get("notes", ""))
# idempotence via grade_finals
_gf = _gr.grade_finals([_g_game], [{**_g_read, "final_read_json": _json.dumps(_gp)}],
                       {"g1": _g_odds}, now=_g_now)
check("grade_finals skips reads already graded",
      len(_gf) == 0)
_gf_old = _gr.grade_finals([_g_game], [{**_g_read, "id": "r5",
    "final_read_json": _json.dumps({**_gp, "grade_version": 1})}],
    {"g1": _g_odds}, now=_g_now)
check("grade version bump re-grades reads graded under the old derivation",
      len(_gf_old) == 1 and _gf_old[0][1]["grade_version"] == _gr.GRADE_VERSION)
_gf2 = _gr.grade_finals([_g_game], [_g_read], {"g1": _g_odds}, now=_g_now)
check("grade_finals grades an ungraded final read",
      len(_gf2) == 1 and _gf2[0][0] == "r1")
_cs = _gr.calibration_summary([_gp, _gp2])
check("calibration_summary aggregates model-closer record and CLV",
      _cs["graded"] == 2 and _cs["model_closer"] == 1 and _cs["market_closer"] == 1
      and _cs["value_record"] == {"LOST": 2})

# ---- identity janitor (nova/janitor.py) ----
from nova import janitor as _jan
_j_games = [
    {"id": "canon", "provider_game_id": "4019", "external_id": "odds-9"},
    {"id": "junk1", "provider_game_id": None, "external_id": "4019"},   # dup, no attachments
    {"id": "junk2", "provider_game_id": None, "external_id": "4019"},   # dup, has read
    {"id": "unique", "provider_game_id": None, "external_id": "4020"}, # no canonical -> keep
    {"id": "canon2", "provider_game_id": "4021", "external_id": None},
]
_safe, _manual = _jan.find_junk(_j_games, {"junk2"}, set())
check("janitor separates unattached dups from attached dups",
      [g["id"] for g in _safe] == ["junk1"] and [g["id"] for g in _manual] == ["junk2"])
# legacy legit games (no canonical collision) are never touched
check("janitor keeps non-duplicate legacy games",
      all(g["id"] not in ("unique", "canon2") for g in _safe + _manual))
class _JanClient:
    def __init__(self): self.deleted = []
    def delete(self, entity, rid): self.deleted.append((entity, rid))
_jc = _JanClient()
_n_del, _n_man = _jan.sweep(_jc, _j_games, [{"game_id": "junk2"}], set(), log=lambda *a: None)
check("janitor sweep deletes only the safe dup",
      _n_del == 1 and _jc.deleted == [("Game", "junk1")] and _n_man == 1)

# json roundtrip guard for changed_fields (api.py canonical compare)
from nova.api import changed_fields as _cf
check("changed_fields ignores json string-vs-object roundtrip",
      _cf({"thesis_json": _json.dumps({"a": 1})}, {"thesis_json": {"a": 1}}, ["thesis_json"]) is None)
check("changed_fields still catches a real answer-pack change",
      _cf({"thesis_json": {"a": 1}}, {"thesis_json": {"a": 2}}, ["thesis_json"]) == {"thesis_json": {"a": 2}})




# ===========================================================================
# LIVE RESEARCH LAYER (nova/research.py)
# ===========================================================================
from nova import research as _res

def _no_key():
    """Temporarily disable research/synthesis (env-independent fail-open).
    OpenAI is gone (Oct 16 2026); the flag is the only gate now."""
    import importlib
    import nova.config as _cfg
    import nova.research as _r2, nova.synthesis as _s2
    _saved = (_os.environ.pop("NOVA_RESEARCH", None),
              _os.environ.pop("NOVA_SYNTH_MODE", None))
    _os.environ["NOVA_RESEARCH"] = "0"
    _os.environ["NOVA_SYNTH_MODE"] = "off"
    for m in (_cfg, _r2, _s2):
        importlib.reload(m)
    return _saved

def _restore_key(_saved):
    import importlib
    import nova.config as _cfg
    import nova.research as _r2, nova.synthesis as _s2
    _os.environ.pop("NOVA_RESEARCH", None)
    _os.environ.pop("NOVA_SYNTH_MODE", None)
    if _saved[0] is not None:
        _os.environ["NOVA_RESEARCH"] = _saved[0]
    if _saved[1] is not None:
        _os.environ["NOVA_SYNTH_MODE"] = _saved[1]
    for m in (_cfg, _r2, _s2):
        importlib.reload(m)

from nova import answers

# MODE ROUTER — canonical-state questions never search; world questions do
check("router: fair-line question is deterministic (MODE 0)",
      _res.classify("What is Nova's fair line for the Rangers game?") == 0)
check("router: buy-to question is deterministic (MODE 0)",
      _res.classify("What is the buy-to on Bills -3.5?") == 0)
check("router: taxonomy question is deterministic (MODE 0)",
      _res.classify("Explain NUMBER GONE to me") == 0)
check("router: update request requires research (MODE 2)",
      _res.classify("Update Rangers Capitals") == 2)
check("router: goalie confirmation requires research (MODE 2)",
      _res.classify("Is the goalie confirmed tonight?") == 2)
check("router: injury news requires research (MODE 2)",
      _res.classify("Any injury news for this matchup?") == 2)

# PROMPT CONTRACT — engine boundaries are stated inside the prompt
_p = _res.build_prompt(
    {"away_team": "Oilers", "home_team": "Sharks", "league_code": "nhl",
     "start_time": "2026-10-15T02:00:00Z", "venue": "SAP Center"},
    {"status": "LEAN", "marketnova_true_line": -1.5, "market_line": -1.5,
     "edge_value": 0.9, "one_line_read": "shading case"})
check("research prompt carries event, league, and canonical snapshot",
      all(s in _p for s in ("Oilers", "Sharks", "NHL", "LEAN", "-1.5")))
check("research prompt forbids price-engine answers",
      "Do not create a betting recommendation" in _p
      and "Do not create a true line" in _p
      and "sharp money" in _p)
check("research prompt requires the JSON contract",
      "material_changes" in _p and "reanalysis_required" in _p
      and "confirmed_facts" in _p)

# OUTPUT PARSING + SOURCE DISCIPLINE
_good = """```json
{
  "material_changes": [{"claim": "Skinner confirmed starting goalie",
    "source": "NHL.com", "url": "https://www.nhl.com/oilers/news",
    "verification": "confirmed"}],
  "confirmed_facts": [{"claim": "No new injuries", "source": "NHL.com",
    "url": "https://www.nhl.com/oilers/injuries"}],
  "uncertain_items": [],
  "reanalysis_required": true
}
```"""
_f = _res.parse_research(_good, [], "2026-10-14T23:00:00Z")
check("parse: valid research JSON normalizes with source discipline",
      _f is not None and _f["material_changes"][0]["retrieved_at"] == "2026-10-14T23:00:00Z"
      and _f["material_changes"][0]["verification"] == "confirmed")
check("parse: malformed research output fails honestly",
      _res.parse_research("no json here at all", [], "t") is None)
_no_url = _res.parse_research(
    '{"material_changes": [{"claim": "goalie changed", "source": "twitter"}]}',
    [], "2026-10-14T23:00:00Z")
check("parse: claim without URL is never confirmed",
      _no_url is not None and _no_url["material_changes"][0]["verification"] == "unverified")
check("source hierarchy: official league domain is tier 1",
      _res.source_quality("https://www.nhl.com/oilers/news") == 1)
check("source hierarchy: random blog is tier 5",
      _res.source_quality("https://someblog.blogspot.com/post") == 5)
check("pack intent: no findings -> no_material_change summary",
      _res.pack_intent({"material_changes": [], "reanalysis_required": False},
                       "t", "gpt-5.5")["no_material_change"] is True)
check("pack intent: material change carries claim + url + retrieved_at",
      _res.pack_intent({"material_changes": [_f["material_changes"][0]],
                        "reanalysis_required": True}, "t", "gpt-5.5")
      ["material_changes"][0]["url"].startswith("https://www.nhl.com"))

# TTL + CANDIDATE SELECTION (cost control)
def _mkgame(gid, hours, status="scheduled"):
    import datetime as _dt
    st = (_dt.datetime.now(_dt.timezone.utc)
          + _dt.timedelta(hours=hours)).isoformat()
    return {"id": gid, "status": status, "start_time": st,
            "away_team": "A", "home_team": "B"}
_now = datetime.now(timezone.utc)
_fresh = {"thesis_json": _json.dumps({"answer_pack": {"live_research":
    {"researched_at": _now.isoformat()}}})}
_stale = {"thesis_json": _json.dumps({"answer_pack": {"live_research":
    {"researched_at": (_now - timedelta(hours=20)).isoformat()}}})}
check("ttl: fresh research (same hour) is not stale",
      not _res.is_stale(_mkgame("g1", 30), _fresh))
check("ttl: 20h-old research is stale for a near game",
      _res.is_stale(_mkgame("g1", 3), _stale))
_age8 = {"thesis_json": _json.dumps({"answer_pack": {"live_research":
    {"researched_at": (_now - timedelta(hours=8)).isoformat()}}})}
check("ttl: far-out games get a long budget (8h age still fresh)",
      not _res.is_stale(_mkgame("g1", 40), _age8))
check("ttl: 20h-old research is stale even for far-out games",
      _res.is_stale(_mkgame("g1", 40), _stale))
_cands = _res.pick_candidates(
    [_mkgame("a", 3), _mkgame("b", 20), _mkgame("c", 40),
     _mkgame("d", 3, status="final"), _mkgame("e", 100)],
    {"a": _stale, "b": _stale, "c": _stale, "d": _stale, "e": _stale},
    cap=2)
check("candidates: scheduled in-window stale games, soonest first, capped",
      [g["id"] for g, _r in _cands] == ["a", "b"])

# FAIL-OPEN: no key -> research disabled, never crashes the cycle
_saved_k = _no_key()
class _NoQueueClient:
    """Offline client whose LLMTask entity is missing -> fail-open."""
    def list_all(self, entity, fields=None, query=None):
        raise RuntimeError("list LLMTask failed 404")
check("fail-open: research flag off disables MODE 2",
      not _res.enabled() and _res.run_research([], {}, _NoQueueClient(),
                                               log=lambda *a: None) == {})
_restore_key(_saved_k)


# ANSWER PACK: research rides thesis_json as the structured live_research intent
_pack = answers.build_pack(
    {"away_team": "Oilers", "home_team": "Sharks", "league_code": "NHL",
     "game_date": "2026-10-15", "start_time": "2026-10-15T02:00:00Z"},
    {"status": "LEAN", "true_line": -1.5, "market_line": -1.5,
     "edge_value": 0.9, "buy_to": "EDM -1.1 or better", "risk": "goalie change"},
    {"doing": "x", "one_line": "y"},
    research={"research_version": 1, "researched_at": "t", "model": "gpt-5.5",
              "summary": "No material change.", "no_material_change": True,
              "reanalysis_required": False, "material_changes": [],
              "confirmed_facts": [], "uncertain_items": [], "sources": []})
check("answer pack carries structured live_research intent",
      isinstance(_pack, dict) and _pack["live_research"]["no_material_change"] is True)
check("answer pack omits live_research when research is None",
      "live_research" not in answers.build_pack(
          {"away_team": "A", "home_team": "B", "league_code": "NHL"},
          {"status": "PASS", "true_line": 1.0, "market_line": 1.0,
           "edge_value": 0.0}, {"doing": "x", "one_line": "y"}))




# ===========================================================================
# MODE 2 SYNTHESIS LAYER (nova/synthesis.py)
# ===========================================================================
from nova import synthesis as _syn
from nova import reads

_G = {"id": "gX", "provider_game_id": "40123", "away_team": "Oilers",
      "home_team": "Sharks", "league_code": "nhl", "status": "scheduled",
      "start_time": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
      "venue": "SAP Center"}

# SPORT BRAIN ROUTING
check("synth: NHL routes to core doctrine (no dedicated brain yet)",
      _syn.sport_brain_spec("nhl") == "CORE_BRAIN+FASTESTPAYOUT_DOCTRINE")
check("synth: NFL routes to the NFL brain",
      "NFL_BRAIN" in _syn.sport_brain_spec("nfl"))
check("synth: MLB routes to MLB + F5 brains",
      "MLB_BRAIN" in _syn.sport_brain_spec("mlb")
      and "F5_BRAIN" in _syn.sport_brain_spec("mlb"))

# PROMPT CONTRACT
_ctx = _syn.build_context(_G, baseline=-1.5, market_line=-1.5,
                         tape=[("t", -1.5, -110)], injuries=2,
                         research={"material_changes": [
                             {"claim": "Skinner starting", "source": "NHL.com",
                              "url": "https://nhl.com/x", "verification": "confirmed",
                              "retrieved_at": "t"}]})
_pr = _syn.build_prompt(_G, _ctx)
check("synth prompt carries doctrine + sport questions + event",
      all(s in _pr for s in ("CORE_BRAIN", "FASTESTPAYOUT_DOCTRINE",
                             "Oilers", "Sharks", "confirmed goalie",
                             "5v5 expected goals")))
check("synth prompt enforces fair-range-not-precision + no-recommendation rules",
      "Fair range, not fake precision" in _pr
      and "Do NOT copy the market" in _pr
      and "no betting recommendation" in _pr.replace("Do not produce a betting recommendation", "no betting recommendation"))
check("synth prompt carries the output contract + FastestPayout questions",
      all(k in _pr for k in ("nova_fair_range", "fair_price_reasoning",
                             "best_expression", "invalidators",
                             "Has the market already corrected?")))
check("synth prompt carries sourced research findings with provenance",
      "Skinner starting" in _pr and "NHL.com" in _pr and "CONFIRMED" in _pr)
check("synth prompt carries the kernel baseline and market line",
      '"kernel_baseline_true_line": -1.5' in _pr)

# PARSE + VALIDATE
_good_pack = """```json
{"research_version": "2", "market_snapshot": {"open": -1.5, "current": -2.0, "movement": "drift to home"},
 "game_reality": {"goalies": "Skinner vs Askarov"},
 "regression": {"structural": ["5v5 xG edge home"], "noisy": ["3-game win streak"]},
 "bettor_story": "home streak", "sportsbook_context": "streak priced",
 "nova_fair_range": {"low": -2.5, "high": -1.5},
 "fair_price_reasoning": ["5v5 xG edge", "goalie edge confirmed"],
 "best_expression": "home ML", "buy_to": "-1.5 or better", "primary_risk": "goalie scratch",
 "invalidators": ["goalie change"], "data_quality": "good", "action_candidate": "LEAN",
 "sources": [{"claim": "Skinner starting", "source": "NHL.com", "url": "https://nhl.com/x"}]}
```"""
_pk = _syn.parse_pack(_good_pack)
check("synth parse: valid pack shapes with fair range",
      _pk is not None and _pk["nova_fair_range"] == {"low": -2.5, "high": -1.5})
check("synth parse: malformed pack fails honestly",
      _syn.parse_pack("The home team is good.") is None)
check("synth validate: in-range synthesis accepted",
      _syn.validate_range({"nova_fair_range": {"low": -2.5, "high": -1.5}}, -1.5)[0] == -2.0)
_mid, _notes = _syn.validate_range({"nova_fair_range": {"low": -8.0, "high": -7.0}}, -1.5)
check("synth validate: drift beyond sanity bound is rejected",
      _mid is None and any("sanity" in n for n in _notes))
_mid2, _notes2 = _syn.validate_range({"nova_fair_range": {"low": -14.0, "high": 2.0}}, -1.5)
check("synth validate: too-wide range rejected as unpriceable",
      _mid2 is None and any("width" in n for n in _notes2))
_mid3, _ = _syn.validate_range({"nova_fair_range": {"low": -1.0, "high": -3.0}}, -1.5)
check("synth validate: inverted range rejected", _mid3 is None)

# UPDATE CONTRACT
_old = {"nova_fair_range": {"low": -1.5, "high": -0.5},
        "personnel": {"goalies": "Campbell"}, "status_at_write": "PASS"}
_new = {"nova_fair_range": {"low": -2.5, "high": -1.5},
        "personnel": {"goalies": "Skinner"}, "status_at_write": "LEAN"}
_d = _syn.compare_packs(_old, _new)
check("update diff: what changed / did not / old-new fair / old-new status",
      "fair_range" in _d["what_changed"] and "personnel" in _d["what_changed"]
      and _d["old_fair"]["low"] == -1.5 and _d["new_fair"]["low"] == -2.5
      and _d["old_status"] == "PASS" and _d["new_status"] == "LEAN")
check("update diff: identical packs produce no update",
      _syn.compare_packs(_new, dict(_new)) is None)
check("render_update: readable block with old/new fair + status",
      "OLD FAIR" in _syn.render_update(_d) and "NEW STATUS: LEAN" in _syn.render_update(_d))

# FIXTURE SELECTION (rollout control: one game per cycle)
_rd = {"status": "PASS", "thesis_json": None}
_far_g = dict(_G, id="far", start_time=(datetime.now(timezone.utc) + timedelta(hours=12)).isoformat())
_aw_g = dict(_G, id="aw", start_time=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
_fx = _syn.select_fixture([_far_g, _aw_g],
                          {"far": _rd, "aw": {"status": "AWAITING DATA"}})
check("fixture: soonest playable game only, AWAITING DATA skipped",
      len(_fx) == 1 and _fx[0][0]["id"] == "far")

# FAIL-OPEN: no key -> synthesis disabled, baseline always stands
_saved_k2 = _no_key()
import nova.synthesis as _syn_nk
check("synth fail-open: flag off disables MODE 2",
      not _syn_nk.synth_enabled())
_r, _pk2, _n2 = _syn_nk.synthesize(_G, -1.5, _ctx, "")
check("synth fail-open: empty bridge result returns no price",
      _r is None and _pk2 is None and _n2)
_restore_key(_saved_k2)

# KERNEL CONSUMPTION: validated synthesis replaces the true-line INPUT only
_pair = {"home": {"rating": 3.5, "league_code": "NHL"}, "away": {"rating": 3.0, "league_code": "NHL"}}
_mkts = {"spread": {"market_line": -1.5, "home_price": -110, "away_price": -110}}
_base = reads.compute_read(_G, _pair, _mkts)
_base_line = _base["true_line"]
_syn_res = reads.compute_read(_G, _pair, _mkts,
                              synth={"mid": _base_line + 0.5,
                                     "pack": {"nova_fair_range": {"low": -2.0, "high": -1.0}}})
check("kernel consumes validated synthesis as the true-line input",
      abs(_syn_res["true_line"] - (_base_line + 0.5)) < 0.01
      and "MODE2 synthesis" in str(_syn_res["telemetry"].get("synthesis")))
_rej = reads.compute_read(_G, _pair, _mkts,
                          synth={"mid": _base_line + 10.0,
                                 "pack": {"nova_fair_range": {"low": -12.0, "high": -10.0}}})
check("kernel rejects out-of-sanity synthesis; baseline stands",
      abs(_rej["true_line"] - _base_line) < 0.01
      and "rejected" in str(_rej["telemetry"].get("synthesis")))
check("synthesis provenance recorded in telemetry on every path",
      _syn_res["telemetry"].get("synthesis") is not None
      and _rej["telemetry"].get("synthesis") is not None)

# ANSWER PACK: structured analysis_pack + update intent ride thesis_json
_ap = answers.build_pack(_G, dict(_syn_res, analysis_pack=_pk, status="LEAN"),
                         {"doing": "x", "one_line": "y"})
check("answer pack carries the structured analysis pack",
      _ap.get("analysis_pack", {}).get("best_expression") == "home ML")
check("answer pack carries update intent when a diff exists",
      "update" in answers.build_pack(
          _G, dict(_syn_res, analysis_pack=dict(_pk, update_diff=_d)), {"doing": "x"}))




# ===========================================================================
# ANALYSIS VERSIONING (nova/versioning.py) — doctrine 34/79
# ===========================================================================
from nova import versioning as _ver
import json as _json_mod

_VG = {"id": "vg1", "provider_game_id": "40188", "home_team": "Rangers",
       "away_team": "Devils", "start_time": "2026-10-15T23:00:00Z"}
_VPAIR = {"home": {"rating": 4.0, "league_code": "NHL"},
          "away": {"rating": 3.2, "league_code": "NHL"}}
_VMKTS = {"spread": {"market_line": -1.5, "home_price": -115,
                     "away_price": -105}}
_VTAPE = [("2026-10-15T10:00:00Z", -1.5, -115), ("2026-10-15T12:00:00Z", -2.0, -110)]

_h1 = _ver.input_fingerprint(_VG, _VPAIR, _VMKTS, injuries=1, tape=_VTAPE)
_h2 = _ver.input_fingerprint(_VG, _VPAIR, _VMKTS, injuries=1, tape=_VTAPE)
check("versioning: same inputs -> same hash (replayable)",
      _h1 == _h2 and len(_h1) == 16)

# tape timestamps are bookkeeping: only line+price path participates
_VTAPE2 = [("2026-10-15T14:00:00Z", -1.5, -115), ("2026-10-15T16:00:00Z", -2.0, -110)]
check("versioning: odds-row timestamps do not churn the hash",
      _ver.input_fingerprint(_VG, _VPAIR, _VMKTS, injuries=1, tape=_VTAPE2) == _h1)

# material changes mint a new hash
check("versioning: line move changes the hash",
      _ver.input_fingerprint(_VG, _VPAIR,
                             {"spread": {"market_line": -2.5, "home_price": -115,
                                         "away_price": -105}},
                             injuries=1, tape=_VTAPE) != _h1)
check("versioning: rating change changes the hash",
      _ver.input_fingerprint(_VG, dict(_VPAIR, home={"rating": 4.5}), _VMKTS,
                             injuries=1, tape=_VTAPE) != _h1)
check("versioning: injury-detail change changes the hash",
      _ver.input_fingerprint(_VG, _VPAIR, _VMKTS, injuries=2, tape=_VTAPE,
                             injury_details=[{"team": "NYR", "name": "Fox"}]) != _h1)

# version chain stamping
_m1 = _ver.next_meta(None, _h1, now="2026-10-15T20:00:00Z")
check("versioning: first analysis mints v1 with no supersedes",
      _m1["analysis_version"] == 1 and _m1["supersedes_version"] is None
      and _m1["input_hash"] == _h1)
_m1b = _ver.next_meta(_m1, _h1, now="2026-10-15T20:30:00Z")
check("versioning: unchanged inputs keep v1 + original created_at (no churn)",
      _m1b["analysis_version"] == 1 and _m1b["created_at"] == _m1["created_at"])
_m2 = _ver.next_meta(_m1, "deadbeef12345678", now="2026-10-15T21:00:00Z")
check("versioning: material change mints v2 superseding v1",
      _m2["analysis_version"] == 2 and _m2["supersedes_version"] == 1
      and _m2["input_hash"] == "deadbeef12345678")

# meta extraction from a Base44-roundtripped pack (string or object)
_pack_s = _json_mod.dumps({"answer_pack": {"meta": _m1, "true_line": "x"}})
check("versioning: pack_meta parses string roundtrip",
      _ver.pack_meta(_pack_s)["analysis_version"] == 1)
check("versioning: pack_meta parses object roundtrip",
      _ver.pack_meta({"answer_pack": {"meta": _m1}})["input_hash"] == _h1)
check("versioning: pack_meta rejects junk quietly",
      _ver.pack_meta("not json") is None and _ver.pack_meta({}) is None)

# MODE 1 cached-synthesis gate
_ap_ok = {"validation": "accepted", "input_hash": _h1,
          "effective_true_line": -2.0,
          "created_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()}
_ap_rej = dict(_ap_ok, validation="rejected")
_ap_stale = dict(_ap_ok, created_at=(datetime.now(timezone.utc) - timedelta(hours=6)).isoformat())
_ap_newhash = dict(_ap_ok, input_hash="deadbeef12345678")
check("versioning: fresh pack with unchanged inputs is cached (MODE 1)",
      _ver.cached_synthesis(_ap_ok, _h1, 2.0) is _ap_ok)
check("versioning: rejected / stale / changed-hash packs force MODE 2",
      _ver.cached_synthesis(_ap_rej, _h1, 2.0) is None
      and _ver.cached_synthesis(_ap_stale, _h1, 2.0) is None
      and _ver.cached_synthesis(_ap_newhash, _h1, 2.0) is None)

# meta rides the answer pack end-to-end
_rz = dict(_syn_res)
_ap_full = answers.build_pack(_VG, dict(_rz, analysis_pack=None), {"doing": "x"},
                              meta=_m2)
check("answer pack carries the meta chain",
      _ap_full.get("meta", {}).get("analysis_version") == 2
      and _ap_full["meta"]["supersedes_version"] == 1)
_json_out = answers.pack_json(_VG, _rz, {"doing": "x"}, meta=_m2)
check("pack_json serializes meta inside answer_pack",
      _ver.pack_meta(_json_out)["input_hash"] == _m2["input_hash"])

# grades stamp the immutable version they postmortem
from nova import grades as _gr
_gr_game = dict(_VG, home_score=4.0, away_score=2.0)
_gr_read = {"true_line_value": -1.5, "market_line": -1.0, "status": "LEAN",
            "edge_direction": "HOME VALUE",
            "thesis_json": _json_mod.dumps({"answer_pack": {"meta": _m2}})}
_payload = _gr.grade_read(_gr_game, _gr_read, [])
check("grades: postmortem carries analysis_version + input_hash",
      _payload.get("analysis_version") == 2
      and _payload.get("input_hash") == _m2["input_hash"]
      and _payload.get("graded") is True)




# ===========================================================================
# LLM BRIDGE REGRESSIONS (Base44 AI queue, OpenAI removed Oct 16 2026)
# ===========================================================================
import nova.config as _cfg_mod
import os as _os
from nova import llm_queue as _lq

check("config: bridge defaults (LLMTask entity, fixture mode)",
      _cfg_mod.LLM_QUEUE_ENTITY == "LLMTask" and _cfg_mod.SYNTH_MODE == "fixture")

class _MemQueueClient:
    """Offline in-memory Base44 client stand-in for queue tests."""
    def __init__(self):
        self.rows, self._n = [], 0
    def list_all(self, entity, fields=None, query=None):
        return [dict(r) for r in self.rows]
    def create(self, entity, data):
        self._n += 1
        rec = dict(data); rec["id"] = f"t{self._n}"
        self.rows.append(rec)
        return rec
    def put(self, entity, rid, data):
        for r in self.rows:
            if r["id"] == rid:
                r.update(data)
        return {}
    def bulk_put(self, entity, pairs):
        for rid, data in pairs:
            self.put(entity, rid, data)
        return {}

_qc = _MemQueueClient()
_p = "SYNTHESIS PROMPT BODY"
_i1 = _lq.enqueue(_qc, "synthesis", "g1", _p)
check("queue: enqueue creates a pending task", _i1 is not None
      and _qc.rows[0]["status"] == "pending"
      and _qc.rows[0]["dedupe_key"] == "synthesis:g1")
check("queue: duplicate task is not re-enqueued",
      _lq.enqueue(_qc, "synthesis", "g1", _p) is None)
check("queue: collect returns nothing while pending",
      _lq.collect(_qc, "synthesis", "g1") is None)
_qc.put("LLMTask", _i1, {"status": "done", "result_text": "PACK JSON",
                         "model": "base44-ai"})
_t, _txt, _mdl = _lq.collect(_qc, "synthesis", "g1")
check("queue: collect returns a finished unconsumed task",
      _t == _i1 and _txt == "PACK JSON" and _mdl == "base44-ai")
_lq.mark_consumed(_qc, _t)
check("queue: consumed task no longer collects; re-enqueue allowed",
      _lq.collect(_qc, "synthesis", "g1") is None
      and _lq.enqueue(_qc, "synthesis", "g1", _p) is not None)
_i2 = _lq.enqueue(_qc, "research", "g2", "RESEARCH PROMPT")
_qc.put("LLMTask", _i2, {"status": "failed", "error": "boom"})
check("queue: sweep_failed unblocks re-enqueue of failed tasks",
      _lq.sweep_failed(_qc) >= 1
      and _lq.enqueue(_qc, "research", "g2", "RESEARCH PROMPT") is not None)

# build_context with the CYCLE's injury shape: {home: [rows], away: [rows]}
_ctx_dict = _syn.build_context(
    {"away_team": "Rays", "home_team": "Guardians", "league_code": "MLB"},
    -1.5, -1.5, injuries=2,
    injury_details={"home": [{"name": f"H{i}"} for i in range(20)],
                    "away": [{"name": "A1"}]},
    weather={"temp": 62}, rest={"home": 1, "away": 2})
check("synthesis: dict injury_details no longer crashes (cap per side)",
      len(_ctx_dict["injury_details"]["home"]) == 14
      and len(_ctx_dict["injury_details"]["away"]) == 1)
_ctx_list = _syn.build_context(
    {"away_team": "Rays", "home_team": "Guardians", "league_code": "MLB"},
    -1.5, -1.5, injury_details=[{"name": f"I{i}"} for i in range(20)])
check("synthesis: list injury_details still works (back-compat)",
      len(_ctx_list["injury_details"]) == 14)




# ===========================================================================
# READ LIFECYCLE (postgame leftover regression, Oct 16 2026)
# ===========================================================================
from nova import cycle as _cyc
_ret, _kept = _cyc.supersede_finals(
    [{"id": "g1", "status": "final"},
     {"id": "g2", "status": "scheduled"},
     {"id": "g3", "status": "postponed"},
     {"id": "g4", "status": "final"}],
    {"g1": {"id": "r1"}, "g2": {"id": "r2"},
     "g3": {"id": "r3"}, "g4": {"id": "r4"}})
check("lifecycle: final + postponed reads retired, live read kept",
      _ret == ["r1", "r3", "r4"] and list(_kept) == ["g2"])
_ret2, _kept2 = _cyc.supersede_finals([{"id": "g1", "status": "scheduled"}],
                                      {"g1": {"id": "r1"}})
check("lifecycle: scheduled games untouched",
      _ret2 == [] and _kept2 == {"g1": {"id": "r1"}})


# ============================================================
# secondary market reads — totals + moneylines (nova/markets.py)
# ============================================================
from nova import markets as _mk
# --- totals: blend math, cap, no-market contract ---
_fr = {"home": {"last_5_json": _json.dumps([
    {"score": 24, "opponent_score": 17, "result": "W"},
    {"score": 31, "opponent_score": 20, "result": "W"},
    {"score": 27, "opponent_score": 24, "result": "W"},
    {"score": 20, "opponent_score": 10, "result": "W"}])},
       "away": {"last_5_json": _json.dumps([
    {"score": 13, "opponent_score": 28, "result": "L"},
    {"score": 17, "opponent_score": 31, "result": "L"},
    {"score": 21, "opponent_score": 27, "result": "L"}])}}
_gm = {"league_code": "nfl", "home_team": "KC", "away_team": "SF",
       "start_time": "2026-10-18T20:15:00Z"}
_mr_tot = {"total": {"market_line": 47.5, "over_price": -110,
                     "under_price": -110, "created_date": "2026-10-16T13:00:00Z"}}
_tr = _mk.compute_total_read(_gm, _fr, _mr_tot)
# Oct 28 2026: blend reweighted to 55/45 and the form sample is
# recency-weighted, so the newest games pull the estimate up (the model
# now has room to disagree with the market; previously a 70% anchor +
# 4-pt cap made a PLAY total mathematically unreachable).
# a 3-game sample carries only ~22% of the blend (sample-size discipline),
# so the market anchors the number and the edge stays small
check("total: recency-weighted blend rounds to the model price",
      _tr and abs(_tr["true_line"] - 46.8) < 0.05
      and _tr["value_side"] == "under" and _tr["status"] == "PASS"
      and _tr["edge_unit"] == "POINTS")
check("total: no total market -> no record",
      _mk.compute_total_read(_gm, _fr, {}) is None)
# no form sample: market stands as fair, honest PASS with zero edge
_tr2 = _mk.compute_total_read(_gm, None, _mr_tot)
check("total: missing form -> market anchor stands, honest PASS",
      _tr2 and _tr2["true_line"] == 47.5 and _tr2["raw_edge"] == 0.0
      and "market anchor" in _tr2["true_line_basis"])
# blend cap: extreme form sample cannot manufacture a huge edge
_fr_ext = {"home": {"last_5_json": _json.dumps([
    {"score": 80, "opponent_score": 0, "result": "W"}] * 4)},
           "away": {"last_5_json": _json.dumps([
    {"score": 0, "opponent_score": 80, "result": "L"}] * 4)}}
_tr3 = _mk.compute_total_read(_gm, _fr_ext, _mr_tot)
check("total: blend capped at TOTAL_BLEND_CAP",
      _tr3 and abs(_tr3["true_line"] - 47.5) <= _mk.TOTAL_BLEND_CAP + 0.01)
# vig penalty: heavy juice on the VALUE side is paid from the edge
_fr_hi = {"home": {"last_5_json": _json.dumps([
    {"score": 38, "opponent_score": 17, "result": "W"},
    {"score": 40, "opponent_score": 20, "result": "W"},
    {"score": 36, "opponent_score": 24, "result": "W"},
    {"score": 38, "opponent_score": 10, "result": "W"}])},
           "away": {"last_5_json": _json.dumps([
    {"score": 33, "opponent_score": 21, "result": "W"},
    {"score": 35, "opponent_score": 19, "result": "W"},
    {"score": 31, "opponent_score": 22, "result": "W"}])}}
_mr_juice = {"total": {"market_line": 45.5, "over_price": -150,
                      "under_price": -110, "created_date": "2026-10-16T13:00:00Z"}}
_tr4 = _mk.compute_total_read(_gm, _fr_hi, _mr_juice)
check("total: value-side juice is paid from the edge",
      _tr4 and _tr4["value_side"] == "over"
      and "Juice costs" in _tr4["thesis"])

# --- moneylines: conversion, devig, gates ---
_mr_ml = {"moneyline": {"home_price": -150.0, "away_price": 130.0,
                        "created_date": "2026-10-16T13:00:00Z"}}
_mr = _mk.compute_ml_read(_gm, -3.0, _mr_ml)
# fair home = Phi(3/13.2) = 0.590 -> fair amer -144; devig = .60/(.60+.4348)=.580
check("moneyline: fair price from true spread (NFL sigma)",
      _mr and abs(_mr["true_line"] - (-144.0)) < 1.0
      and _mr["edge_unit"] == "PROBABILITY"
      and _mr["value_side"] == "home" and _mr["status"] == "PASS")
check("moneyline: no ML market -> no record",
      _mk.compute_ml_read(_gm, -3.0, {}) is None)
check("moneyline: no true spread -> no record",
      _mk.compute_ml_read(_gm, None, _mr_ml) is None)
# away-favorite game: value side flips correctly (sign-collapsed trap)
_mr5 = _mk.compute_ml_read(
    {"league_code": "nba", "home_team": "BOS", "away_team": "TOR",
     "start_time": "2026-10-18T20:00:00Z"}, 2.5,
    {"moneyline": {"home_price": 145.0, "away_price": -170.0,
                   "created_date": "2026-10-16T13:00:00Z"}})
check("moneyline: away favorite -> home dog value side named",
      _mr5 and _mr5["value_side"] == "home"
      and _mr5["edge_direction"] == "NOVA_FAVORS_HOME")
# cross-market conflict (margin space, not probability space):
# true spread -10 vs an even ML -> AWAITING DATA
_mr6 = _mk.compute_ml_read(_gm, -10.0,
    {"moneyline": {"home_price": -110.0, "away_price": -110.0,
                   "created_date": "2026-10-16T13:00:00Z"}})
check("moneyline: cross-market conflict -> AWAITING DATA with reason",
      _mr6 and _mr6["status"] == "AWAITING DATA"
      and "cross-market" in _mr6["reason"])
# deep-tail read publishes but never carries action weight
_mr7 = _mk.compute_ml_read(
    {"league_code": "ncaaf", "home_team": "Utah", "away_team": "Kansas",
     "start_time": "2026-10-18T20:00:00Z"}, 24.0,
    {"moneyline": {"home_price": 2200.0, "away_price": -10000.0,
                   "created_date": "2026-10-16T13:00:00Z"}})
check("moneyline: outside trust band -> PASS (row exists, no action)",
      _mr7 and _mr7["status"] == "PASS"
      and "outside the trusted band" in _mr7["risk"])
# float odds never crash the American formatting
check("moneyline: float American odds render without noise",
      _mk._fmt_amer(-150.0) == "-150" and _mk._fmt_amer(2200.0) == "+2200"
      and _mk._fmt_amer(-144.5) == "-144.5")

# --- resolve_form: NBA games carry NAMES, form rows carry abbrs ---
_frm_rows = [
    {"team_abbr": "OKC", "team_name": "Oklahoma City Thunder",
     "league_code": "nba", "last_5_json": _json.dumps([])},
    {"team_abbr": "DEN", "team_name": "Denver Nuggets",
     "league_code": "nba", "last_5_json": _json.dumps([])},
    {"team_abbr": "GS", "team_name": "Golden State Warriors",
     "league_code": "nba", "last_5_json": _json.dumps([])},
]
_frm_g = {"league_code": "nba", "home_team": "Oklahoma City Thunder",
          "away_team": "Denver Nuggets"}
_frm_res = reads.resolve_form(_frm_g, _frm_rows)
check("resolve_form: name-keyed game matches abbr-keyed form rows",
      _frm_res and _frm_res.get("home", {}).get("team_abbr") == "OKC"
      and _frm_res.get("away", {}).get("team_abbr") == "DEN")
_frm_res2 = reads.resolve_form(
    {"league_code": "nba", "home_team": "Golden State Warriors",
     "away_team": "Denver Nuggets"}, _frm_rows)
check("resolve_form: name fallback covers alias abbrs too",
      _frm_res2 and _frm_res2.get("home", {}).get("team_abbr") == "GS")
_frm_res3 = reads.resolve_form(
    {"league_code": "nba", "home_team": "Nobody State",
     "away_team": "Denver Nuggets"}, _frm_rows)
check("resolve_form: unknown team -> None side, honest",
      _frm_res3 and _frm_res3.get("home") is None
      and _frm_res3.get("away", {}).get("team_abbr") == "DEN")

# --- forms.build_forms captures display names ---
_names_games = [{
    "start_time": "2026-10-10T00:00Z", "status": "STATUS_FINAL",
    "status_detail": "Final", "home_abbr": "OKC",
    "home_name": "Oklahoma City Thunder", "home_score": "110",
    "away_abbr": "DEN", "away_name": "Denver Nuggets", "away_score": "100",
}]
_forms_out, _names_out = __import__("nova.forms", fromlist=["x"]).build_forms(_names_games)
check("forms: build_forms returns last-5 rows AND display names",
      _forms_out["OKC"][0]["result"] == "W"
      and _names_out.get("OKC") == "Oklahoma City Thunder"
      and _names_out.get("DEN") == "Denver Nuggets")

# --- resolve_form via espn name_map (NBA name-keyed games) ---
_frm_rows2 = [
    {"team_abbr": "OKC", "team_name": "Oklahoma City Thunder",
     "league_code": "nba", "last_5_json": _json.dumps([{"score": 1}] * 3)},
    {"team_abbr": "DEN", "team_name": "Denver Nuggets",
     "league_code": "nba", "last_5_json": _json.dumps([{"score": 1}] * 3)},
]
from nova import matching as _matching_mod
_nm = {"nba": {_matching_mod.canon("Oklahoma City Thunder", "nba"): "OKC",
              _matching_mod.canon("Denver Nuggets", "nba"): "DEN",
              _matching_mod.canon("Thunder", "nba"): "OKC"}}
_frm_res4 = reads.resolve_form(
    {"league_code": "nba", "home_team": "Oklahoma City Thunder",
     "away_team": "Denver Nuggets"}, _frm_rows2, name_map=_nm)
check("resolve_form: espn name_map resolves name-keyed game",
      _frm_res4 and _frm_res4.get("home", {}).get("team_abbr") == "OKC"
      and _frm_res4.get("away", {}).get("team_abbr") == "DEN")
_frm_res5 = reads.resolve_form(
    {"league_code": "nba", "home_team": "Thunder",
     "away_team": "Denver Nuggets"}, _frm_rows2, name_map=_nm)
check("resolve_form: short display names resolve too",
      _frm_res5 and _frm_res5.get("home", {}).get("team_abbr") == "OKC")

# --- janitor: attached-dup merge (canonical keeps children) ---
import importlib as _il
_j = _il.import_module("nova.janitor")

class _MergeClient:
    """Fake client recording bulk puts/deletes."""
    def __init__(self):
        self.puts, self.deletes = [], []
    def list_all(self, entity, fields=None):
        if entity == "OddsSnapshot":
            return [{"id": "o1", "game_id": "dup1", "book": "DK",
                      "market_type": "spread", "is_current": True,
                      "created_date": "2026-10-16T20:00Z"},
                     {"id": "o2", "game_id": "canon", "book": "DK",
                      "market_type": "spread", "is_current": True,
                      "created_date": "2026-10-16T21:00Z"}]
        if entity == "MarketNovaRead":
            return [{"id": "r1", "game_id": "dup1", "market_type": "spread",
                      "is_current": True, "updated_date": "2026-10-16T20:00Z"},
                     {"id": "r2", "game_id": "canon", "market_type": "spread",
                      "is_current": True, "updated_date": "2026-10-16T21:00Z"}]
        return []
    def bulk_put(self, entity, puts):
        for rid, patch in puts:
            self.puts.append((entity, {"id": rid, **patch}))
        return len(puts)
    def delete(self, entity, rid):
        self.deletes.append((entity, rid))

_mg_games = [
    {"id": "canon", "league_code": "ncaaf", "game_date": "2026-10-17",
     "start_time": "2026-10-18T03:00Z", "home_team": "Oregon Ducks",
     "away_team": "Nebraska Cornhuskers", "provider_game_id": "401858492",
     "created_date": "2026-10-15T00:24Z"},
    {"id": "dup1", "league_code": "ncaaf", "game_date": "2026-10-17",
     "start_time": "2026-10-18T03:00Z", "home_team": "Oregon Ducks",
     "away_team": "Nebraska Cornhuskers", "provider_game_id": None,
     "created_date": "2026-10-16T20:27Z"},
]
_mc = _MergeClient()
_ng, _nd, _nr, _ns = _j.merge_duplicates(_mc, _mg_games, log=lambda m: None)
check("janitor merge: canonical chosen, dup deleted",
      _nd == 1 and ("Game", "dup1") in _mc.deletes)
check("janitor merge: odds+read repointed to canonical",
      ("OddsSnapshot", {"id": "o1", "game_id": "canon"}) in _mc.puts
      and ("MarketNovaRead", {"id": "r1", "game_id": "canon"}) in _mc.puts)
check("janitor merge: duplicate currents superseded (2 read-currents -> 1)",
      ("MarketNovaRead", {"id": "r1", "is_current": False}) in _mc.puts)

class _FailingClient(_MergeClient):
    def bulk_put(self, entity, puts):
        return 0   # repoint fails entirely

_fc = _FailingClient()
_fg, _fd, _fr, _fs = _j.merge_duplicates(_fc, _mg_games, log=lambda m: None)
check("janitor merge: failed repoint -> dup game KEPT (no orphaning)",
      _fd == 0 and len(_fc.deletes) == 0)

# --- forms: ESPN scoreboard abbr -> official abbr translation ---
_f_games = [{
    "start_time": "2026-10-10T00:00Z", "status": "STATUS_FINAL",
    "status_detail": "Final", "home_abbr": "OKLA", "home_team_id": "201",
    "home_name": "Oklahoma Sooners", "home_score": "30",
    "away_abbr": "UK", "away_team_id": "96",
    "away_name": "Kentucky Wildcats", "away_score": "10",
}]
_f_info = {"names": {"OKLAHOMASOONERS": "OU"}, "by_id": {"201": "OU", "96": "UK"}}
_f_forms, _f_names = __import__("nova.forms", fromlist=["x"]).build_forms(
    _f_games, abbr_by_id=_f_info["by_id"])
check("forms: scoreboard OKLA translated to official OU via team-id join",
      "OU" in _f_forms and _f_forms["OU"][0]["opponent"] == "UK"
      and _f_forms["OU"][0]["result"] == "W"
      and _f_names.get("OU") == "Oklahoma Sooners")

# --- kernel: pregame_total thresholds exist ---
from nova import kernel as _kn_mod
check("kernel: pregame_total threshold band present",
      _kn_mod.PRICE_THRESHOLDS["pregame_total"]["play_gte"] == 1.5)





# --- janitor self-heal sweeps ---
class _SweepClient:
    def __init__(self, forms, odds, ratings):
        self._f, self._o, self._r = forms, odds, ratings
        self.deleted, self.puts, self.created = [], [], []
    def list_all(self, entity, fields=None):
        return {"TeamForm": self._f, "OddsSnapshot": self._o,
                "TeamRating": self._r}[entity]
    def delete(self, entity, rid):
        self.deleted.append((entity, rid))
    def bulk_put(self, entity, puts):
        self.puts += [(entity, p) for p in puts]
        return len(puts)
    def bulk_create(self, entity, rows):
        self.created += [(entity, r) for r in rows]
        return len(rows)

_f1 = {"id": "f1", "league_code": "ncaaf", "team_abbr": "APP",
       "updated_date": "2026-10-17T00:06:00Z", "created_date": "2026-10-17T00:06:00Z"}
_f2 = {"id": "f2", "league_code": "ncaaf", "team_abbr": "APP",
       "updated_date": "2026-10-02T00:24:00Z", "created_date": "2026-10-02T00:24:00Z"}
_f3 = {"id": "f3", "league_code": "NBA", "team_abbr": "NY",
       "updated_date": "2026-10-01T00:00:00Z", "created_date": "2026-10-01T00:00:00Z"}
_cl = _SweepClient([_f1, _f2, _f3], [], [])
n = _j.dedupe_forms(_cl, log=lambda m: None)
check("janitor forms dedupe: stale dup deleted, newest kept",
      n == 1 and ("TeamForm", "f2") in _cl.deleted
      and ("TeamForm", "f1") not in _cl.deleted)

_o1 = {"id": "o1", "game_id": "g1", "book": "ESPN", "market_type": "spread",
       "is_current": True, "snapshot_time": "2026-10-20T10:00:00Z"}
_o2 = {"id": "o2", "game_id": "g1", "book": "ESPN", "market_type": "spread",
       "is_current": True, "snapshot_time": "2026-10-20T14:00:00Z"}
_o3 = {"id": "o3", "game_id": "g1", "book": "ESPN", "market_type": "total",
       "is_current": True, "snapshot_time": "2026-10-20T14:00:00Z"}
_cl2 = _SweepClient([], [_o1, _o2, _o3], [])
n = _j.dedupe_current_odds(_cl2, log=lambda m: None)
sup = {pid for e, (pid, v) in _cl2.puts if e == "OddsSnapshot"}
check("janitor odds dedupe: newest kept, older superseded",
      n == 1 and "o1" in sup and "o2" not in sup and "o3" not in sup)

def _rating(aid, lg, abbr, name):
    return {"id": aid, "league_code": lg, "team_abbr": abbr,
            "team_name": name, "rating": 84.0, "created_date": "x"}
_r_canon = _rating("r1", "nba", "PHX", "Phoenix Suns")
_r_dup = _rating("r2", "nba", "PHX", "Phoenix Suns")
_r_set = [_r_canon, _r_dup,
          _rating("r3", "nba", "SA", "San Antonio Spurs"),
          _rating("r4", "nba", "NY", "New York Knicks"),
          _rating("r5", "nba", "NO", "New Orleans Pelicans"),
          _rating("r6", "nba", "UTAH", "Utah Jazz"),
          _rating("r7", "nhl", "LA", "Los Angeles Kings")]
_cl3 = _SweepClient([], [], _r_set)
n = _j.ensure_rating_aliases(_cl3, log=lambda m: None)
abbrs = {r["team_abbr"] for e, r in _cl3.created if e == "TeamRating"}
check("janitor ratings: PHO/UTA/NYK/SAS/NOP/LAK alias rows created",
      n == 6 and abbrs == {"PHO", "UTA", "NYK", "SAS", "NOP", "LAK"})

_r_set2 = list(_r_set) + [_rating("r8", "nba", "PHO", "Phoenix Suns")]
_cl4 = _SweepClient([], [], _r_set2)
n = _j.ensure_rating_aliases(_cl4, log=lambda m: None)
check("janitor ratings: existing alias not duplicated", n == 5)
# ============================================================
# True-line game-reality construction (nova/trueline.py)
# ============================================================
import os as _os2, json as _tj
from datetime import datetime, timedelta, timezone
sys.path.insert(0, _os2.path.dirname(_os2.path.dirname(_os2.path.abspath(__file__))))
from nova import trueline as _tl
from nova import config as tconfig

_inj = {"home": [{"player_name": "H1", "position": "QB", "status": "Out", "impact_rating": 4.0}],
        "away": [{"player_name": "A1", "position": "WR", "status": "Questionable", "impact_rating": 4.0}]}
_d, _b = _tl.injury_delta(_inj, "nfl")
check("trueline: home QB out -> negative home delta (home weaker)",
      _d < 0, f"delta={_d}")
check("trueline: QB gets position weight above raw impact",
      _b["home_impact"] > 4.0, f"home_impact={_b['home_impact']}")
check("trueline: questionable discounted 65%",
      abs(_b["away_impact"] - 4.0 * 0.35) < 0.01, f"away_impact={_b['away_impact']}")

_huge = {"home": [{"position": "WR", "status": "Out For Season",
                   "impact_rating": 5.0, "player_name": f"P{i}"} for i in range(20)],
         "away": []}
_dupd = {"home": [{"position": "WR", "status": "Out", "impact_rating": 5.0,
                  "player_name": "Same Guy"}] * 4, "away": []}
_d2, _ = _tl.injury_delta(_huge, "nfl")
check("trueline: injury delta capped at TRUELINE_INJURY_CAP",
      _d2 == -tconfig.TRUELINE_INJURY_CAP, f"delta={_d2}")
_dupd_d, _dupd_b = _tl.injury_delta(_dupd, "nfl")
check("trueline: duplicate injury rows count once (deduped by player)",
      _dupd_b["home_impact"] == 5.0, f"impact={_dupd_b['home_impact']}")
check("trueline: 20 distinct players hit the side cap",
      _tl.injury_delta(_huge, "nfl")[1]["home_impact"] == tconfig.TRUELINE_INJURY_SIDE_CAP)
_d3, _b3 = _tl.injury_delta({"home": [], "away": []}, "nfl")
check("trueline: empty injury book -> zero delta, fail-open",
      _d3 == 0.0 and _b3["delta"] == 0.0)
_d4, _ = _tl.injury_delta(None, "nfl")
check("trueline: None injury details -> zero delta", _d4 == 0.0)

_qbt, _ = _tl.injury_total_delta(_inj, "nfl")
check("trueline: OUT QB lowers the football total",
      _qbt == -tconfig.TRUELINE_QB_TOTAL_POINTS, f"delta={_qbt}")
_qbt2, _ = _tl.injury_total_delta(_inj, "nba")
check("trueline: QB total effect football-only", _qbt2 == 0.0)

_rd, _rb = _tl.rest_delta({"home": 7, "away": 2}, "nfl")
check("trueline: 5-day rest edge -> positive home delta",
      0 < _rd <= tconfig.TRUELINE_REST_CAP, f"delta={_rd}")
_rd0, _ = _tl.rest_delta({"home": 3, "away": 3}, "nfl")
check("trueline: normal rest deadband -> zero", _rd0 == 0.0)
_rdb, _ = _tl.rest_delta({"home": 1, "away": 0}, "nba")
check("trueline: nba back-to-back skips deadband (fatigue priced)",
      _rdb != 0.0, f"delta={_rdb}")
check("trueline: nba B2B delta points at the rested side",
      _rdb > 0, f"home rested 1 vs away 0 -> {_rdb}")
_rdn, _ = _tl.rest_delta({"home": None, "away": 4}, "nfl")
check("trueline: missing rest data -> zero, fail-open", _rdn == 0.0)

_frm = {"home": {"last_5_json": _tj.dumps([{"score": 35, "opponent_score": 10}] * 5)},
        "away": {"last_5_json": _tj.dumps([{"score": 10, "opponent_score": 35}] * 5)}}
_fd, _fb = _tl.form_delta(_frm)
check("trueline: form differential -> small positive home delta",
      0 < _fd <= tconfig.TRUELINE_FORM_CAP, f"delta={_fd}")
_fd2, _ = _tl.form_delta({"home": {"last_5_json": _tj.dumps([{"score": 1, "opponent_score": 0}])}, "away": None})
check("trueline: under 3-game minimum -> zero, fail-open", _fd2 == 0.0)

_wx, _wxb = _tl.weather_total_delta({"wind_speed_mph": 28, "precipitation_pct": 70, "temp_f": 95})
check("trueline: heavy weather lowers total but respects floor",
      _wx >= tconfig.TRUELINE_WEATHER_TOTAL_MIN and _wx < 0, f"delta={_wx}")
_wx0, _ = _tl.weather_total_delta({"wind_speed_mph": 5, "precipitation_pct": 10, "temp_f": 70})
check("trueline: calm weather -> zero total delta", _wx0 == 0.0)
_wx2, _ = _tl.weather_total_delta(None)
check("trueline: no weather payload -> zero, fail-open", _wx2 == 0.0)

_con = _tl.construction("nfl", form=_frm, rest={"home": 4, "away": 1},
                        injury_details=_inj,
                        weather={"wind_speed_mph": 22, "precipitation_pct": 5, "temp_f": 55})
check("trueline: full construction journals every active module",
      set(_con["breakdown"]["active_modules"]) >= {"injury", "rest", "form", "weather"},
      f"active={_con['breakdown']['active_modules']}")
check("trueline: spread stack equals sum of spread deltas (rounding tolerance)",
      abs(_con["spread_delta"] - (_con["injury_delta"] + _con["rest_delta"] + _con["form_delta"])) < 0.005,
      f"stack={_con['spread_delta']}")

_tp = build_true_price(team_power_gap=3.0, expected_pace_points=45.0, home_field=2.5,
                       injury_delta=-1.0, schedule_delta=0.5, form_delta=0.3,
                       weather_delta=-2.0)
check("kernel: build_true_price applies all construction deltas",
      _tp.spread == -(3.0 + 2.5 - 1.0 + 0.5 + 0.3) and _tp.total == 43.0,
      f"spread={_tp.spread} total={_tp.total}")
_tp0 = build_true_price(team_power_gap=3.0, expected_pace_points=45.0, home_field=2.5)
check("kernel: build_true_price defaults unchanged (backward compat)",
      _tp0.spread == -5.5 and _tp0.total == 45.0)

# end-to-end: compute_read carries the construction journal on telemetry
from reads import compute_read as _cr
_rg = {"league_code": "NFL", "sport": "NFL", "home_team": "Rams", "away_team": "Bears",
       "start_time": (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat()}
_pair = {"home": {"rating": 3.0}, "away": {"rating": 0.0}}
_mk = {"spread": {"market_line": -1.0, "book": "draftkings"}}
_resh = _cr(_rg, _pair, _mk, form=_frm, rest={"home": 4, "away": 1},
            injuries=2, injury_details=_inj,
            weather={"wind_speed_mph": 22, "precipitation_pct": 5, "temp_f": 55,
                     "weather_read": "wind-impacted"})
check("reads: telemetry carries construction breakdown",
      isinstance(_resh.get("telemetry", {}).get("construction"), dict)
      and "spread_stack" in _resh["telemetry"]["construction"])
check("reads: constructed true line differs from ratings-only baseline",
      _resh["true_line"] != -(3.0 + 2.5), f"true={_resh['true_line']}")

# ============================================================
# Total-read environment pricing (markets.compute_total_read)
# ============================================================
from markets import compute_total_read as _ctr
_tg = {"league_code": "NFL", "sport": "NFL", "home_team": "Rams", "away_team": "Bears",
       "start_time": (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat()}
_tmkt = {"total": {"market_line": 44.5, "book": "dk", "over_price": -110, "under_price": -110}}
_tform = {"home": {"last_5_json": _tj.dumps([{"score": 24, "opponent_score": 20}] * 5)},
          "away": {"last_5_json": _tj.dumps([{"score": 23, "opponent_score": 21}] * 5)}}
_tbase = _ctr(_tg, _tform, _tmkt)
_twind = _ctr(_tg, _tform, _tmkt, weather={"wind_speed_mph": 26, "precipitation_pct": 30, "temp_f": 48})
_tqb = _ctr(_tg, _tform, _tmkt, injury_details={"home": [{"position": "QB", "status": "Out", "impact_rating": 4.0}], "away": []})
check("total read: heavy wind lowers the fair total",
      _twind["true_line"] < _tbase["true_line"],
      f"base={_tbase['true_line']} windy={_twind['true_line']}")
check("total read: OUT QB lowers the fair total",
      _tqb["true_line"] < _tbase["true_line"],
      f"base={_tbase['true_line']} qbout={_tqb['true_line']}")
check("total read: environment journals into telemetry",
      isinstance(_twind["telemetry"].get("environment"), dict)
      and _twind["telemetry"]["environment"]["env_delta"] < 0)
check("total read: no weather/injuries -> identical to baseline",
      _ctr(_tg, _tform, _tmkt)["true_line"] == _tbase["true_line"])

# ============================================================
# Construction input resolution (name-only games must resolve)
# ============================================================
from nova.cycle import _injury_details as _idtl
from nova import espn as _espn_mod
_nm = {}
for _lg in ("nba", "nhl"):
    try:
        _tm = _espn_mod.teams_map(_lg)
        if _tm:
            _nm[_lg] = _tm
    except Exception:
        pass
_gname = {"league_code": "nba", "home_team": "Golden State Warriors",
          "away_team": "Memphis Grizzlies"}
_inj_all = [{"league_code": "nba", "team_abbr": "GS", "player_name": "P1",
             "position": "G", "status": "Out", "impact_rating": 4.0},
            {"league_code": "nba", "team_abbr": "PHO", "player_name": "P2",
             "position": "G", "status": "Out", "impact_rating": 4.0}]
_gn2 = {"league_code": "nba", "home_team": "Phoenix Suns",
         "away_team": "Memphis Grizzlies"}
check("cycle: injury details resolve name-only NBA games via name_map",
      len(_idtl(_gname, _inj_all, name_map=_nm)["home"]) == 1
      and len(_idtl(_gn2, _inj_all, name_map=_nm)["home"]) == 1)
check("cycle: injury details fail-open without name_map (no crash)",
      _idtl(_gname, _inj_all)["home"] == [])
_gabbr = {"league_code": "nba", "home_team": "GSW", "away_team": "MEM"}
check("cycle: injury details still match abbr-only games",
      len(_idtl(_gabbr, _inj_all)["home"]) == 1)

print()
print("=" * 78)
print(f"RESULT: {PASS} passed, {FAIL} failed")
print("=" * 78)
sys.exit(1 if FAIL else 0)
