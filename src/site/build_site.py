"""
build_site.py — Generate the static World Cup predictor dashboard.

Called automatically after montecarlo.py completes, or standalone:
    python -m src.site.build_site results.csv
    python -m src.site.build_site results.csv --n-sims 1000000 --output-dir /tmp/site

Each run detects which round of the bracket has fully known participants
(via BracketResolver.detect_frontier_round), archives that round's
predictions permanently under data/site_archive/<slug>.json, and rebuilds
every archived round's page plus /current -- so old archived pages' round
switcher stays up to date as new rounds get simulated.

Output: site/<slug>/index.html + data/results.json for every archived
round, plus site/current/ mirroring the latest one, plus a site/index.html
landing page with a grid of tiles linking to each available round.
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

from src.bracket.bracket import BracketResolver
from src.config import (
    FIXTURES_PATH,
    N_SIMS,
    PAST_WORLD_CUP_WINNERS,
    SITE_ARCHIVE_DIR,
    SITE_DIR,
    SITE_TEMPLATES_DIR,
)
from src.site.rounds import ROUND_META, meta_for_round_name, meta_for_slug, sort_key

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
    "exit_sf":     "4th Place",
    "third_place": "3rd Place",
    "runner_up":   "Runner-up",
    "champion":    "Champion",
}

# ---------------------------------------------------------------------------
# Per-round column visibility
# ---------------------------------------------------------------------------
#
# Once the bracket has moved past a round, every team on that round's page
# already survived it in real life -- so a "Reach <that round>" cell would
# always read 100% and an "Exit <that round>" cell would always read 0%.
# Both are dead weight once they're no longer live predictions, so they're
# dropped from the "How Far Each Team Went" / "Full Probability Breakdown"
# tables as the frontier round advances. `decided_by_round_index` is the
# FRONTIER_ROUNDS index of the round each column threshold belongs to; a
# column is hidden once the page's own round index has moved past it.

_REACH_COLUMNS = [
    {"key": "p_reach_r16",   "header": "Reach R16",   "decided_by_round_index": 0},
    {"key": "p_reach_qf",    "header": "Reach QF",    "decided_by_round_index": 1},
    {"key": "p_reach_sf",    "header": "Reach SF",    "decided_by_round_index": 2},
    {"key": "p_reach_final", "header": "Reach Final", "decided_by_round_index": 3},
]
_WIN_IT_COLUMN = {"key": "champion", "header": "Win It"}

_EXIT_COLUMNS = [
    {"key": "exit_r32", "header": "Exit R32", "styleLabel": "Round of 32",   "decided_by_round_index": 0},
    {"key": "exit_r16", "header": "Exit R16", "styleLabel": "Round of 16",   "decided_by_round_index": 1},
    {"key": "exit_qf",  "header": "Exit QF",  "styleLabel": "Quarter-final", "decided_by_round_index": 2},
    {"key": "exit_sf",  "header": "Exit SF",  "styleLabel": "4th Place",    "decided_by_round_index": 3},
]
_ALWAYS_EXIT_COLUMNS = [
    {"key": "third_place", "header": "3rd Place", "styleLabel": "3rd Place"},
    {"key": "runner_up",   "header": "Runner-up", "styleLabel": "Runner-up"},
    {"key": "champion",    "header": "Champion",  "styleLabel": "Champion"},
]


def _visible_adv_columns(round_index: int) -> list[dict]:
    """"How Far Each Team Went" columns still worth showing at `round_index`:
    reach thresholds not yet decided in real life, plus the always-shown
    "Win It" column."""
    cols = [
        {"key": c["key"], "header": c["header"]}
        for c in _REACH_COLUMNS
        if c["decided_by_round_index"] >= round_index
    ]
    cols.append(_WIN_IT_COLUMN)
    return cols


def _visible_full_columns(round_index: int) -> list[dict]:
    """"Full Probability Breakdown" columns still worth showing at
    `round_index`: exit buckets not yet decided in real life, plus the
    always-shown terminal buckets (3rd place / runner-up / champion)."""
    cols = [
        {"key": c["key"], "header": c["header"], "styleLabel": c["styleLabel"]}
        for c in _EXIT_COLUMNS
        if c["decided_by_round_index"] >= round_index
    ]
    cols.extend(_ALWAYS_EXIT_COLUMNS)
    return cols


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
            "exit_r32":    round(r32, 4),
            "exit_r16":    round(r16, 4),
            "exit_qf":     round(qf,  4),
            "exit_sf":     round(sf,  4),
            "third_place": round(tp,  4),
            "runner_up":   round(ru,  4),
            "champion":    round(ch,  4),
            "p_reach_r16":   round(100.0 - r32,              4),
            "p_reach_qf":    round(100.0 - r32 - r16,        4),
            "p_reach_sf":    round(100.0 - r32 - r16 - qf,   4),
            "p_reach_final": round(ru + ch,                   4),
            "most_likely_exit":     _BUCKET_LABELS[best_bucket],
            "most_likely_exit_pct": round(bucket_vals[best_bucket], 4),
        })
    return teams


# ---------------------------------------------------------------------------
# Round archive I/O
# ---------------------------------------------------------------------------

def _archive_path(archive_dir: Path, slug: str) -> Path:
    return archive_dir / f"{slug}.json"


def _write_archive(archive_dir: Path, slug: str, payload: dict) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    _archive_path(archive_dir, slug).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _load_archives(archive_dir: Path) -> dict[str, dict]:
    """Return {slug: payload} for every archived round found on disk, in
    bracket order."""
    archives: dict[str, dict] = {}
    if not archive_dir.exists():
        return archives
    for meta in ROUND_META:
        path = _archive_path(archive_dir, meta.slug)
        if path.exists():
            archives[meta.slug] = json.loads(path.read_text(encoding="utf-8"))
    return archives


def _latest_slug(archived_slugs: set[str]) -> str:
    """Return the slug with the deepest bracket order among archived_slugs."""
    return max(archived_slugs, key=lambda s: sort_key(meta_for_slug(s).round_name))


# ---------------------------------------------------------------------------
# Round-switcher nav
# ---------------------------------------------------------------------------

def _build_nav_items(
    archived_slugs: set[str], latest_slug: str, active_slug: str
) -> list[dict]:
    """Build the round-switcher dropdown: "Current Predictions" pinned
    first, then archived rounds in descending bracket order (most advanced
    first). Only slugs present in archived_slugs are included."""
    items: list[dict] = [{
        "label": "Current Predictions",
        "url": "/current/",
        "active": active_slug == "current",
        "show_current_tag": False,
    }]
    for meta in reversed(ROUND_META):
        if meta.slug not in archived_slugs:
            continue
        items.append({
            "label": f"Predictions {meta.label}",
            "url": f"/{meta.slug}/",
            "active": active_slug == meta.slug,
            "show_current_tag": meta.slug == latest_slug,
        })
    return items


def _format_generated_at(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# Root landing page (grid of tiles, one per available round + Current)
# ---------------------------------------------------------------------------

def _build_landing_tiles(archives: dict[str, dict], latest_slug: str) -> list[dict]:
    """Build the root landing page's tile grid: "Current Predictions"
    pinned first, then archived rounds in descending bracket order (most
    advanced first). Only slugs present in archives are included."""
    tiles: list[dict] = [{
        "label": "Current Predictions",
        "url": "/current/",
        "generated_at": _format_generated_at(archives[latest_slug]["generated_at"]),
        "show_current_tag": False,
    }]
    for meta in reversed(ROUND_META):
        if meta.slug not in archives:
            continue
        payload = archives[meta.slug]
        tiles.append({
            "label": f"Predictions {meta.label}",
            "url": f"/{meta.slug}/",
            "generated_at": _format_generated_at(payload["generated_at"]),
            "show_current_tag": meta.slug == latest_slug,
        })
    return tiles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_site(
    results: pd.DataFrame,
    n_sims: int = N_SIMS,
    fixtures_path: Path | None = None,
    output_dir: Path | None = None,
    archive_dir: Path | None = None,
) -> Path:
    """Generate the static dashboard from simulation results.

    Detects which round is the current "frontier" (deepest round whose
    matches are all fully known) from fixtures_path, archives this run's
    predictions under that round's slug, then rebuilds every archived
    round's page plus /current from the full set of archives on disk.

    Args:
        results:       DataFrame returned by MonteCarloSimulator.run().
        n_sims:        Number of simulations (used in page metadata).
        fixtures_path: Bracket CSV used to detect the current round
                       (default: FIXTURES_PATH).
        output_dir:    Root directory for the generated site (default:
                       SITE_DIR).
        archive_dir:   Directory for permanent per-round JSON snapshots
                       (default: SITE_ARCHIVE_DIR).

    Returns:
        Path to the generated site/current/index.html.
    """
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError as exc:
        raise ImportError(
            "Jinja2 is required to build the site.  Install it with: pip install jinja2"
        ) from exc

    fixtures_path = fixtures_path or FIXTURES_PATH
    out_dir      = output_dir or SITE_DIR
    archive_dir  = archive_dir or SITE_ARCHIVE_DIR

    resolver   = BracketResolver.from_csv(fixtures_path)
    round_name = resolver.detect_frontier_round()
    meta       = meta_for_round_name(round_name)

    team_stats = _compute_team_stats(results)
    now = datetime.now(timezone.utc)

    payload: dict = {
        "generated_at": now.isoformat(),
        "n_sims":       n_sims,
        "round_slug":   meta.slug,
        "round_label":  meta.label,
        "teams":        team_stats,
    }
    _write_archive(archive_dir, meta.slug, payload)

    archives       = _load_archives(archive_dir)
    archived_slugs = set(archives)
    latest_slug    = _latest_slug(archived_slugs)

    env = Environment(
        loader=FileSystemLoader(str(SITE_TEMPLATES_DIR)),
        autoescape=False,
    )
    template = env.get_template("index.html.j2")

    def _render_and_write(slug_payload: dict, target_dir: Path, active_slug: str) -> None:
        nav_items = _build_nav_items(archived_slugs, latest_slug, active_slug)
        active_label = next(item["label"] for item in nav_items if item["active"])

        round_index = sort_key(meta_for_slug(slug_payload["round_slug"]).round_name)
        adv_columns = _visible_adv_columns(round_index)
        full_columns = _visible_full_columns(round_index)

        html = template.render(
            data_json=json.dumps(slug_payload, separators=(",", ":")),
            adv_columns=adv_columns,
            adv_columns_json=json.dumps(adv_columns, separators=(",", ":")),
            full_columns=full_columns,
            full_columns_json=json.dumps(full_columns, separators=(",", ":")),
            n_sims_fmt=f"{slug_payload['n_sims']:,}",
            generated_at=_format_generated_at(slug_payload["generated_at"]),
            round_label=slug_payload["round_label"],
            nav_items=nav_items,
            active_nav_label=active_label,
        )

        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "index.html").write_text(html, encoding="utf-8")

        data_dir = target_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "results.json").write_text(
            json.dumps(slug_payload, indent=2), encoding="utf-8"
        )

    for slug, slug_payload in archives.items():
        _render_and_write(slug_payload, out_dir / slug, active_slug=slug)

    _render_and_write(archives[latest_slug], out_dir / "current", active_slug="current")

    landing_template = env.get_template("landing.html.j2")
    landing_html = landing_template.render(tiles=_build_landing_tiles(archives, latest_slug))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(landing_html, encoding="utf-8")

    current_index = out_dir / "current" / "index.html"
    print(f"\nDashboard built -> {current_index}  (latest round: {latest_slug})")
    print(f"Landing page   -> {out_dir / 'index.html'}")
    return current_index


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
        "--fixtures",
        type=str,
        default=str(FIXTURES_PATH),
        metavar="PATH",
        help=f"Bracket fixtures CSV used to detect the current round (default: {FIXTURES_PATH})",
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
    build_site(
        df,
        args.n_sims,
        fixtures_path=Path(args.fixtures),
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
