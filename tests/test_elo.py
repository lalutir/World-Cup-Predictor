"""
test_elo.py — Unit tests for the Elo rating computation in src/features/elo.py.

Tests cover:
  - k_base(): correct tier assignments and ordering.
  - g_factor(): formula values and monotonicity.
  - compute_elo(): zero-sum updates, monotonic win probability in rating gap,
    and the no-lookahead invariant (home_elo_before never uses post-match data).
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.features.elo import k_base, g_factor, compute_elo


# ---------------------------------------------------------------------------
# Minimal DataFrame factory for compute_elo tests
# ---------------------------------------------------------------------------


def _make_match(
    home: str,
    away: str,
    h_score: int,
    a_score: int,
    tournament: str = "Friendly",
    neutral: bool = True,
    date: str = "2000-01-01",
) -> dict:
    return {
        "date":        pd.Timestamp(date),
        "home_team":   home,
        "away_team":   away,
        "home_score":  h_score,
        "away_score":  a_score,
        "tournament":  tournament,
        "neutral":     neutral,
    }


def _df(*matches) -> pd.DataFrame:
    return pd.DataFrame(list(matches))


# ---------------------------------------------------------------------------
# k_base — tournament tier lookup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tournament,expected_k", [
    ("FIFA World Cup",                        60),
    ("UEFA Euro",                             60),   # "UEFA Euro" contains "uefa euro"
    ("Copa América",                          60),
    ("FIFA World Cup qualification",          40),
    ("UEFA European Championship qualifying", 40),
    ("Friendly",                              20),
    ("CONIFA World Cup",                      30),
    ("Viva World Cup",                        30),
])
def test_k_base_known_tournaments(tournament, expected_k):
    assert k_base(tournament) == expected_k, (
        f"k_base('{tournament}') = {k_base(tournament)}, expected {expected_k}"
    )


def test_k_base_world_cup_higher_than_qualifier():
    assert k_base("FIFA World Cup") > k_base("FIFA World Cup qualification")


def test_k_base_qualifier_higher_than_friendly():
    assert k_base("FIFA World Cup qualification") > k_base("Friendly")


def test_k_base_unknown_returns_minor_tier():
    """Unknown tournaments should return the minor/default tier (30), not crash."""
    k = k_base("Some Obscure Cup 1897")
    assert k == 30


def test_k_base_returns_positive():
    for t in ("Friendly", "FIFA World Cup", "Copa América", "CONIFA World Cup"):
        assert k_base(t) > 0


# ---------------------------------------------------------------------------
# g_factor (goal-difference multiplier)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("diff,expected", [
    (0,  1.000),
    (1,  1.000),
    (2,  1.500),
    (3,  1.750),
    (4,  1.750 + 1 / 8),
    (5,  1.750 + 2 / 8),
    (8,  1.750 + 5 / 8),
    (10, 1.750 + 7 / 8),
])
def test_g_factor_formula(diff, expected):
    assert abs(g_factor(diff) - expected) < 1e-9


def test_g_factor_monotone():
    """G must be non-decreasing with goal difference."""
    prev = 0.0
    for diff in range(0, 12):
        g = g_factor(diff)
        assert g >= prev, f"g_factor({diff}) = {g} < g_factor({diff-1}) = {prev}"
        prev = g


def test_g_factor_plateau_at_zero_and_one():
    assert g_factor(0) == g_factor(1) == 1.0


# ---------------------------------------------------------------------------
# compute_elo — zero-sum updates
# ---------------------------------------------------------------------------


def test_zero_sum_home_win():
    """Sum of Elo changes after any match must be zero."""
    df = _df(_make_match("A", "B", 2, 1))
    _, hist = compute_elo(df.copy())

    a_final = float(hist.loc[hist["team"] == "A", "elo"].iloc[-1])
    b_final = float(hist.loc[hist["team"] == "B", "elo"].iloc[-1])
    # Both start at 1500; conservation requires their sum stays at 3000.
    assert abs((a_final + b_final) - 3000.0) < 1e-6


def test_zero_sum_draw():
    df = _df(_make_match("A", "B", 1, 1))
    _, hist = compute_elo(df.copy())
    a = float(hist.loc[hist["team"] == "A", "elo"].iloc[-1])
    b = float(hist.loc[hist["team"] == "B", "elo"].iloc[-1])
    assert abs((a + b) - 3000.0) < 1e-6


def test_zero_sum_away_win():
    df = _df(_make_match("A", "B", 0, 3))
    _, hist = compute_elo(df.copy())
    a = float(hist.loc[hist["team"] == "A", "elo"].iloc[-1])
    b = float(hist.loc[hist["team"] == "B", "elo"].iloc[-1])
    assert abs((a + b) - 3000.0) < 1e-6


def test_winner_gains_loser_loses():
    """After a decisive result, the winner's Elo rises and the loser's falls."""
    df = _df(_make_match("A", "B", 2, 0))
    _, hist = compute_elo(df.copy())
    a = float(hist.loc[hist["team"] == "A", "elo"].iloc[-1])
    b = float(hist.loc[hist["team"] == "B", "elo"].iloc[-1])
    assert a > 1500.0  # A (home, winner) gained
    assert b < 1500.0  # B (away, loser) lost


# ---------------------------------------------------------------------------
# compute_elo — monotonicity in rating gap
# ---------------------------------------------------------------------------


def _run_single_match_and_get_home_gain(
    home_elo_diff: float,
    h_score: int,
    a_score: int,
) -> float:
    """Return the Elo gained by the home team in a single match.

    home team starts at 1500 + home_elo_diff; away at 1500.
    Two sequential matches are used: one to set up the desired initial Elo,
    one to measure the response.
    """
    # First match: home wins big to boost home team's Elo to target level.
    # Use neutral=True and Friendly so K=20 gives a controllable delta.
    # Instead, just pre-populate Elo by running multiple warm-up matches.
    #
    # Simpler approach: craft a 2-match sequence where the first match sets
    # the starting Elos, then extract the second match's delta.
    # Here we use a direct approach: since both teams start at 1500 and we
    # want a specific gap, we manually construct a warm-up period.
    #
    # Actually, the cleanest approach is to chain enough matches to reach the
    # desired gap, but that's fragile. For the test we only need to verify
    # *relative* ordering, not absolute values, so we can compare two separate
    # single-match runs with different starting assumptions by using teams with
    # historical pre-seeded Elo from prior matches.
    pass  # placeholder — see direct test below


def test_higher_rated_team_favoured():
    """
    After inflating A's Elo via prior wins, A should gain less for a subsequent
    win (expected outcome) than B gains for the same win starting from equal Elo.
    This verifies that the expected_score factor correctly dampens gains for
    heavy favourites.
    """
    # Build A's Elo up through prior wins.
    warm_up = [
        _make_match("A", "X", 3, 0, date="1990-01-01"),
        _make_match("A", "Y", 3, 0, date="1990-01-02"),
        _make_match("A", "Z", 3, 0, date="1990-01-03"),
        _make_match("A", "W", 3, 0, date="1990-01-04"),
        _make_match("A", "V", 3, 0, date="1990-01-05"),
    ]
    # Test match: A (boosted) vs B (untouched, 1500) — A wins.
    test_match = _make_match("A", "B", 1, 0, date="2000-01-01")
    df = _df(*warm_up, test_match)

    enriched, hist = compute_elo(df.copy())

    # A's Elo just before the test match (after warm-up)
    a_before = float(
        enriched.loc[enriched.index[-1], "home_elo_before"]
    )
    # A's Elo after the test match
    a_after = float(hist.loc[
        (hist["team"] == "A") & (hist["date"] == pd.Timestamp("2000-01-01")),
        "elo"
    ].iloc[-1])

    gain_a_boosted = a_after - a_before

    # Equal-Elo baseline: A vs B starting fresh (no warm-up)
    df_equal = _df(_make_match("A", "B", 1, 0, date="2000-01-01"))
    _, hist_eq = compute_elo(df_equal.copy())
    a_eq_after = float(hist_eq.loc[hist_eq["team"] == "A", "elo"].iloc[-1])
    gain_equal = a_eq_after - 1500.0

    # The boosted (higher-rated) A should gain LESS for the same win.
    assert gain_a_boosted < gain_equal, (
        f"Boosted A gained {gain_a_boosted:.3f} but equal A gained {gain_equal:.3f}; "
        "expected fewer points for the higher-rated team."
    )


# ---------------------------------------------------------------------------
# compute_elo — no-lookahead invariant
# ---------------------------------------------------------------------------


def test_home_elo_before_is_pre_match():
    """home_elo_before must equal the rating *before* the match, not after."""
    df = _df(
        _make_match("A", "B", 2, 0, date="2000-01-01"),
        _make_match("A", "B", 1, 0, date="2001-01-01"),  # A again, with a higher Elo
    )
    enriched, hist = compute_elo(df.copy())

    # First match: both teams start at 1500, so home_elo_before should be 1500.
    first_row = enriched.iloc[0]
    assert abs(first_row["home_elo_before"] - 1500.0) < 1e-9

    # Second match: A's home_elo_before must equal A's Elo AFTER match 1,
    # which is stored in elo_history.
    a_after_match_1 = float(
        hist.loc[
            (hist["team"] == "A") & (hist["date"] == pd.Timestamp("2000-01-01")),
            "elo"
        ].iloc[-1]
    )
    second_row = enriched.iloc[1]
    assert abs(second_row["home_elo_before"] - a_after_match_1) < 1e-9


def test_k_scales_with_goal_difference():
    """A 3-goal win should produce a larger Elo shift than a 1-goal win."""
    df1 = _df(_make_match("A", "B", 2, 1))   # 1-goal win (G=1.0)
    df3 = _df(_make_match("A", "B", 4, 1))   # 3-goal win (G=1.75)

    _, hist1 = compute_elo(df1.copy())
    _, hist3 = compute_elo(df3.copy())

    gain1 = float(hist1.loc[hist1["team"] == "A", "elo"].iloc[-1]) - 1500.0
    gain3 = float(hist3.loc[hist3["team"] == "A", "elo"].iloc[-1]) - 1500.0

    assert gain3 > gain1, (
        f"3-goal win yielded {gain3:.3f} Elo gain, "
        f"but 1-goal win yielded {gain1:.3f} — expected more for larger margin"
    )
