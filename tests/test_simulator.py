"""
test_simulator.py — Unit tests for the Monte Carlo simulator engine.

Uses a MockPredictor and mocked resolve_shootout so no trained model or
downloaded data is required.  Tests cover:

  - Per-team outcome-bucket percentages sum to exactly 100 %.
  - Total teams eliminated per round match the bracket structure.
  - Simulation is reproducible with the same seed.
  - Different seeds produce different results.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import TEST_BRACKET_PATH
from src.simulator.montecarlo import MonteCarloSimulator

# ---------------------------------------------------------------------------
# Mock predictor — returns fixed balanced probabilities so tests are fast
# and deterministic without needing real model files.
# ---------------------------------------------------------------------------

BUCKET_COLS = MonteCarloSimulator.BUCKET_NAMES
_MOCK_PROBA = (0.40, 0.20, 0.40)   # (p_home_win, p_draw, p_away_win)
_MOCK_SO    = (0.50, 0.50)          # (p_home_wins_shootout, p_away_wins_shootout)

_N_SMALL = 20_000   # fast enough for unit tests; large enough for reliable stats


class MockPredictor:
    def predict_proba(
        self,
        home_team: str,
        away_team: str,
        asof: pd.Timestamp,
        is_neutral: bool = True,
        tournament: str = "",
    ) -> tuple[float, float, float]:
        return _MOCK_PROBA

    def _elo_asof(self, team: str, asof: pd.Timestamp) -> float:
        return 1500.0


@pytest.fixture
def results_df():
    """Run the simulator on the 8-team test bracket with mock predictor."""
    with patch("src.simulator.montecarlo.resolve_shootout", return_value=_MOCK_SO):
        sim = MonteCarloSimulator(n_sims=_N_SMALL, seed=42)
        return sim.run(TEST_BRACKET_PATH, MockPredictor())


# ---------------------------------------------------------------------------
# Core invariant: each team's buckets sum to exactly 100 %
# ---------------------------------------------------------------------------


def test_bucket_percentages_sum_to_100(results_df):
    for _, row in results_df.iterrows():
        total = sum(float(row[b]) for b in BUCKET_COLS)
        assert abs(total - 100.0) < 1e-6, (
            f"Team '{row['team']}' buckets sum to {total:.8f} (expected 100.0)"
        )


# ---------------------------------------------------------------------------
# Round-level total checks
# ---------------------------------------------------------------------------


def test_qf_elimination_total(results_df):
    # 4 of 8 teams eliminated in QF per sim → total exit_qf% across all teams = 400
    total = float(results_df["exit_qf"].sum())
    assert abs(total - 400.0) < 2.0, f"Total exit_qf = {total} (expected ~400)"


def test_sf_and_third_place_total(results_df):
    # Both SF losers go to 3rd-place match: one exit_sf, one third_place
    # → combined total = 200 % across all teams
    total = float(results_df["exit_sf"].sum() + results_df["third_place"].sum())
    assert abs(total - 200.0) < 2.0, f"SF exit + 3rd place = {total} (expected ~200)"


def test_final_total(results_df):
    # 1 champion, 1 runner-up per sim → combined total = 200 %
    total = float(results_df["runner_up"].sum() + results_df["champion"].sum())
    assert abs(total - 200.0) < 2.0, f"Runner-up + champion = {total} (expected ~200)"


def test_no_r32_or_r16_exits_for_qf_bracket(results_df):
    # Test bracket starts at QF — no team should have R32 or R16 exits
    assert float(results_df["exit_r32"].sum()) == 0.0
    assert float(results_df["exit_r16"].sum()) == 0.0


# ---------------------------------------------------------------------------
# All 8 teams are represented
# ---------------------------------------------------------------------------


def test_all_initial_teams_in_results(results_df):
    expected = {"Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta"}
    actual = set(results_df["team"])
    assert actual == expected


def test_results_has_correct_shape(results_df):
    assert len(results_df) == 8
    assert set(BUCKET_COLS).issubset(results_df.columns)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_same_seed_reproducible():
    with patch("src.simulator.montecarlo.resolve_shootout", return_value=_MOCK_SO):
        df1 = MonteCarloSimulator(n_sims=_N_SMALL, seed=42).run(
            TEST_BRACKET_PATH, MockPredictor()
        )
        df2 = MonteCarloSimulator(n_sims=_N_SMALL, seed=42).run(
            TEST_BRACKET_PATH, MockPredictor()
        )

    df1 = df1.sort_values("team").reset_index(drop=True)
    df2 = df2.sort_values("team").reset_index(drop=True)

    for col in BUCKET_COLS:
        assert (df1[col] == df2[col]).all(), f"Column '{col}' differs between runs"


def test_different_seeds_differ():
    with patch("src.simulator.montecarlo.resolve_shootout", return_value=_MOCK_SO):
        df1 = MonteCarloSimulator(n_sims=_N_SMALL, seed=1).run(
            TEST_BRACKET_PATH, MockPredictor()
        )
        df2 = MonteCarloSimulator(n_sims=_N_SMALL, seed=99).run(
            TEST_BRACKET_PATH, MockPredictor()
        )

    # Champion percentages for at least one team must differ
    assert not (df1["champion"].values == df2["champion"].values).all()


# ---------------------------------------------------------------------------
# Non-zero champion probability for all teams (with balanced mock probs)
# ---------------------------------------------------------------------------


def test_all_teams_have_nonzero_champion_chance(results_df):
    # With balanced (40/20/40) probs and 20k sims, every team should have won
    # at least once.
    assert (results_df["champion"] > 0.0).all(), (
        "Some teams have 0% champion probability; "
        "check simulation bucket assignment logic."
    )
