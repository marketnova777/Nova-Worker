"""Pluggable narrative generation for MarketNovaRead fields.

If LLM_API_KEY is set (Anthropic-style), narrative fields are drafted by an
LLM call per NEW read only — never on unchanged reads, so the write budget
stays minimal. Without a key, reads.py's deterministic templates are used,
which are honest and numbers-grounded already.

Keeping this optional is deliberate: the worker must never BLOCK on an LLM.
Numbers and decisions come from kernel.py; narrative is decoration on top.
"""
import json
import time
import urllib.request

from nova import config

PROMPT_HEADER = """You write market-analysis copy for MarketNova, a sportsbook-intelligence product.
Voice: institutional, calm, Bloomberg-terminal register. No hype, no 'AI' fluff, no emoji.
Terminology: price, number, edge, market. Status taxonomy only: PLAY/MONITOR/PASS/NUMBER GONE/AWAITING DATA (LEAN is retired — an edge is a PLAY).
Never say 'bet', 'lock', 'guaranteed', or 'A-Grade'. Ground every claim in the numbers provided.
Output strict JSON: {"what_the_book_is_doing": "...", "one_line_read": "..."}.
what_the_book_is_doing: 2-3 sentences explaining what the market price reflects and why it sits there.
one_line_read: one sentence summarizing the true-line gap and status."""


LAST_CALL = {}


def _record(provider, model, usage, latency_ms):
    pin = int((usage or {}).get("prompt_tokens") or (usage or {}).get("input_tokens") or 0)
    pout = int((usage or {}).get("completion_tokens") or (usage or {}).get("output_tokens") or 0)
    cost = pin / 1e6 * config.LLM_PRICE_IN + pout / 1e6 * config.LLM_PRICE_OUT
    LAST_CALL.clear()
    LAST_CALL.update({"provider": provider, "model": model, "prompt_tokens": pin,
                      "completion_tokens": pout, "cost_usd": round(cost, 6),
                      "latency_ms": round(latency_ms)})
    print(f"  llm: provider={provider} model={model} in={pin} out={pout} "
          f"cost_usd={cost:.6f} latency_ms={latency_ms:.0f}")
    return LAST_CALL


def _call_openai(system, user, max_tokens=500):
    body = json.dumps({"model": config.LLM_MODEL, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}",
                 "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    _record("openai", data.get("model", config.LLM_MODEL), data.get("usage"),
            (time.time() - t0) * 1000)
    return data["choices"][0]["message"]["content"]


def _call_anthropic(system, user, max_tokens=500):
    body = json.dumps({"model": config.LLM_MODEL, "max_tokens": max_tokens,
                       "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": config.LLM_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    _record("anthropic", data.get("model", config.LLM_MODEL), data.get("usage"),
            (time.time() - t0) * 1000)
    return data["content"][0]["text"]


def complete(system, user, max_tokens=500):
    """One real model call. Raises on failure so callers can decide."""
    if not config.LLM_API_KEY:
        raise RuntimeError("no LLM credential configured")
    if config.LLM_PROVIDER == "anthropic":
        return _call_anthropic(system, user, max_tokens)
    return _call_openai(system, user, max_tokens)


def narrate(game, result):
    """Return (what_the_book_is_doing, one_line_read) or None on any failure."""
    if not config.LLM_API_KEY:
        return None
    user = json.dumps({"sport": game.get("league_code"),
                       "game": f"{game.get('away_team')} @ {game.get('home_team')}",
                       "market_line": result.get("market_line"),
                       "true_line": result.get("true_line"),
                       "raw_edge": result.get("raw_edge"),
                       "status": result.get("status")})
    try:
        text = complete(PROMPT_HEADER, user, 500)
        parsed = json.loads(text)
        return parsed.get("what_the_book_is_doing"), parsed.get("one_line_read")
    except Exception as e:
        print(f"  llm: narrative failed ({e}); templates will be used")
        return None
