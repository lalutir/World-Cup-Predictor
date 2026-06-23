"""
build_site.py — Generate the static World Cup predictor dashboard.

Called automatically after montecarlo.py completes, or standalone:
    python -m src.site.build_site results.csv
    python -m src.site.build_site results.csv --n-sims 1000000 --output-dir /tmp/site

Output: site/index.html  +  site/data/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import (
    N_SIMS,
    PAST_WORLD_CUP_WINNERS,
    SITE_DIR,
    SITE_TEMPLATES_DIR,
)

# ---------------------------------------------------------------------------
# Bucket metadata
# ---------------------------------------------------------------------------

_BUCKET_ORDER = [
    "exit_r32", "exit_r16", "exit_qf", "exit_sf",
    "third_place", "runner_up", "champion",
]

_BUCKET_LABELS: dict[str, str] = {
    "exit_r32":    "Round of 32",
    "exit_r16":    "Round of 16",
    "exit_qf":     "Quarter-final",
    "exit_sf":     "Semi-final",
    "third_place": "3rd Place",
    "runner_up":   "Runner-up",
    "champion":    "Champion",
}


# ---------------------------------------------------------------------------
# Stat derivation
# ---------------------------------------------------------------------------

def _compute_team_stats(df: pd.DataFrame) -> list[dict]:
    """Derive display-ready stats from the raw simulation output DataFrame."""
    teams: list[dict] = []
    for _, row in df.iterrows():
        name = str(row["team"])
        r32  = float(row["exit_r32"])
        r16  = float(row["exit_r16"])
        qf   = float(row["exit_qf"])
        sf   = float(row["exit_sf"])
        tp   = float(row["third_place"])
        ru   = float(row["runner_up"])
        ch   = float(row["champion"])

        bucket_vals = {
            "exit_r32": r32, "exit_r16": r16, "exit_qf": qf,
            "exit_sf": sf, "third_place": tp, "runner_up": ru, "champion": ch,
        }
        best_bucket = max(bucket_vals, key=lambda k: bucket_vals[k])

        teams.append({
            "team":          name,
            "is_past_winner": name in PAST_WORLD_CUP_WINNERS,
            # Raw buckets
            "exit_r32":    round(r32, 4),
            "exit_r16":    round(r16, 4),
            "exit_qf":     round(qf,  4),
            "exit_sf":     round(sf,  4),
            "third_place": round(tp,  4),
            "runner_up":   round(ru,  4),
            "champion":    round(ch,  4),
            # Cumulative advancement probabilities
            "p_reach_r16":   round(100.0 - r32,              4),
            "p_reach_qf":    round(100.0 - r32 - r16,        4),
            "p_reach_sf":    round(100.0 - r32 - r16 - qf,   4),
            "p_reach_final": round(ru + ch,                   4),
            # Most common exit
            "most_likely_exit":     _BUCKET_LABELS[best_bucket],
            "most_likely_exit_pct": round(bucket_vals[best_bucket], 4),
        })
    return teams


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_site(
    results: pd.DataFrame,
    n_sims: int = N_SIMS,
    output_dir: Path | None = None,
) -> Path:
    """Generate the static dashboard from simulation results.

    Args:
        results:    DataFrame returned by MonteCarloSimulator.run().
        n_sims:     Number of simulations (used in page metadata).
        output_dir: Root directory for the generated site (default: SITE_DIR).

    Returns:
        Path to the generated index.html.
    """
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError as exc:
        raise ImportError(
            "Jinja2 is required to build the site.  Install it with: pip install jinja2"
        ) from exc

    out_dir  = output_dir or SITE_DIR
    data_dir = out_dir / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    team_stats = _compute_team_stats(results)
    now = datetime.now(timezone.utc)

    payload: dict = {
        "generated_at": now.isoformat(),
        "n_sims":       n_sims,
        "teams":        team_stats,
    }

    # ── Write JSON data file ─────────────────────────────────────────────────
    json_path = data_dir / "results.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ── Render HTML template ─────────────────────────────────────────────────
    env = Environment(
        loader=FileSystemLoader(str(SITE_TEMPLATES_DIR)),
        autoescape=False,
    )
    template = env.get_template("index.html.j2")
    html = template.render(
        data_json=json.dumps(payload, separators=(",", ":")),
        n_sims_fmt=f"{n_sims:,}",
        generated_at=now.strftime("%Y-%m-%d %H:%M UTC"),
    )

    html_path = out_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    print(f"\nDashboard built → {html_path}")
    return html_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        prog="python -m src.site.build_site",
        description="Build the static World Cup predictor dashboard from a simulation results CSV.",
    )
    p.add_argument("results", help="CSV file produced by montecarlo.py --output")
    p.add_argument(
        "--n-sims",
        type=int,
        default=N_SIMS,
        metavar="N",
        help=f"Number of simulations that produced the CSV (default: {N_SIMS:,})",
    )
    p.add_argument(
        "--output-dir",
        type=str,
        default=None,
        metavar="PATH",
        help="Root directory for the generated site (default: site/ in repo root)",
    )
    args = p.parse_args()

    df = pd.read_csv(args.results)
    build_site(df, args.n_sims, Path(args.output_dir) if args.output_dir else None)
