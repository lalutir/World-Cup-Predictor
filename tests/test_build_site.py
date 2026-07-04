"""
test_build_site.py — Unit/integration tests for the static site builder.

Covers round archiving (data/site_archive/<slug>.json), the round-switcher
dropdown nav list, and the multi-page render loop (/current + one page per
archived round) -- all against temp directories, no real project data
touched.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.site.build_site import _build_nav_items, _load_archives, build_site


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIXTURES_ROUND32 = """match_id,round,home_team,away_team,stadium,date
1,Round of 32,TeamA,TeamB,Test Stadium,2026-07-01
2,Round of 32,TeamC,TeamD,Test Stadium,2026-07-01
3,Round of 16,W1,W2,Test Stadium,2026-07-04
4,Quarter-finals,W3,W3,Test Stadium,2026-07-09
5,Semi-finals,W4,W4,Test Stadium,2026-07-14
6,Third place play-off,L5,L5,Test Stadium,2026-07-18
7,Final,W5,W5,Test Stadium,2026-07-19
"""

_FIXTURES_ROUND16 = """match_id,round,home_team,away_team,stadium,date
1,Round of 32,TeamA,TeamB,Test Stadium,2026-07-01
2,Round of 32,TeamC,TeamD,Test Stadium,2026-07-01
3,Round of 16,TeamA,TeamC,Test Stadium,2026-07-04
4,Quarter-finals,W3,W3,Test Stadium,2026-07-09
5,Semi-finals,W4,W4,Test Stadium,2026-07-14
6,Third place play-off,L5,L5,Test Stadium,2026-07-18
7,Final,W5,W5,Test Stadium,2026-07-19
"""


def _tiny_results_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"team": "TeamA", "exit_r32": 10.0, "exit_r16": 20.0, "exit_qf": 20.0,
         "exit_sf": 15.0, "third_place": 10.0, "runner_up": 10.0, "champion": 15.0},
        {"team": "TeamB", "exit_r32": 5.0, "exit_r16": 15.0, "exit_qf": 25.0,
         "exit_sf": 20.0, "third_place": 10.0, "runner_up": 15.0, "champion": 10.0},
    ])


@pytest.fixture
def site_dirs(tmp_path):
    return {
        "fixtures": tmp_path / "fixtures.csv",
        "site": tmp_path / "site",
        "archive": tmp_path / "site_archive",
    }


# ---------------------------------------------------------------------------
# _build_nav_items
# ---------------------------------------------------------------------------


def test_nav_items_single_archived_round():
    items = _build_nav_items({"round32"}, latest_slug="round32", active_slug="round32")
    assert [i["label"] for i in items] == ["Current Predictions", "Predictions Round of 32"]
    assert items[0]["active"] is False
    assert items[1]["active"] is True
    assert items[1]["show_current_tag"] is True


def test_nav_items_current_active():
    items = _build_nav_items({"round32"}, latest_slug="round32", active_slug="current")
    assert items[0]["active"] is True
    assert items[1]["active"] is False


def test_nav_items_two_rounds_newest_first():
    items = _build_nav_items(
        {"round32", "round16"}, latest_slug="round16", active_slug="round16"
    )
    labels = [i["label"] for i in items]
    assert labels == [
        "Current Predictions",
        "Predictions Round of 16",
        "Predictions Round of 32",
    ]
    tag_map = {i["label"]: i["show_current_tag"] for i in items}
    assert tag_map["Predictions Round of 16"] is True
    assert tag_map["Predictions Round of 32"] is False


# ---------------------------------------------------------------------------
# build_site -- archiving + multi-page render
# ---------------------------------------------------------------------------


def test_build_site_archives_round32(site_dirs):
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")

    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    archive_file = site_dirs["archive"] / "round32.json"
    assert archive_file.exists()
    payload = json.loads(archive_file.read_text(encoding="utf-8"))
    assert payload["round_slug"] == "round32"
    assert payload["round_label"] == "Round of 32"
    assert len(payload["teams"]) == 2


def test_build_site_writes_current_and_round_pages(site_dirs):
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")

    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    assert (site_dirs["site"] / "current" / "index.html").exists()
    assert (site_dirs["site"] / "current" / "data" / "results.json").exists()
    assert (site_dirs["site"] / "round32" / "index.html").exists()
    assert (site_dirs["site"] / "round32" / "data" / "results.json").exists()


def test_build_site_second_round_creates_both_archives(site_dirs):
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    site_dirs["fixtures"].write_text(_FIXTURES_ROUND16, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    archives = _load_archives(site_dirs["archive"])
    assert set(archives) == {"round32", "round16"}
    assert (site_dirs["site"] / "round32" / "index.html").exists()
    assert (site_dirs["site"] / "round16" / "index.html").exists()
    assert (site_dirs["site"] / "current" / "index.html").exists()


# ---------------------------------------------------------------------------
# Template rendering -- round label, title, dropdown markup
# ---------------------------------------------------------------------------


def test_rendered_page_shows_round_label_and_title(site_dirs):
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    html = (site_dirs["site"] / "round32" / "index.html").read_text(encoding="utf-8")
    assert "<title>2026 FIFA World Cup Predictor — Round of 32</title>" in html
    assert 'class="round-switcher"' in html


def test_dropdown_updates_on_older_page_after_new_round_archived(site_dirs):
    """Archiving Round of 16 must also rebuild the Round of 32 page's nav
    so its dropdown includes the newly archived round, with the
    "current" tag moved onto Round of 16."""
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    site_dirs["fixtures"].write_text(_FIXTURES_ROUND16, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    round32_html = (site_dirs["site"] / "round32" / "index.html").read_text(encoding="utf-8")
    assert "Predictions Round of 16" in round32_html
    assert 'href="/round16/"' in round32_html
    assert 'href="/round32/"' in round32_html

    current_html = (site_dirs["site"] / "current" / "index.html").read_text(encoding="utf-8")
    assert "Predictions Round of 16" in current_html
