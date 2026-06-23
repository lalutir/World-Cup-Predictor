"""
model.py — Multinomial match outcome predictor.

Exposes two public interfaces:
  train()                                           → build features + train + save model
  predict_proba(home, away, asof, ...)              → (p_home_win, p_draw, p_away_win)

Training pipeline
-----------------
1. Load data/processed/matches.csv
2. Compute form, H2H, and context features via feature modules
3. Derive elo_gap and log-scale economic ratio features
4. Chronological split: train < 2022-01-01, validate 2022–2024, hold-out 2025+
5. Fit LogisticRegression (multinomial) and HistGradientBoostingClassifier
6. Keep whichever has lower log-loss on the 2022–2024 validation set
7. Save artifact (model + feature column list + metrics) to data/processed/model.pkl

Inference
---------
predict_proba() lazy-loads a module-level Predictor singleton on first call.
The Predictor pre-processes historical match data into fast per-team lookups so
that feature computation at inference time is O(N) in history depth, not O(M×N).

Entry points
------------
    python -m src.predictor.model                    # build features + train + save
    python -m src.predictor.model --features-only    # build features.parquet only
    python -m src.predictor.model --eval             # print validation metrics then exit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import (
    ELO_HISTORY_PATH,
    ELO_SEED_RATING,
    FEATURES_PATH,
    FORM_WINDOWS,
    H2H_HALF_LIFE_YEARS,
    MATCHES_PROCESSED,
    MODEL_PATH,
    PROCESSED_DIR,
)
from src.features.context import compute_context
from src.features.elo import k_base as _k_base
from src.features.form import compute_form
from src.features.h2h import compute_h2h

# ---------------------------------------------------------------------------
# Feature specification
# ---------------------------------------------------------------------------

FEATURE_COLS: list[str] = [
    "elo_gap",
    "home_elo",
    "away_elo",
    "log_gdp_ratio",
    "log_pop_ratio",
    "log_gdp_per_capita_ratio",
    "home_win_rate_5",
    "home_win_rate_10",
    "away_win_rate_5",
    "away_win_rate_10",
    "home_goal_diff_5",
    "home_goal_diff_10",
    "away_goal_diff_5",
    "away_goal_diff_10",
    "home_rest_days",
    "away_rest_days",
    "h2h_home_win_rate",
    "h2h_total_weight",
    "is_neutral",
    "match_importance",
]

TARGET_COL: str = "outcome"  # 0 = home win, 1 = draw, 2 = away win

TRAIN_CUTOFF = pd.Timestamp("2022-01-01")
VAL_CUTOFF = pd.Timestamp("2025-01-01")


# ---------------------------------------------------------------------------
# Feature construction helpers
# ---------------------------------------------------------------------------


def _add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute elo_gap, log economic ratios, and the outcome target column."""
    df = df.copy()

    df["elo_gap"] = df["home_elo_before"] - df["away_elo_before"]
    df["home_elo"] = df["home_elo_before"]
    df["away_elo"] = df["away_elo_before"]

    with np.errstate(divide="ignore", invalid="ignore"):
        df["log_gdp_ratio"] = np.log(df["home_gdp"] / df["away_gdp"])
        df["log_pop_ratio"] = np.log(df["home_population"] / df["away_population"])
        df["log_gdp_per_capita_ratio"] = np.log(
            df["home_gdp_per_capita"] / df["away_gdp_per_capita"]
        )

    for col in ("log_gdp_ratio", "log_pop_ratio", "log_gdp_per_capita_ratio"):
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)

    df[TARGET_COL] = np.where(
        df["home_score"] > df["away_score"],
        0,
        np.where(df["home_score"] == df["away_score"], 1, 2),
    )
    return df


def build_features(
    matches_path: Path = MATCHES_PROCESSED,
    output_path: Path = FEATURES_PATH,
) -> pd.DataFrame:
    """Compute the full feature table from matches.csv and write features.parquet.

    This is the single pipeline step that goes from the raw processed dataset to a
    model-ready feature table.  Running it again overwrites the previous output.

    Returns:
        Feature DataFrame (same data that was written to *output_path*).
    """
    print("  Loading matches.csv …")
    matches = pd.read_csv(matches_path, parse_dates=["date"])

    print("  Computing form features …")
    matches = compute_form(matches)

    print("  Computing H2H features …")
    matches = compute_h2h(matches)

    print("  Computing context features …")
    matches = compute_context(matches)

    print("  Deriving ratio features and outcome target …")
    matches = _add_derived_features(matches)

    keep_cols = ["date", "home_team", "away_team"] + FEATURE_COLS + [TARGET_COL]
    features = matches[[c for c in keep_cols if c in matches.columns]]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    features.to_parquet(output_path, index=False)
    print(f"  Written {len(features):,} rows → {output_path.relative_to(_REPO_ROOT)}")
    return features


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------


