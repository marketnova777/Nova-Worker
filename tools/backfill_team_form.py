#!/usr/bin/env python3
"""Backfill/refresh TeamForm rows from ESPN completed games.

The heavy lifting lives in nova/forms.py (shared with the cycle's
daily self-heal). Usage:

    python3 tools/backfill_team_form.py [leagues...]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nova import config, forms
from nova.api import Base44Client

DEFAULT_LEAGUES = ["nba", "nfl", "nhl", "mlb"]

if __name__ == "__main__":
    leagues = [l.lower() for l in (sys.argv[1:] or DEFAULT_LEAGUES)]
    client = Base44Client()
    stats = forms.refresh(client, leagues)
    for lg, (games, teams, upd, cre) in stats.items():
        print(f"{lg}: {games} completed games -> {teams} teams "
              f"({upd} updated, {cre} created)")
