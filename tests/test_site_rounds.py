"""test_site_rounds.py — Unit tests for the round slug/label mapping."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.site.rounds import ROUND_META, meta_for_round_name, meta_for_slug, sort_key


def test_round_meta_has_five_entries():
    assert len(ROUND_META) == 5


def test_round_meta_order_matches_bracket_order():
    assert [m.round_name for m in ROUND_META] == [
        "Round of 32", "Round of 16", "Quarter-finals", "Semi-finals", "Final",
    ]


def test_meta_for_round_name():
    meta = meta_for_round_name("Round of 32")
    assert meta.slug == "round32"
    assert meta.label == "Round of 32"


def test_meta_for_slug():
    meta = meta_for_slug("quarterfinal")
    assert meta.round_name == "Quarter-finals"
    assert meta.label == "Quarter Final"


def test_sort_key_ascending_with_bracket_order():
    assert sort_key("Round of 32") < sort_key("Round of 16")
    assert sort_key("Round of 16") < sort_key("Quarter-finals")
    assert sort_key("Quarter-finals") < sort_key("Semi-finals")
    assert sort_key("Semi-finals") < sort_key("Final")


def test_labels_match_dropdown_convention():
    """Dropdown copy is "Predictions {label}" -- confirm exact wording."""
    expected = {
        "round32": "Round of 32",
        "round16": "Round of 16",
        "quarterfinal": "Quarter Final",
        "semifinal": "Semi Final",
        "final": "Final",
    }
    for slug, label in expected.items():
        assert meta_for_slug(slug).label == label