def train(
    features_path: Path = FEATURES_PATH,
    model_path: Path = MODEL_PATH,
    rebuild_features: bool = False,
) -> dict[str, Any]:
    """Train and save the best match outcome model.

    Compares LogisticRegression and HistGradientBoostingClassifier on the
    2022–2024 validation set and keeps the one with lower log-loss.

    Args:
        features_path:    Path to features.parquet; built if missing or rebuild_features.
        model_path:       Where to write the trained model (joblib format).
        rebuild_features: Force recomputation of features.parquet even if it exists.

    Returns:
        Dict with keys: winner, lr_val_loss, hgbt_val_loss.
    """
    if rebuild_features or not features_path.exists():
        print("[1/3] Building features …")
        build_features(output_path=features_path)
    else:
        print(f"[1/3] Using pre-built features ({features_path.name})")

    df = pd.read_parquet(features_path)
    df["date"] = pd.to_datetime(df["date"])

    train_mask = df["date"] < TRAIN_CUTOFF
    val_mask = (df["date"] >= TRAIN_CUTOFF) & (df["date"] < VAL_CUTOFF)

    X_train = df.loc[train_mask, FEATURE_COLS]
    y_train = df.loc[train_mask, TARGET_COL].astype(int)
    X_val = df.loc[val_mask, FEATURE_COLS]
    y_val = df.loc[val_mask, TARGET_COL].astype(int)

    print(
        f"[2/3] Training on {len(X_train):,} matches "
        f"(val: {len(X_val):,}) …"
    )

    lr = Pipeline([
        ("imputer", SimpleImputer(strategy="mean")),
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            max_iter=1000,
            C=0.1,
            random_state=42,
        )),
    ])

    hgbt = HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.05,
        max_depth=4,
        random_state=42,
    )

    lr.fit(X_train, y_train)
    hgbt.fit(X_train, y_train)

    lr_loss = log_loss(y_val, lr.predict_proba(X_val))
    hgbt_loss = log_loss(y_val, hgbt.predict_proba(X_val))

    print(f"  LogisticRegression  val log-loss: {lr_loss:.4f}")
    print(f"  HGBT                val log-loss: {hgbt_loss:.4f}")

    best_model = lr if lr_loss <= hgbt_loss else hgbt
    winner = "LogisticRegression" if lr_loss <= hgbt_loss else "HGBT"
    print(f"  Winner: {winner}")

    artifact: dict[str, Any] = {
        "model": best_model,
        "feature_cols": FEATURE_COLS,
        "lr_val_loss": lr_loss,
        "hgbt_val_loss": hgbt_loss,
        "winner": winner,
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, model_path)
    print(f"[3/3] Model saved → {model_path.relative_to(_REPO_ROOT)}")
    return artifact


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


