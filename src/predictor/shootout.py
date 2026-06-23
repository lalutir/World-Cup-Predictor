"""
shootout.py — Penalty shootout probability estimator.

Fits a logistic regression on historical shootout outcomes as a function of
the Elo gap between the two teams at the time of the shootout.

Shootouts are notoriously close to coin flips, so the model applies strong L2
regularisation (C=0.01) to keep the slope very flat rather than over-fitting
to the historical Elo gap signal.

Usage
-----
    # Train (once, after build_dataset.py has run):
    python -m src.predictor.shootout

    # Inference:
    from src.predictor.shootout import resolve_shootout
    p_home, p_away = resolve_shootout("Argentina", "France", home_elo=1920, away_elo=1900)
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import MATCHES_PROCESSED, PROCESSED_DIR, SHOOTOUT_MODEL_PATH


def train_shootout(
    matches_path: Path = MATCHES_PROCESSED,
    model_path: Path = SHOOTOUT_MODEL_PATH,
) -> None:
    """Fit and save a shootout probability model from historical data.

    Joins the `shootout_winner` column in matches.csv with the pre-match Elo
    ratings already computed by build_dataset.py, then fits a logistic
    regression P(home wins | elo_gap).

    The intercept captures the baseline (near-50%) home win rate in shootouts;
    the coefficient on elo_gap is expected to be small and positive — better
    teams win more shootouts, but only slightly more than chance.

    Args:
        matches_path: Path to data/processed/matches.csv.
        model_path:   Where to write the trained model (joblib format).
    """
    matches = pd.read_csv(matches_path, parse_dates=["date"])

    shootout_rows = matches.dropna(subset=["shootout_winner"]).copy()
    if len(shootout_rows) == 0:
        raise ValueError(
            "No shootout rows found in matches.csv. "
            "Make sure build_dataset.py has been run with a shootouts.csv that "
            "contains the 'winner' column."
        )

    shootout_rows["elo_gap"] = (
        shootout_rows["home_elo_before"] - shootout_rows["away_elo_before"]
    )
    shootout_rows["home_won"] = (
        shootout_rows["shootout_winner"] == shootout_rows["home_team"]
    ).astype(int)

    X = shootout_rows[["elo_gap"]].values
    y = shootout_rows["home_won"].values

    model = LogisticRegression(
        C=0.01,         # strong regularisation → near-coin-flip predictions
        max_iter=500,
        random_state=42,
    )
    model.fit(X, y)

    baseline = float(y.mean())
    coef = float(model.coef_[0, 0])
    print(f"  Shootout model trained on {len(y):,} historical shootouts")
    print(f"  Baseline home win rate : {baseline:.3f}")
    print(f"  Elo-gap coefficient    : {coef:.6f}  (expected ~0, near coin-flip)")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    print(f"  Saved → {model_path.relative_to(_REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

_model: LogisticRegression | None = None


def _load_model() -> LogisticRegression:
    global _model
    if _model is None:
        if not SHOOTOUT_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"No shootout model at {SHOOTOUT_MODEL_PATH}. "
                "Run `python -m src.predictor.shootout` first."
            )
        _model = joblib.load(SHOOTOUT_MODEL_PATH)
    return _model


def resolve_shootout(
    home_team: str,
    away_team: str,
    home_elo: float,
    away_elo: float,
) -> tuple[float, float]:
    """Return (p_home_wins_shootout, p_away_wins_shootout).

    Probabilities are derived from the logistic model calibrated on historical
    penalty data.  The slope is intentionally flat — Elo gap matters far less
    in shootouts than in open play.

    Args:
        home_team: Name of the home/first team (informational only).
        away_team: Name of the away/second team (informational only).
        home_elo:  Elo rating of the home team at match time.
        away_elo:  Elo rating of the away team at match time.

    Returns:
        (p_home, p_away) guaranteed to sum to exactly 1.0.
    """
    model = _load_model()
    elo_gap = np.array([[home_elo - away_elo]], dtype=float)
    p_home = float(model.predict_proba(elo_gap)[0, 1])
    return p_home, 1.0 - p_home


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Training shootout probability model")
    print("=" * 60)
    train_shootout()
