"""
test_features.py — Schema and no-lookahead tests for the processed feature table.

Requires data/processed/features.parquet to exist (built by
``python -m src.predictor.model --features-only`` or ``python -m src.predictor.model``).

Tests cover:
  - Required columns are present with correct dtypes.
  - Outcome target encodes exactly {0, 1, 2}.
  - No feature for match date d was computed using any row with date >= d
    (the no-lookahead / no-data-leakage invariant).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import FEATURES_PATH
from src.predictor.model import FEATURE_COLS, TARGET_COL

# Skip the entire module if the feature file hasn't been built yet.
pytestmark = pytest.mark.skipif(
    not FEATURES_PATH.exists(),
    reason=(
        "features.parquet not found — run "
        "`python -m src.predictor.model --features-only` first."
    ),
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    df = pd.read_parquet(FEATURES_PATH)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ---------------------------------------------------------------------------
# Schema checks
# ---------------------------------------------------------------------------


def test_required_columns_present(features):
    missing = [c for c in FEATURE_COLS + [TARGET_COL] if c not in features.columns]
    assert not missing, f"Missing columns: {missing}"


def test_date_column_present(features):
    assert "date" in features.columns


def test_team_columns_present(features):
    assert "home_team" in features.columns
    assert "away_team" in features.columns


def test_no_zero_rows(features):
    assert len(features) > 0, "Feature table is empty"


def test_date_dtype(features):
    assert pd.api.types.is_datetime64_any_dtype(features["date"])


# ---------------------------------------------------------------------------
# Target variable
# ---------------------------------------------------------------------------


def test_target_encodes_three_classes(features):
    classes = set(features[TARGET_COL].unique())
    assert classes == {0, 1, 2}, (
        f"Expected target classes {{0, 1, 2}}, got {classes}"
    )


def test_target_no_nulls(features):
    assert features[TARGET_COL].notna().all()


def test_target_class_distribution_plausible(features):
    counts = features[TARGET_COL].value_counts(normalize=True)
    # Home wins (0) typically 40–50 %, draws (1) 20–30 %, away wins (2) 25–35 %
    for cls, (lo, hi) in ((0, (0.30, 0.60)), (1, (0.15, 0.40)), (2, (0.20, 0.50))):
        pct = counts.get(cls, 0.0)
        assert lo <= pct <= hi, (
            f"Class {cls} makes up {pct:.1%} of targets — outside expected range "
            f"[{lo:.0%}, {hi:.0%}]"
        )


# ---------------------------------------------------------------------------
# Feature value sanity
# ---------------------------------------------------------------------------


def test_elo_gap_finite(features):
    assert np.isfinite(features["elo_gap"].dropna()).all()


def test_is_neutral_binary(features):
    vals = features["is_neutral"].dropna().unique()
    assert set(vals).issubset({0, 1, 0.0, 1.0})


def test_match_importance_between_0_and_1(features):
    col = features["match_importance"].dropna()
    assert (col >= 0).all() and (col <= 1).all()


def test_win_rates_between_0_and_1(features):
    for col in ["home_win_rate_5", "home_win_rate_10",
                "away_win_rate_5", "away_win_rate_10"]:
        vals = features[col].dropna()
        assert (vals >= 0).all() and (vals <= 1).all(), (
            f"Column {col} has values outside [0, 1]"
        )


# ---------------------------------------------------------------------------
# No-lookahead invariant
# ---------------------------------------------------------------------------


def test_no_lookahead_elo_features(features):
    """home_elo and away_elo for row i must only use data from before row i's date.

    Specifically: the elo_history file records Elo *after* each match, so
    the Elo value attached to a match row should be the rating *before* that
    match — i.e., the last rating update that has date < match.date.

    We verify this at the dataset level: after sorting by date, the elo_gap
    series should not be the same as what we'd get by using *future* ratings.
    The direct leakage check is: no match's home_elo value should equal the
    elo_history entry *for the same match date* (which would mean it used
    the post-match rating).
    """
    from src.config import ELO_HISTORY_PATH

    if not ELO_HISTORY_PATH.exists():
        pytest.skip("elo_history.csv not found — run build_dataset.py first")

    elo_hist = pd.read_csv(ELO_HISTORY_PATH, parse_dates=["date"])

    # For each match row, check that home_elo != the Elo rating *on* the match date.
    # (Using the post-match rating would be a lookahead leak.)
    leaks = 0
    sample = features.sample(min(500, len(features)), random_state=0)

    for _, row in sample.iterrows():
        team = row["home_team"]
        date = row["date"]
        home_elo_in_features = row["home_elo"]

        # Elo history entry ON the match date (post-match rating)
        same_day = elo_hist[
            (elo_hist["team"] == team) & (elo_hist["date"] == date)
        ]
        if same_day.empty:
            continue  # No history entry for this date — can't check

        post_match_elo = float(same_day.iloc[-1]["elo"])

        # If the feature uses the post-match Elo, it should not equal the pre-match.
        # The pre-match Elo should be the one *before* this match.
        # We flag it as a leak if the feature value exactly equals the post-match value
        # AND the team actually played that day (i.e. the Elo changed).
        before_day = elo_hist[
            (elo_hist["team"] == team) & (elo_hist["date"] < date)
        ]
        if before_day.empty:
            continue

        pre_match_elo = float(before_day.iloc[-1]["elo"])

        # If Elo changed on this date and features used the post-match value → leak
        if pre_match_elo != post_match_elo:
            if abs(home_elo_in_features - post_match_elo) < 1e-6:
                leaks += 1

    assert leaks == 0, (
        f"Found {leaks} rows where home_elo appears to use the post-match Elo "
        "(lookahead leak).  Check compute_elo() in elo.py — pre-match ratings "
        "must be stored before updating."
    )