class Predictor:
    """Loads the trained model and historical data; computes match probabilities.

    On construction, pre-processes all historical matches into per-team and
    per-pair lookup structures so that feature computation for a new matchup
    at a given date is fast.
    """

    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        matches_path: Path = MATCHES_PROCESSED,
        elo_history_path: Path = ELO_HISTORY_PATH,
    ) -> None:
        if not model_path.exists():
            raise FileNotFoundError(
                f"No trained model found at {model_path}. "
                "Run `python -m src.predictor.model` first."
            )
        artifact = joblib.load(model_path)
        self._model = artifact["model"]
        self._feature_cols: list[str] = artifact["feature_cols"]

        matches = pd.read_csv(matches_path, parse_dates=["date"])
        self._elo_history = pd.read_csv(elo_history_path, parse_dates=["date"])
        self._build_lookups(matches)

    def _build_lookups(self, matches: pd.DataFrame) -> None:
        """Precompute per-team and per-pair data structures for O(1) inference."""
        team_log: dict[str, list] = {}
        h2h_log: dict[tuple[str, str], list] = {}
        team_econ: dict[str, dict] = {}

        has_econ = "home_gdp" in matches.columns

        for row in matches.sort_values("date").itertuples(index=False):
            home: str = row.home_team
            away: str = row.away_team
            date: pd.Timestamp = row.date
            h_score: int = row.home_score
            a_score: int = row.away_score

            h_win = int(h_score > a_score)
            a_win = int(a_score > h_score)
            h_gd = h_score - a_score
            a_gd = a_score - h_score

            team_log.setdefault(home, []).append((date, h_win, h_gd))
            team_log.setdefault(away, []).append((date, a_win, a_gd))

            key: tuple[str, str] = (min(home, away), max(home, away))
            if key[0] == home:
                outcome = 1.0 if h_win else (0.5 if h_gd == 0 else 0.0)
            else:
                outcome = 1.0 if a_win else (0.5 if a_gd == 0 else 0.0)
            h2h_log.setdefault(key, []).append((date, outcome))

            if has_econ:
                year = date.year
                for team, gdp, pop, gdp_pc in (
                    (home, getattr(row, "home_gdp", np.nan),
                     getattr(row, "home_population", np.nan),
                     getattr(row, "home_gdp_per_capita", np.nan)),
                    (away, getattr(row, "away_gdp", np.nan),
                     getattr(row, "away_population", np.nan),
                     getattr(row, "away_gdp_per_capita", np.nan)),
                ):
                    curr = team_econ.get(team)
                    if curr is None or year > curr["year"]:
                        team_econ[team] = {
                            "year": year,
                            "gdp": gdp,
                            "population": pop,
                            "gdp_per_capita": gdp_pc,
                        }

        self._team_log = team_log
        self._h2h_log = h2h_log
        self._team_econ = team_econ

    # ------------------------------------------------------------------
    # Per-feature helpers
    # ------------------------------------------------------------------

    def _elo_asof(self, team: str, asof: pd.Timestamp) -> float:
        mask = (self._elo_history["team"] == team) & (self._elo_history["date"] < asof)
        sub = self._elo_history.loc[mask]
        return float(sub.iloc[-1]["elo"]) if not sub.empty else ELO_SEED_RATING

    def _form_asof(
        self, team: str, asof: pd.Timestamp, w: int
    ) -> tuple[float, float]:
        log = self._team_log.get(team, [])
        recent = [r for r in log if r[0] < asof][-w:]
        if not recent:
            return np.nan, np.nan
        return (
            float(np.mean([r[1] for r in recent])),
            float(np.mean([r[2] for r in recent])),
        )

    def _rest_asof(self, team: str, asof: pd.Timestamp) -> float:
        log = self._team_log.get(team, [])
        prior = [r for r in log if r[0] < asof]
        if not prior:
            return np.nan
        return float((asof - prior[-1][0]).days)

    def _h2h_asof(
        self, home: str, away: str, asof: pd.Timestamp
    ) -> tuple[float, float]:
        key = (min(home, away), max(home, away))
        history = [(d, r) for d, r in self._h2h_log.get(key, []) if d < asof]
        if not history:
            return np.nan, 0.0

        first_is_home = key[0] == home
        weighted_sum = 0.0
        weight_total = 0.0
        for hist_date, result_for_first in history:
            years_ago = (asof - hist_date).days / 365.25
            w = 0.5 ** (years_ago / H2H_HALF_LIFE_YEARS)
            result_for_home = result_for_first if first_is_home else (1.0 - result_for_first)
            weighted_sum += w * result_for_home
            weight_total += w

        return (weighted_sum / weight_total if weight_total > 0 else 0.5), weight_total

    def _econ(self, team: str) -> dict:
        return self._team_econ.get(
            team,
            {"gdp": np.nan, "population": np.nan, "gdp_per_capita": np.nan},
        )

    # ------------------------------------------------------------------
    # Public prediction interface
    # ------------------------------------------------------------------

    def predict_proba(
        self,
        home_team: str,
        away_team: str,
        asof: pd.Timestamp,
        is_neutral: bool = True,
        tournament: str = "FIFA World Cup",
    ) -> tuple[float, float, float]:
        """Return (p_home_win, p_draw, p_away_win) for a matchup.

        Args:
            home_team:  Name of the team occupying the home/first slot.
            away_team:  Name of the team occupying the away/second slot.
            asof:       Match date used as the cutoff for historical lookups.
            is_neutral: True when the match is at a neutral venue.
            tournament: Tournament name for deriving match_importance tier.

        Returns:
            Three floats summing to 1.0: (p_home_win, p_draw, p_away_win).
        """
        home_elo = self._elo_asof(home_team, asof)
        away_elo = self._elo_asof(away_team, asof)
        h2h_rate, h2h_weight = self._h2h_asof(home_team, away_team, asof)
        home_econ = self._econ(home_team)
        away_econ = self._econ(away_team)

        def _safe_log_ratio(a: float, b: float) -> float:
            if pd.isna(a) or pd.isna(b) or b == 0 or a <= 0:
                return np.nan
            return float(np.log(a / b))

        feature_vals: dict[str, float] = {
            "elo_gap": home_elo - away_elo,
            "home_elo": home_elo,
            "away_elo": away_elo,
            "log_gdp_ratio": _safe_log_ratio(home_econ["gdp"], away_econ["gdp"]),
            "log_pop_ratio": _safe_log_ratio(
                home_econ["population"], away_econ["population"]
            ),
            "log_gdp_per_capita_ratio": _safe_log_ratio(
                home_econ["gdp_per_capita"], away_econ["gdp_per_capita"]
            ),
            "h2h_home_win_rate": h2h_rate,
            "h2h_total_weight": h2h_weight,
            "is_neutral": int(is_neutral),
            "match_importance": _k_base(tournament) / 60.0,
        }

        for w in FORM_WINDOWS:
            h_wr, h_gd = self._form_asof(home_team, asof, w)
            a_wr, a_gd = self._form_asof(away_team, asof, w)
            feature_vals[f"home_win_rate_{w}"] = h_wr
            feature_vals[f"home_goal_diff_{w}"] = h_gd
            feature_vals[f"away_win_rate_{w}"] = a_wr
            feature_vals[f"away_goal_diff_{w}"] = a_gd

        feature_vals["home_rest_days"] = self._rest_asof(home_team, asof)
        feature_vals["away_rest_days"] = self._rest_asof(away_team, asof)

        X = pd.DataFrame([feature_vals])[self._feature_cols]
        probs = self._model.predict_proba(X)[0]

        # Map class labels to positions (class_ order is [0,1,2] by construction
        # but we verify so the result is always correctly labelled)
        classes = list(self._model.classes_)
        p = {c: float(p_) for c, p_ in zip(classes, probs)}
        return p.get(0, 0.0), p.get(1, 0.0), p.get(2, 0.0)


