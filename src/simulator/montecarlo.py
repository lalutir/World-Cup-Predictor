"""
montecarlo.py — Vectorized Monte Carlo simulator for the 2026 World Cup knockout bracket.

When run as a script, the simulator first ensures all data is present and
models are trained before running the simulation:

    python -m src.simulator.montecarlo

Full pipeline (executed automatically on first run):
  1. Download raw data   (results, shootouts, former_names, WDI)
  2. Build processed dataset  (Elo, economic features → matches.csv)
  3. Train match-outcome model  (features.parquet → model.pkl)
  4. Train shootout model  (shootout_model.pkl)
  5. Run 1,000,000-iteration Monte Carlo bracket simulation
  6. Print per-team outcome-bucket percentages

Options
-------
    --n N            Number of simulations (default: 1,000,000)
    --fixtures PATH  Bracket CSV (default: data/knockout_fixtures/fixtures.csv)
    --output PATH    Save results table to CSV
    --rebuild        Force re-download of raw data and retrain all models
    --seed N         RNG seed (default: 42, for reproducibility)

Simulation design
-----------------
The tournament is represented as N_SIMS parallel bracket runs.  Each bracket
slot is an integer array of shape (N_SIMS,) whose elements are indices into a
master team list.  Within a round:

  1. Resolve placeholder slots (W<id>, L<id>) to team-index arrays from the
     prior round's winner/loser arrays.
  2. For each match, find the set of unique (home_idx, away_idx) pairs that
     actually appear across the N_SIMS simulations (far fewer than N_SIMS).
  3. Call predict_proba **once per unique pair** to get win/draw/loss probs.
  4. Vectorize the random draw across all sims sharing each matchup.
  5. Route drawn matches to penalty shootout (resolve_shootout called once per
     unique draw-matchup pair, same caching strategy).
  6. Accumulate per-team outcome-bucket counts.

Outcome buckets (per team, sums to 100 % across all 7 buckets):
  exit_r32    — lost in Round of 32
  exit_r16    — lost in Round of 16
  exit_qf     — lost in Quarter-finals
  exit_sf     — lost in Semi-finals AND lost the Third-place play-off
  third_place — lost in Semi-finals AND won the Third-place play-off
  runner_up   — lost the Final
  champion    — won the Final

Elo ratings are frozen at their pre-tournament snapshot for the entire
simulation run — no intra-tournament Elo updates as simulated rounds progress.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.bracket.bracket import STADIUM_COUNTRY, BracketResolver
from src.config import (
    ELO_SEED_RATING,
    FEATURES_PATH,
    FIXTURES_PATH,
    MATCHES_PROCESSED,
    MODEL_PATH,
    N_SIMS,
    PROCESSED_DIR,
    RNG_SEED,
    SHOOTOUT_MODEL_PATH,
)
from src.predictor.shootout import resolve_shootout

# ---------------------------------------------------------------------------
# Predictor protocol — allows dependency injection of a mock in tests
# ---------------------------------------------------------------------------


class PredictorLike(Protocol):
    """Structural type for any object usable as a match-outcome predictor."""

    def predict_proba(
        self,
        home_team: str,
        away_team: str,
        asof: pd.Timestamp,
        is_neutral: bool,
        tournament: str,
    ) -> tuple[float, float, float]: ...

    def _elo_asof(self, team: str, asof: pd.Timestamp) -> float: ...


# ---------------------------------------------------------------------------
# Match-level vectorized simulation
# ---------------------------------------------------------------------------


def _simulate_match(
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    match_date: pd.Timestamp,
    venue_country: str,
    predictor: PredictorLike,
    idx_to_team: list[str],
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Simulate one bracket match across all N_SIMS simulations.

    Args:
        home_idx:     (N_SIMS,) int32 — team index for the home slot per sim.
        away_idx:     (N_SIMS,) int32 — team index for the away slot per sim.
        match_date:   Timestamp used as the asof date for all feature lookups.
        venue_country: Country where the match is played (from STADIUM_COUNTRY).
        predictor:    Model that returns (p_home_win, p_draw, p_away_win).
        idx_to_team:  Maps integer index → canonical team name.
        rng:          Seeded numpy Generator (PCG64).

    Returns:
        (winner_idx, loser_idx) — each (N_SIMS,) int32 arrays.
    """
    n_sims = len(home_idx)
    n_teams = len(idx_to_team)

    # Derive is_neutral per sim: not neutral only when the venue country's
    # team (a host nation) is one of the two participants.
    if venue_country in ("__neutral__", ""):
        is_neutral_arr = np.ones(n_sims, dtype=np.int8)
    else:
        venue_team_idx = next(
            (i for i, t in enumerate(idx_to_team) if t == venue_country), -1
        )
        if venue_team_idx < 0:
            is_neutral_arr = np.ones(n_sims, dtype=np.int8)
        else:
            is_neutral_arr = (
                (home_idx != venue_team_idx) & (away_idx != venue_team_idx)
            ).astype(np.int8)

    # Identify unique (home, away, is_neutral) triplets to minimize model calls.
    # Encode as a single int64: home * n_teams * 2 + away * 2 + is_neutral
    triplet_codes = (
        home_idx.astype(np.int64) * (n_teams * 2)
        + away_idx.astype(np.int64) * 2
        + is_neutral_arr.astype(np.int64)
    )
    unique_codes = np.unique(triplet_codes)

    # Flat lookup arrays indexed by triplet_code.
    max_code = n_teams * n_teams * 2
    p_hw_flat  = np.zeros(max_code, dtype=np.float64)
    p_d_flat   = np.zeros(max_code, dtype=np.float64)
    p_so_flat  = np.full(max_code, 0.5, dtype=np.float64)

    for code in unique_codes:
        hi   = int(code // (n_teams * 2))
        rest = int(code %  (n_teams * 2))
        ai   = rest // 2
        neut = bool(rest % 2)

        home_name = idx_to_team[hi]
        away_name = idx_to_team[ai]

        p_hw, p_d, _ = predictor.predict_proba(
            home_name, away_name, match_date, neut, "FIFA World Cup"
        )
        h_elo = predictor._elo_asof(home_name, match_date)
        a_elo = predictor._elo_asof(away_name, match_date)
        p_so, _ = resolve_shootout(home_name, away_name, h_elo, a_elo)

        p_hw_flat[code]  = p_hw
        p_d_flat[code]   = p_d
        p_so_flat[code]  = p_so

    # Vectorized probability lookup for all N_SIMS.
    p_hw = p_hw_flat[triplet_codes]
    p_d  = p_d_flat[triplet_codes]
    p_so = p_so_flat[triplet_codes]

    # Draw outcomes.
    r    = rng.random(n_sims)
    r_so = rng.random(n_sims)

    home_wins_open = r < p_hw
    is_draw        = (r >= p_hw) & (r < p_hw + p_d)
    home_wins_so   = r_so < p_so

    # After shootout resolution there are no draws; only home/away wins.
    home_wins = home_wins_open | (is_draw & home_wins_so)

    winner_idx = np.where(home_wins, home_idx, away_idx).astype(np.int32)
    loser_idx  = np.where(home_wins, away_idx, home_idx).astype(np.int32)
    return winner_idx, loser_idx


def _tally(outcome_counts: np.ndarray, team_arr: np.ndarray, bucket: int) -> None:
    """Increment outcome_counts[team_idx, bucket] for each sim in team_arr."""
    unique_teams, counts = np.unique(team_arr, return_counts=True)
    for t, c in zip(unique_teams, counts):
        outcome_counts[t, bucket] += c


# ---------------------------------------------------------------------------
# Main simulator class
# ---------------------------------------------------------------------------


class MonteCarloSimulator:
    """Vectorized knockout-bracket Monte Carlo engine.

    Args:
        n_sims: Number of parallel simulation runs (default 1,000,000).
        seed:   RNG seed for reproducibility (default from config.RNG_SEED).
    """

    # Outcome bucket indices
    EXIT_R32     = 0
    EXIT_R16     = 1
    EXIT_QF      = 2
    EXIT_SF      = 3
    THIRD_PLACE  = 4
    RUNNER_UP    = 5
    CHAMPION     = 6
    N_BUCKETS    = 7
    BUCKET_NAMES = [
        "exit_r32", "exit_r16", "exit_qf", "exit_sf",
        "third_place", "runner_up", "champion",
    ]

    # Maps round name → loser bucket index (for rounds with immediate elimination)
    _ROUND_LOSER_BUCKET: dict[str, int] = {
        "Round of 32": EXIT_R32,
        "Round of 16": EXIT_R16,
        "Quarter-finals": EXIT_QF,
    }

    def __init__(self, n_sims: int = N_SIMS, seed: int = RNG_SEED) -> None:
        self.n_sims = n_sims
        self.rng = np.random.Generator(np.random.PCG64(seed))

    def _resolve_slot_array(
        self,
        slot: str,
        team_to_idx: dict[str, int],
        winner_of: dict[int, np.ndarray],
        loser_of: dict[int, np.ndarray],
    ) -> np.ndarray:
        """Return an (n_sims,) int32 team-index array for *slot*."""
        if slot.startswith("W") and slot[1:].isdigit():
            return winner_of[int(slot[1:])]
        if slot.startswith("L") and slot[1:].isdigit():
            return loser_of[int(slot[1:])]
        # Literal team name: all sims agree on the same team.
        idx = team_to_idx.get(slot, 0)
        return np.full(self.n_sims, idx, dtype=np.int32)

    def run(
        self,
        fixtures_path: Path,
        predictor: PredictorLike,
    ) -> pd.DataFrame:
        """Run the full Monte Carlo simulation.

        Args:
            fixtures_path: Path to the bracket CSV (fixtures.csv or test_bracket.csv).
            predictor:     Trained Predictor (or a compatible mock for tests).

        Returns:
            DataFrame with columns: team, exit_r32, exit_r16, exit_qf, exit_sf,
            third_place, runner_up, champion — all as percentages summing to 100.0
            per team row.  Sorted by champion% descending.
        """
        resolver = BracketResolver.from_csv(fixtures_path)
        initial_teams = resolver.all_initial_teams()

        # Warn if the fixtures still contain group-stage placeholders.
        unresolved = [t for t in initial_teams if "Group" in t or "Best 3rd" in t]
        if unresolved:
            warnings.warn(
                f"{len(unresolved)} group-stage placeholder(s) remain in fixtures.csv "
                "(e.g. 'Group A runners-up').  These slots will use default Elo (1500) "
                "with no form/H2H history.  Replace them with actual team names for "
                "accurate predictions.",
                UserWarning,
                stacklevel=2,
            )

        n_teams = len(initial_teams)
        team_to_idx: dict[str, int] = {t: i for i, t in enumerate(initial_teams)}
        idx_to_team: list[str] = list(initial_teams)

        winner_of: dict[int, np.ndarray] = {}
        loser_of:  dict[int, np.ndarray] = {}

        outcome_counts = np.zeros((n_teams, self.N_BUCKETS), dtype=np.int64)

        for round_name, matches in resolver.rounds_ordered():
            n_matches = len(matches)
            print(f"  Simulating {round_name} ({n_matches} match{'es' if n_matches != 1 else ''}) …")

            for match in matches:
                home_arr = self._resolve_slot_array(
                    match.home_slot, team_to_idx, winner_of, loser_of
                )
                away_arr = self._resolve_slot_array(
                    match.away_slot, team_to_idx, winner_of, loser_of
                )

                venue_country = STADIUM_COUNTRY.get(match.stadium, "__neutral__")

                w_arr, l_arr = _simulate_match(
                    home_arr, away_arr,
                    match.date,
                    venue_country,
                    predictor,
                    idx_to_team,
                    self.rng,
                )

                winner_of[match.match_id] = w_arr
                loser_of[match.match_id]  = l_arr

                if round_name in self._ROUND_LOSER_BUCKET:
                    _tally(outcome_counts, l_arr, self._ROUND_LOSER_BUCKET[round_name])
                elif round_name == "Semi-finals":
                    pass  # SF losers resolved after Third-place play-off
                elif round_name == "Third place play-off":
                    _tally(outcome_counts, w_arr, self.THIRD_PLACE)
                    _tally(outcome_counts, l_arr, self.EXIT_SF)
                elif round_name == "Final":
                    _tally(outcome_counts, w_arr, self.CHAMPION)
                    _tally(outcome_counts, l_arr, self.RUNNER_UP)

        rows = []
        for i, team in enumerate(idx_to_team):
            row: dict[str, object] = {"team": team}
            for j, bucket in enumerate(self.BUCKET_NAMES):
                row[bucket] = round(outcome_counts[i, j] / self.n_sims * 100, 4)
            rows.append(row)

        df = (
            pd.DataFrame(rows)
            .sort_values("champion", ascending=False)
            .reset_index(drop=True)
        )
        return df


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


def _ensure_data(force_rebuild: bool) -> None:
    """Download raw data and build processed matches.csv if needed."""
    from src.data.build_dataset import build as _build_dataset

    if force_rebuild or not MATCHES_PROCESSED.exists():
        print("\n[Pipeline 1/3] Building dataset (downloading raw data + Elo) …")
        _build_dataset()
    else:
        print(
            f"\n[Pipeline 1/3] Dataset already present "
            f"({MATCHES_PROCESSED.name}) — skipping re-download."
        )
        print("               Use --rebuild to force a fresh download.")


def _ensure_models(force_rebuild: bool) -> None:
    """Train match-outcome and shootout models if needed."""
    from src.predictor.model import train as _train_model
    from src.predictor.shootout import train_shootout as _train_shootout

    need_model    = force_rebuild or not MODEL_PATH.exists()
    need_features = force_rebuild or not FEATURES_PATH.exists()
    need_shootout = force_rebuild or not SHOOTOUT_MODEL_PATH.exists()

    if need_model or need_features:
        print("\n[Pipeline 2/3] Training match-outcome model …")
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        _train_model(rebuild_features=(need_features or force_rebuild))
    else:
        print(
            f"\n[Pipeline 2/3] Match-outcome model already present "
            f"({MODEL_PATH.name}) — skipping training."
        )

    if need_shootout:
        print("\n[Pipeline 2b/3] Training shootout model …")
        _train_shootout()
    else:
        print(
            f"               Shootout model already present "
            f"({SHOOTOUT_MODEL_PATH.name}) — skipping."
        )


def _load_predictor() -> "PredictorLike":
    from src.predictor.model import Predictor
    return Predictor()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_results(df: pd.DataFrame) -> None:
    """Print the results table in a readable fixed-width format."""
    bucket_cols = MonteCarloSimulator.BUCKET_NAMES
    header_row = f"{'Team':<30} {'R32%':>7} {'R16%':>7} {'QF%':>7} {'SF%':>7} {'3rd%':>7} {'RU%':>7} {'Win%':>7}"
    sep = "-" * len(header_row)
    print()
    print(header_row)
    print(sep)
    for _, row in df.iterrows():
        vals = [row[b] for b in bucket_cols]
        print(
            f"{row['team']:<30} "
            + " ".join(f"{v:>7.2f}" for v in vals)
        )
    print(sep)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.simulator.montecarlo",
        description=(
            "Run the 2026 World Cup knockout-phase Monte Carlo simulator.\n\n"
            "On first run the pipeline downloads raw data and trains all "
            "models automatically before simulating."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--n",
        type=int,
        default=N_SIMS,
        metavar="N",
        help=f"Number of simulations (default: {N_SIMS:,})",
    )
    p.add_argument(
        "--fixtures",
        type=str,
        default=str(FIXTURES_PATH),
        metavar="PATH",
        help=f"Bracket fixtures CSV (default: {FIXTURES_PATH})",
    )
    p.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Save results to this CSV file (optional)",
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help="Force re-download of all raw data and retrain all models",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=RNG_SEED,
        metavar="N",
        help=f"Random seed for reproducibility (default: {RNG_SEED})",
    )
    p.add_argument(
        "--no-site",
        action="store_true",
        help="Skip building the static HTML dashboard after simulation",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()

    print("=" * 60)
    print("2026 FIFA World Cup — Knockout Phase Monte Carlo Simulator")
    print("=" * 60)

    # Step 1: Ensure data
    _ensure_data(force_rebuild=args.rebuild)

    # Step 2: Ensure models
    _ensure_models(force_rebuild=args.rebuild)

    # Step 3: Run simulation
    print(f"\n[Pipeline 3/3] Running {args.n:,} Monte Carlo simulations …")
    predictor = _load_predictor()
    sim = MonteCarloSimulator(n_sims=args.n, seed=args.seed)
    results = sim.run(Path(args.fixtures), predictor)

    # Step 4: Output
    _print_results(results)

    if args.output:
        out_path = Path(args.output)
        results.to_csv(out_path, index=False)
        print(f"\nResults saved → {out_path}")

    # Step 5: Build static dashboard (skippable with --no-site)
    if not args.no_site:
        print("\n[Pipeline 4/4] Building static dashboard …")
        try:
            from src.site.build_site import build_site
            build_site(results, n_sims=args.n, fixtures_path=Path(args.fixtures))
        except ImportError as exc:
            print(f"  Skipped (missing dependency): {exc}")
            print("  Install with: pip install jinja2")

    print("\nDone.")
