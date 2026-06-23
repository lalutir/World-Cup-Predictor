"""
test_bracket.py — Unit tests for BracketResolver using the synthetic test bracket.

These tests are entirely self-contained: no trained model or downloaded data is
required.  The test_bracket.csv (8 teams, QF → SF → 3rd place + Final) is the
fixture.
"""

import pytest
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.bracket.bracket import BracketResolver, Match
from src.config import TEST_BRACKET_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver() -> BracketResolver:
    return BracketResolver.from_csv(TEST_BRACKET_PATH)


# ---------------------------------------------------------------------------
# Loading & structure
# ---------------------------------------------------------------------------


def test_loads_correct_number_of_matches(resolver):
    assert len(resolver) == 8


def test_initial_teams_in_order(resolver):
    teams = resolver.all_initial_teams()
    assert teams == ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta"]


def test_initial_teams_no_duplicates(resolver):
    teams = resolver.all_initial_teams()
    assert len(teams) == len(set(teams))


def test_rounds_ordered_names(resolver):
    round_names = [r for r, _ in resolver.rounds_ordered()]
    assert round_names == [
        "Quarter-finals",
        "Semi-finals",
        "Third place play-off",
        "Final",
    ]


def test_rounds_ordered_match_counts(resolver):
    rounds = dict(resolver.rounds_ordered())
    assert len(rounds["Quarter-finals"])       == 4
    assert len(rounds["Semi-finals"])          == 2
    assert len(rounds["Third place play-off"]) == 1
    assert len(rounds["Final"])                == 1


def test_first_round_is_qf(resolver):
    first = resolver.first_round_matches()
    assert all(m.round == "Quarter-finals" for m in first)


def test_match_ids_in_round_ascending(resolver):
    for _, matches in resolver.rounds_ordered():
        ids = [m.match_id for m in matches]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# is_placeholder detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slot,expected", [
    ("W1", True),
    ("L5", True),
    ("W10", True),
    ("L100", True),
    ("Alpha", False),
    ("Group A winners", False),
    ("W", False),      # no digits
    ("WW1", False),    # double letter
])
def test_is_placeholder(slot, expected):
    assert BracketResolver.is_placeholder(slot) == expected


# ---------------------------------------------------------------------------
# resolve_slot
# ---------------------------------------------------------------------------


def test_resolve_literal_slot(resolver):
    """Literal team name resolves immediately without any recorded results."""
    match_1 = resolver.get_match(1)
    assert resolver.resolve_slot(match_1.home_slot) == "Alpha"
    assert resolver.resolve_slot(match_1.away_slot) == "Beta"


def test_resolve_winner_slot_after_recording(resolver):
    resolver.record_result(1, "Alpha", "Beta")
    match_5 = resolver.get_match(5)
    assert resolver.resolve_slot(match_5.home_slot) == "Alpha"


def test_resolve_loser_slot_after_recording(resolver):
    resolver.record_result(5, "Alpha", "Gamma")  # Gamma lost SF match 5
    match_7 = resolver.get_match(7)
    # match 7 home_slot = L5
    assert resolver.resolve_slot(match_7.home_slot) == "Gamma"


def test_resolve_slot_raises_if_unrecorded(resolver):
    match_5 = resolver.get_match(5)
    with pytest.raises(KeyError, match="not yet recorded"):
        resolver.resolve_slot(match_5.home_slot)  # W1 not recorded yet


def test_resolve_chain(resolver):
    """Resolve a two-hop chain: QF → SF → Final."""
    resolver.record_result(1, "Alpha", "Beta")
    resolver.record_result(2, "Delta", "Gamma")
    resolver.record_result(3, "Epsilon", "Zeta")
    resolver.record_result(4, "Eta", "Theta")

    resolver.record_result(5, "Alpha", "Delta")
    resolver.record_result(6, "Epsilon", "Eta")

    match_8 = resolver.get_match(8)  # Final: W5 vs W6
    assert resolver.resolve_slot(match_8.home_slot) == "Alpha"
    assert resolver.resolve_slot(match_8.away_slot) == "Epsilon"


# ---------------------------------------------------------------------------
# winner_of / loser_of accessors
# ---------------------------------------------------------------------------


def test_winner_and_loser_accessors(resolver):
    assert resolver.winner_of(1) is None   # nothing recorded yet
    resolver.record_result(1, "Alpha", "Beta")
    assert resolver.winner_of(1) == "Alpha"
    assert resolver.loser_of(1)  == "Beta"


# ---------------------------------------------------------------------------
# is_neutral
# ---------------------------------------------------------------------------


def test_test_bracket_always_neutral(resolver):
    """Test Stadium maps to __neutral__ so every match is neutral."""
    match_1 = resolver.get_match(1)
    assert resolver.is_neutral(match_1, "Alpha", "Beta") is True


def test_not_neutral_when_host_plays_at_home():
    """United States playing at a US venue should NOT be neutral."""
    from src.bracket.bracket import Match
    import pandas as pd

    match = Match(
        match_id=99,
        round="Final",
        home_slot="United States",
        away_slot="France",
        stadium="Dallas Stadium",
        date=pd.Timestamp("2026-07-14"),
    )
    resolver = BracketResolver([match])
    assert resolver.is_neutral(match, "United States", "France") is False


def test_neutral_when_non_host_plays_at_us_venue():
    from src.bracket.bracket import Match
    import pandas as pd

    match = Match(
        match_id=99,
        round="Final",
        home_slot="Germany",
        away_slot="France",
        stadium="Dallas Stadium",
        date=pd.Timestamp("2026-07-14"),
    )
    resolver = BracketResolver([match])
    assert resolver.is_neutral(match, "Germany", "France") is True
