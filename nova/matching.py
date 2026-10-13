"""Shared matching helpers — abbr alias map + same-day discipline.

The alias map is the canonical team-identity fix from the Sep-Oct cleanup:
ESPN and odds-api abbreviations differ (TOR/TORO, NJ/NJD, TBL/TB, ...).
Every match path goes through here so one map fixes all of them.
"""
from datetime import datetime, timezone

ALIASES = {
    "TORO": "TOR", "LVA": "LV", "NYL": "NY", "WAS": "WSH", "GOLD": "GS",
    "TBL": "TB", "NJD": "NJ", "GEOR": "UGA", "ALAB": "ALA", "VIRG": "VT",
}

# League-scoped aliases for odds-api abbreviation quirks verified against
# ESPN during the Sep-Oct 2026 cleanups (e.g. odds-api calls Liberty "NY"
# in NCAAF, but "NY" is New York Liberty in WNBA — so scope by league).
LEAGUE_ALIASES = {
    "ncaaf": {
        "NY": "LIB", "SAMH": "SHSU", "SANJ": "SJSU", "WYOM": "WYO",
        "MISS": "MSST",
        # full-name quirks verified against ESPN (Oct 2026 cleanup):
        "SAMHOUSTONSTATE": "SHSU", "SANJOSESTATE": "SJSU", "MIAMIOHIO": "M-OH", "CONN": "UCONN",
        "ULLAFAYETTE": "UL", "MIAMIOH": "M-OH", "CONNECTICUT": "UCONN",
        "APPALACHIANSTATE": "APP", "LOUISIANATECH": "LT",
    },
    "wnba": {"NY": "NYL"},
}


_ACCENTS = str.maketrans("áéíóúñ", "aeioun")


def canon(abbr, league=""):
    a = (abbr or "").upper().strip().replace(" ", "").translate(_ACCENTS)
    l = str(league or "").lower()
    if l in LEAGUE_ALIASES and a in LEAGUE_ALIASES[l]:
        return LEAGUE_ALIASES[l][a]
    return ALIASES.get(a, a)


def abbr_matches(game_abbrs, team_a, team_b, league=""):
    """True if both canonical abbrs appear in the game's abbr set."""
    have = {canon(a, league) for a in game_abbrs if a}
    return canon(team_a, league) in have and canon(team_b, league) in have


def same_day(game, iso_time, tol_hours=36):
    """Same calendar day-ish match (kickoff times can shift by a few hours)."""
    try:
        g = datetime.fromisoformat(str(game.get("start_time", "")).replace("Z", "+00:00"))
        t = datetime.fromisoformat(str(iso_time).replace("Z", "+00:00"))
        if g.tzinfo is None:
            g = g.replace(tzinfo=timezone.utc)
        return abs((g - t).total_seconds()) < tol_hours * 3600
    except (ValueError, TypeError):
        return False
