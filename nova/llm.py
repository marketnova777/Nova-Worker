"""Pluggable narrative generation for MarketNovaRead fields.

If LLM_API_KEY is set (Anthropic-style), narrative fields are drafted by an
LLM call per NEW read only — never on unchanged reads, so the write budget
stays minimal. Without a key, reads.py's deterministic templates are used,
which are honest and numbers-grounded already.

Keeping this optional is deliberate: the worker must never BLOCK on an LLM.
Numbers and decisions come from kernel.py; narrative is decoration on top.
"""
import json
import urllib.request

from nova import config

PROMPT_HEADER = """You write market-analysis copy for MarketNova, a sportsbook-intelligence product.
Voice: institutional, calm, Bloomberg-terminal register. No hype, no 'AI' fluff, no emoji.
Terminology: price, number, edge, market. Status taxonomy only: PLAY/LEAN/MONITOR/PASS/NUMBER GONE/AWAITING DATA.
Never say 'bet', 'lock', 'guaranteed', or 'A-Grade'. Ground every claim in the numbers provided.
Output strict JSON: {"what_the_book_is_doing": "...", "one_line_read": "..."}.
what_the_book_is_doing: 2-3 sentences explaining what the market price reflects and why it sits there.
one_line_read: one sentence summarizing the true-line gap and status."""


def narrate(game, result):
    """Return (what_the_book_is_doing, one_line_read) or None on any failure."""
    if not config.LLM_API_KEY:
        return None
    body = json.dumps({
        "model": config.LLM_MODEL,
        "max_tokens": 500,
        "system": PROMPT_HEADER,
        "messages": [{"role": "user", "content": json.dumps({
            "sport": game.get("league_code"),
            "game": f"{game.get('away_team')} @ {game.get('home_team')}",
            "market_line": result.get("market_line"),
            "true_line": result.get("true_line"),
            "raw_edge": result.get("raw_edge"),
            "status": result.get("status"),
        })}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
            "x-api-key": config.LLM_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read())
        text = data["content"][0]["text"]
        parsed = json.loads(text)
        return parsed.get("what_the_book_is_doing"), parsed.get("one_line_read")
    except Exception as e:
        print(f"  llm: narrative failed ({e}); templates will be used")
        return None