# ---------------------------------------------------------------------------
# Module-level singleton for convenient import
# ---------------------------------------------------------------------------

_predictor: Predictor | None = None


def predict_proba(
    home_team: str,
    away_team: str,
    asof: pd.Timestamp | str,
    is_neutral: bool = True,
    tournament: str = "FIFA World Cup",
) -> tuple[float, float, float]:
    """Module-level predict_proba — lazy-loads Predictor on first call.

    Returns:
        (p_home_win, p_draw, p_away_win) summing to 1.0.
    """
    global _predictor
    if _predictor is None:
        _predictor = Predictor()
    if isinstance(asof, str):
        asof = pd.Timestamp(asof)
    return _predictor.predict_proba(home_team, away_team, asof, is_neutral, tournament)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.predictor.model",
        description="Build match outcome features and train the predictor model.",
    )
    p.add_argument(
        "--features-only",
        action="store_true",
        help="Only (re)build features.parquet; skip model training.",
    )
    p.add_argument(
        "--rebuild-features",
        action="store_true",
        help="Force regeneration of features.parquet before training.",
    )
    p.add_argument(
        "--eval",
        action="store_true",
        help="Print validation metrics from an already-trained model and exit.",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()

    if args.eval:
        if not MODEL_PATH.exists():
            print("No model found. Run without --eval to train first.")
            sys.exit(1)
        artifact = joblib.load(MODEL_PATH)
        print(f"Winner:                {artifact['winner']}")
        print(f"LR val log-loss:       {artifact['lr_val_loss']:.4f}")
        print(f"HGBT val log-loss:     {artifact['hgbt_val_loss']:.4f}")
        sys.exit(0)

    if args.features_only:
        print("=" * 60)
        print("Building features only")
        print("=" * 60)
        build_features()
    else:
        print("=" * 60)
        print("Training match outcome predictor")
        print("=" * 60)
        train(rebuild_features=args.rebuild_features)
