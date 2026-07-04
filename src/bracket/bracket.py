"""
bracket.py — Match dataclass and bracket resolver for knockout tournaments.

Loads a fixtures CSV (real or synthetic test bracket), resolves W<id>/L<id>
placeholder slots to actual team names as match results are recorded, and
provides round-ordered traversal for the Monte Carlo engine.

Usage
-----
    from src.bracket.bracket import BracketResolver
    from src.config import FIXTURES_PATH

    resolver = BracketResolver.from_csv(FIXTURES_PATH)
    for round_name, matches in resolver.rounds_ordered():
        for match in matches:
            home = resolver.resolve_slot(match.home_slot)
            away = resolver.resolve_slot(match.away_slot)
            # ... simulate outcome ...
            resolver.record_result(match.match_id, winner, loser)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Stadium → host country mapping for 2026 World Cup venues.
# Used to derive is_neutral at resolution time: a match is NOT neutral when
# one of the host nations plays at their home venue.
# ---------------------------------------------------------------------------

STADIUM_COUNTRY: dict[str, str] = {
    "Los Angeles Stadium":            "United States",
    "Boston Stadium":                 "United States",
    "Estadio Monterrey":              "Mexico",
    "Houston Stadium":                "United States",
    "New York New Jersey Stadium":    "United States",
    "Dallas Stadium":                 "United States",
    "Mexico City Stadium":            "Mexico",
    "Atlanta Stadium":                "United States",
    "San Francisco Bay Area Stadium": "United States",
    "Seattle Stadium":                "United States",
    "Toronto Stadium":                "Canada",
    "BC Place Vancouver":             "Canada",
    "Miami Stadium":                  "United States",
    "Kansas City Stadium":            "United States",
    "Philadelphia Stadium":           "United States",
    # Synthetic brackets
    "Test Stadium":                   "__neutral__",
}

# 2026 host nations — only these teams can claim home advantage in the knockout stage.
HOST_TEAMS: frozenset[str] = frozenset({"United States", "Canada", "Mexico"})

# Canonical round order for bracket traversal.
_ROUND_ORDER: dict[str, int] = {
    "Round of 32":          0,
    "Round of 16":          1,
    "Quarter-finals":       2,
    "Semi-finals":          3,
    "Third place play-off": 4,
    "Final":                5,
}

_PLACEHOLDER_RE = re.compile(r"^[WL]\d+$")

# Rounds that get their own archived prediction page / URL slug on the
# results site. Bracket order matters here (used by detect_frontier_round
# and by the site's round-switcher sort order). "Third place play-off" is
# intentionally excluded -- it shares its participants' resolution with
# "Final" and has no URL slug of its own.
FRONTIER_ROUNDS: list[str] = [
    "Round of 32", "Round of 16", "Quarter-finals", "Semi-finals", "Final",
]


# ---------------------------------------------------------------------------
# Match dataclass
# ---------------------------------------------------------------------------


@dataclass
class Match:
    """A single fixture in the knockout bracket.

    ``home_slot`` / ``away_slot`` are the raw CSV values.  They are either:
    - A literal team name  (Round of 32, or any round once groups resolve)
    - ``"W<id>"``  — winner of match ``<id>``
    - ``"L<id>"``  — loser  of match ``<id>`` (Third-place match only)
    """

    match_id: int
    round: str
    home_slot: str
    away_slot: str
    stadium: str
    date: pd.Timestamp


# ---------------------------------------------------------------------------
# Bracket resolver
# ---------------------------------------------------------------------------


class BracketResolver:
    """Resolves W<id>/L<id> placeholders to team names as results come in.

    Maintains a running record of match winners and losers so that
    subsequent rounds can be resolved once prior results are known.
    """

    def __init__(self, matches: list[Match]) -> None:
        self._matches: dict[int, Match] = {m.match_id: m for m in matches}
        self._winners: dict[int, str] = {}
        self._losers: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_csv(cls, path: Path | str) -> "BracketResolver":
        """Load a fixtures CSV and return a BracketResolver.

        Expected columns: match_id, round, home_team, away_team, stadium, date.
        The ``date`` column accepts both dayfirst (28-06-2026) and ISO formats.
        """
        raw = pd.read_csv(path)
        # Strip leading/trailing whitespace from the date column before parsing
        # (fixtures.csv entries sometimes have trailing spaces).
        raw["date"] = raw["date"].astype(str).str.strip()
        # Try ISO parsing first; fall back to dayfirst (European format like 28-06-2026)
        try:
            raw["date"] = pd.to_datetime(raw["date"], format="mixed", dayfirst=False)
        except (ValueError, TypeError):
            raw["date"] = pd.to_datetime(raw["date"], dayfirst=True)

        matches = [
            Match(
                match_id=int(row.match_id),
                round=str(row.round).strip(),
                home_slot=str(row.home_team).strip(),
                away_slot=str(row.away_team).strip(),
                stadium=str(row.stadium).strip(),
                date=row.date,
            )
            for row in raw.itertuples(index=False)
        ]
        return cls(matches)

    # ------------------------------------------------------------------
    # Slot resolution
    # ------------------------------------------------------------------

    @staticmethod
    def is_placeholder(slot: str) -> bool:
        """Return True if *slot* is a ``W<id>`` or ``L<id>`` reference."""
        return bool(_PLACEHOLDER_RE.fullmatch(slot))

    def resolve_slot(self, slot: str) -> str:
        """Return the team name for *slot*.

        For ``W<id>`` / ``L<id>`` references, looks up the recorded result.
        For literal strings, returns the string unchanged.

        Raises:
            KeyError: if the referenced match result has not been recorded yet.
        """
        if slot.startswith("W") and slot[1:].isdigit():
            mid = int(slot[1:])
            if mid not in self._winners:
                raise KeyError(
                    f"Winner of match {mid} not yet recorded. "
                    "Simulate that match before resolving this slot."
                )
            return self._winners[mid]
        if slot.startswith("L") and slot[1:].isdigit():
            mid = int(slot[1:])
            if mid not in self._losers:
                raise KeyError(
                    f"Loser of match {mid} not yet recorded. "
                    "Simulate that match before resolving this slot."
                )
            return self._losers[mid]
        return slot

    def record_result(self, match_id: int, winner: str, loser: str) -> None:
        """Record the outcome of a match so future slots can be resolved."""
        self._winners[match_id] = winner
        self._losers[match_id] = loser

    # ------------------------------------------------------------------
    # Bracket navigation
    # ------------------------------------------------------------------

    def rounds_ordered(self) -> list[tuple[str, list[Match]]]:
        """Return ``(round_name, [Match, ...])`` pairs in bracket order.

        Rounds are sorted by position (Round of 32 → … → Final).
        Within a round, matches are sorted ascending by match_id.
        Unknown round names are placed after all known rounds.
        """
        round_key: dict[str, int] = {}
        for m in self._matches.values():
            if m.round not in round_key:
                round_key[m.round] = _ROUND_ORDER.get(m.round, 99)

        grouped: dict[str, list[Match]] = {}
        for m in self._matches.values():
            grouped.setdefault(m.round, []).append(m)

        result: list[tuple[str, list[Match]]] = []
        for rname in sorted(grouped, key=lambda r: round_key.get(r, 99)):
            result.append((rname, sorted(grouped[rname], key=lambda m: m.match_id)))
        return result

    def first_round_matches(self) -> list[Match]:
        """Return matches in the opening round (the round with no W/L slots)."""
        rounds = self.rounds_ordered()
        return rounds[0][1] if rounds else []

    def all_initial_teams(self) -> list[str]:
        """Return all literal team names from the opening round, in order.

        Teams are collected left-to-right (home before away) across matches
        sorted by match_id.  Duplicates are removed while preserving order.
        """
        seen: set[str] = set()
        teams: list[str] = []
        for match in self.first_round_matches():
            for slot in (match.home_slot, match.away_slot):
                if not self.is_placeholder(slot) and slot not in seen:
                    seen.add(slot)
                    teams.append(slot)
        return teams

    def detect_frontier_round(self) -> str:
        """Return the deepest round whose matches are all fully known.

        Walks FRONTIER_ROUNDS in bracket order. A round only counts once
        every one of its own matches has literal (non-placeholder) home_slot
        and away_slot values. Stops at the first round that is present but
        still has any placeholder slot. A round that isn't present in the
        fixtures at all is skipped rather than treated as a stop condition
        -- once a round fully resolves in real life, its rows may be removed
        from fixtures.csv entirely (nothing references W#/L# into it
        anymore), and that must not be mistaken for "unresolved."
        Defaults to "Round of 32" if even that round isn't fully resolved
        yet (shouldn't happen in practice -- Round of 32 participants are a
        given input, not something this project simulates).
        """
        grouped = dict(self.rounds_ordered())
        frontier = FRONTIER_ROUNDS[0]
        for round_name in FRONTIER_ROUNDS:
            matches = grouped.get(round_name)
            if not matches:
                continue
            fully_resolved = all(
                not self.is_placeholder(m.home_slot) and not self.is_placeholder(m.away_slot)
                for m in matches
            )
            if fully_resolved:
                frontier = round_name
            else:
                break
        return frontier

    # ------------------------------------------------------------------
    # Neutral venue logic
    # ------------------------------------------------------------------

    def is_neutral(self, match: Match, home_team: str, away_team: str) -> bool:
        """Return True if *match* is played at a neutral venue.

        A match is **not** neutral when a 2026 host nation (USA, Canada,
        Mexico) is playing at a venue in their own country.  All other
        matches are neutral — even host-country venues are neutral for
        non-host teams.
        """
        venue_country = STADIUM_COUNTRY.get(match.stadium, "__neutral__")
        if venue_country == "__neutral__":
            return True
        if venue_country in (home_team, away_team):
            return False
        return True

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def get_match(self, match_id: int) -> Match:
        """Return the Match with ``match_id``."""
        return self._matches[match_id]

    def winner_of(self, match_id: int) -> str | None:
        """Return the recorded winner of ``match_id``, or None if unresolved."""
        return self._winners.get(match_id)

    def loser_of(self, match_id: int) -> str | None:
        """Return the recorded loser of ``match_id``, or None if unresolved."""
        return self._losers.get(match_id)

    def __len__(self) -> int:
        return len(self._matches)
