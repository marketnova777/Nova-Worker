"""
Cycle gate — decide whether a full Nova cycle is worth running now.

Exits 0 (run the cycle) when any of:
  - a game is live / in progress
  - a game starts within GATE_UPCOMING_H hours (default 8)
  - a game went final within GATE_FINAL_H hours (default 3, so scores sync)
Exits 1 (skip, costs ~10 seconds) when the slate is quiet — keeps a
30-minute cadence without burning Actions minutes on dead overnight runs.
"""

import os
import sys
from datetime import datetime, timezone

from nova.api import Base44Client

GATE_UPCOMING_H = float(os.environ.get("GATE_UPCOMING_H", "8"))
GATE_FINAL_H = float(os.environ.get("GATE_FINAL_H", "3"))

LIVE = {"live", "in_progress", "halftime", "second_half", "q1", "q2", "q3", "q4",
        "1st", "2nd", "3rd", "ot", "final_ot", "top", "bottom", "mid", "pre"}
FINAL = {"final", "final_ot", "completed", "post"}


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def main():
    client = Base44Client()
    games = client.list_all("Game", fields=["id", "status", "start_time"])
    now = datetime.now(timezone.utc)
    reasons = {"live": 0, "upcoming": 0, "final_recent": 0}
    for g in games:
        status = str(g.get("status") or "").lower()
        if status in LIVE:
            reasons["live"] += 1
            continue
        if status in FINAL:
            # Game.updated_date is our best proxy for when it finished.
            done = _parse(g.get("updated_date"))
            if done and (now - done).total_seconds() <= GATE_FINAL_H * 3600:
                reasons["final_recent"] += 1
            continue
        start = _parse(g.get("start_time"))
        if start and 0 <= (start - now).total_seconds() <= GATE_UPCOMING_H * 3600:
            reasons["upcoming"] += 1

    run = any(reasons.values())
    print(f"gate: live={reasons['live']} upcoming<{GATE_UPCOMING_H}h={reasons['upcoming']} "
          f"final<{GATE_FINAL_H}h={reasons['final_recent']} -> {'RUN' if run else 'SKIP'}")
    sys.exit(0 if run else 1)


if __name__ == "__main__":
    main()
