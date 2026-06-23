"""
form.py — Recent-form and rest-day features.

For each match row in the processed dataset, adds:
  home/away_win_rate_{5,10}  : fraction of wins in the team's last N matches
  home/away_goal_diff_{5,10} : average goal differential in the last N matches
  home_rest_days / away_rest_days : days since each team's previous match

All features are computed strictly from matches before the current row's date
(no lookahead). The input DataFrame is sorted chronologically on entry.

Win is defined as more goals scored in 90+ET; draws give win=0 and goal_diff=0.

Standalone:
    python -m src.features.form
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import FEATURES_PATH, FORM_WINDOWS, MATCHES_PROCESSED, PROCESSED_DIR


def compute_form(
    matches: pd.DataFrame,
    windows: tuple[int, int] = FORM_WINDOWS,
) -> pd.DataFrame:
    """Add form and rest-day features to a match history.

    Args:
        matches: DataFrame with columns: date (Timestamp), home_team, away_team,
                 home_score (int), away_score (int). Need not be pre-sorted.
        windows: Look-back window sizes (number of previous matches to consider).

    Returns:
        matches sorted by date ascending with new columns:
            home/away_win_rate_{w}, home/away_goal_diff_{w}  for w in windows
            home_rest_days, away_rest_days
        NaN where a team has no prior matches within the window.
    """
    df = matches.sort_values("date", kind="stable").reset_index(drop=True)
    max_w = max(windows)

    # Per-team rolling history: deque of (win: int, goal_diff: int) from team's POV.
    team_hist: dict[str, deque] = {}
    team_last: dict[str, pd.Timestamp] = {}

    out: dict[str, list] = {}
    for w in windows:
        out[f"home_win_rate_{w}"] = []
        out[f"away_win_rate_{w}"] = []
        out[f"home_goal_diff_{w}"] = []
        out[f"away_goal_diff_{w}"] = []
    out["home_rest_days"] = []
    out["away_rest_days"] = []

    for row in df.itertuples(index=False):
        home: str = row.home_team
        away: str = row.away_team
        date: pd.Timestamp = row.date

        if home not in team_hist:
            team_hist[home] = deque(maxlen=max_w)
        if away not in team_hist:
            team_hist[away] = deque(maxlen=max_w)

        h_list = list(team_hist[home])
        a_list = list(team_hist[away])

        for w in windows:
            rh = h_list[-w:]
            ra = a_list[-w:]
            if rh:
                out[f"home_win_rate_{w}"].append(float(np.mean([r[0] for r in rh])))
                out[f"home_goal_diff_{w}"].append(float(np.mean([r[1] for r in rh])))
            else:
                out[f"home_win_rate_{w}"].append(np.nan)
                out[f"home_goal_diff_{w}"].append(np.nan)
            if ra:
                out[f"away_win_rate_{w}"].append(float(np.mean([r[0] for r in ra])))
                out[f"away_goal_diff_{w}"].append(float(np.mean([r[1] for r in ra])))
            else:
                out[f"away_win_rate_{w}"].append(np.nan)
                out[f"away_goal_diff_{w}"].append(np.nan)

        h_last = team_last.get(home)
        a_last = team_last.get(away)
        out["home_rest_days"].append(
            float((date - h_last).days) if h_last is not None else np.nan
        )
        out["away_rest_days"].append(
            float((date - a_last).days) if a_last is not None else np.nan
        )

        # Update AFTER recording — no lookahead
        team_hist[home].append((
            int(row.home_score > row.away_score),
            row.home_score - row.away_score,
        ))
        team_hist[away].append((
            int(row.away_score > row.home_score),
            row.away_score - row.home_score,
        ))
        team_last[home] = date
        team_last[away] = date

    result = df.copy()
    for col, vals in out.items():
        result[col] = vals

    n_cols = len(windows) * 4 + 2
    print(f"    form: added {n_cols} columns to {len(result):,} rows")
    return result


if __name__ == "__main__":
    print("Computing form features …")
    matches = pd.read_csv(MATCHES_PROCESSED, parse_dates=["date"])
    result = compute_form(matches)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / "form_check.csv"
    result[["date", "home_team", "away_team",
            "home_win_rate_5", "away_win_rate_5",
            "home_rest_days", "away_rest_days"]].tail(20).to_csv(out_path, index=False)
    print(f"Sample written → {out_path}")
