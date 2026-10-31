"""End-to-end Nova research smoke test.

live read -> grounded prompt -> real model call -> answer checked against the
canonical Brain numbers -> usage/cost/latency printed.

Fails closed: exits 1 when no genuine model call happens (missing credential,
quota, network). Deterministic pricing is never touched — this only proves the
research layer and measures what a call actually costs.
"""
import json
import re
import sys

from nova import config, llm
from nova.api import Base44Client

SYSTEM = """You are Nova's research layer for MarketNova, a sportsbook market-intelligence product.
You explain and stress-test a read that has ALREADY been priced by the deterministic Brain.
Rules:
- Use ONLY the numbers provided. Never invent injuries, line movement, prices or statistics.
- You may reason about why the price sits where it does and what would break the thesis.
- If information needed for a claim is absent, say it is unavailable.
- Output strict JSON: {"explanation": "...", "primary_risk": "...", "what_would_change_nova": "..."}
"""


def pick_read(client):
    reads = client.list_all("MarketNovaRead", fields=[
        "id", "game_id", "market_type", "status", "market_line",
        "marketnova_true_line", "raw_edge", "buy_to", "edge_unit"])
    games = {g["id"]: g for g in client.list_all("Game", fields=[
        "id", "away_team", "home_team", "league_code", "start_time"])}
    for r in reads:
        if not r.get("game_id") or r.get("market_line") is None:
            continue
        if r.get("marketnova_true_line") is None:
            continue
        g = games.get(r["game_id"])
        if g:
            return r, g
    return None, None


def main():
    print(f"provider={config.LLM_PROVIDER} model={config.LLM_MODEL} "
          f"key_present={bool(config.LLM_API_KEY)}")
    if not config.LLM_API_KEY:
        print("BLOCKED: no LLM credential in the runtime environment")
        return 1
    client = Base44Client()
    read, game = pick_read(client)
    if not read:
        print("BLOCKED: no priced read available to ground the test")
        return 1
    facts = {
        "matchup": f"{game.get('away_team')} @ {game.get('home_team')}",
        "league": game.get("league_code"),
        "market": read.get("market_type"),
        "book_line": read.get("market_line"),
        "nova_true_line": read.get("marketnova_true_line"),
        "dislocation": read.get("raw_edge"),
        "buy_to": read.get("buy_to"),
        "state": read.get("status"),
    }
    print("grounded on:", json.dumps(facts))
    try:
        text = llm.complete(SYSTEM, json.dumps(facts), 600)
    except Exception as e:
        print(f"BLOCKED: model call failed -> {type(e).__name__}: {str(e)[:220]}")
        return 1
    call = dict(llm.LAST_CALL)
    try:
        answer = json.loads(text)
    except Exception:
        answer = {"explanation": text[:600]}
    print("\nANSWER:", json.dumps(answer)[:900])
    # grounding guard: any number in the answer must appear in the facts
    allowed = {str(v).lstrip("-") for v in facts.values() if isinstance(v, (int, float))}
    stray = [n for n in re.findall(r"-?\d+(?:\.\d+)?", json.dumps(answer))
             if n.lstrip("-") not in allowed]
    print("\nUSAGE:", json.dumps(call))
    print("numbers not in the Brain read (fabrication check):", stray[:8] or "none")
    print("RESULT:", "MODEL_CALL_VERIFIED" if call.get("prompt_tokens") else "NO_USAGE_RETURNED")
    return 0 if call.get("prompt_tokens") else 1


if __name__ == "__main__":
    sys.exit(main())
