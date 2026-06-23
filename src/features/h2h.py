"""
h2h.py — Head-to-head record features.

For each match row, computes recency-weighted H2H statistics from all prior
meetings between the two teams:

  h2h_home_win_rate  : weighted win fraction for the home team (NaN if no prior H2H)
  h2h_total_weight   : sum of recency weights (proxy for depth of H2H history)

Recency weighting: weight = 0.5 ** (years_ago / H2H_HALF_LIFE_YEARS)
A match 10 years ago gets weight 0.5; a match 20 years ago gets 0.25.

Pairs are keyed by (min(team_a, team_b), max(team_a, team_b)) so the history
is shared regardless of which side each team was on in any given meeting.

All lookups use only matches preceding the current row's date — no lookahead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import H2H_HALF_LIFE_YEARS, MATCHES_PROCESSED


def compute_h2h(
    matches: pd.DataFrame,
    half_life_years: float = H2H_HALF_LIFE_YEARS,
) -> pd.DataFrame:
    """Add recency-weighted H2H features to a match history.

    Args:
        matches: DataFrame with columns: date (Timestamp), home_team, away_team,
                 home_score (int), away_score (int). Need not be pre-sorted.
        half_life_years: Years for the exponential decay to reach 0.5 weight.

    Returns:
        matches sorted by date ascending with two new columns:
            h2h_home_win_rate   : float in [0, 1], NaN if no prior meetings
            h2h_total_weight    : float >= 0, 0.0 if no prior meetings
    """
    df = matches.sort_values("date", kind="stable").reset_index(drop=True)

    # key = (alphabetically first team, alphabetically second team)
    # value = list of (match_date, result_for_first_team)
    #   result: 1.0 = first team won, 0.5 = draw, 0.0 = first team lost
    h2h_hist: dict[tuple[str, str], list[tuple[pd.Timestamp, float]]] = {}

    home_win_rates: list[float] = []
    total_weights: list[float] = []

    for row in df.itertuples(index=False):
        home: str = row.home_team
        away: str = row.away_team
        date: pd.Timestamp = row.date

        key: tuple[str, str] = (min(home, away), max(home, away))
        history = h2h_hist.get(key, [])

        if history:
            first_is_home = key[0] == home
            weighted_sum = 0.0
            weight_total = 0.0
            for hist_date, result_for_first in history:
                years_ago = (date - hist_date).days / 365.25
                w = 0.5 ** (years_ago / half_life_years)
                result_for_home = result_for_first if first_is_home else (1.0 - result_for_first)
                weighted_sum += w * result_for_home
                weight_total += w
            home_win_rates.append(weighted_sum / weight_total)
            total_weights.append(weight_total)
        else:
            home_win_rates.append(np.nan)
            total_weights.append(0.0)

        # Record this match's outcome from first-sorted-team's perspective
        h_score = row.home_score
        a_score = row.away_score
        if key[0] == home:
            outcome = 1.0 if h_score > a_score else (0.5 if h_score == a_score else 0.0)
        else:
            outcome = 1.0 if a_score > h_score else (0.5 if h_score == a_score else 0.0)

        if key not in h2h_hist:
            h2h_hist[key] = []
        h2h_hist[key].append((date, outcome))

    result = df.copy()
    result["h2h_home_win_rate"] = home_win_rates
    result["h2h_total_weight"] = total_weights

    n_with_h2h = sum(1 for v in home_win_rates if not np.isnan(v))
    print(f"    h2h: {n_with_h2h:,}/{len(df):,} rows have prior H2H history")
    return result


if __name__ == "__main__":
    print("Computing H2H features …")
    matches = pd.read_csv(MATCHES_PROCESSED, parse_dates=["date"])
    result = compute_h2h(matches)
    print(result[["date", "home_team", "away_team", "h2h_home_win_rate", "h2h_total_weight"]].tail(10).to_string())
