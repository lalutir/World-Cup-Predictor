"""
rounds.py — Round name <-> URL slug / dropdown label mapping for the site.

Bridges the bracket's round names (as used in fixtures.csv and
BracketResolver, e.g. "Round of 32") to the static site's URL slugs
(e.g. "round32") and dropdown copy (e.g. "Round of 32" -> "Predictions
Round of 32"). Bracket order matters for sorting the round-switcher
dropdown -- reuses FRONTIER_ROUNDS from bracket.py as the single source of
truth for that order.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.bracket.bracket import FRONTIER_ROUNDS


@dataclass(frozen=True)
class RoundMeta:
    round_name: str   # e.g. "Round of 32" (matches fixtures.csv `round` column)
    slug: str         # e.g. "round32" (URL path segment)
    label: str        # e.g. "Round of 32" (used in "Predictions {label}")


_SLUGS_AND_LABELS: list[tuple[str, str]] = [
    ("round32",      "Round of 32"),
    ("round16",      "Round of 16"),
    ("quarterfinal", "Quarter Final"),
    ("semifinal",    "Semi Final"),
    ("final",        "Final"),
]

assert len(FRONTIER_ROUNDS) == len(_SLUGS_AND_LABELS)

ROUND_META: list[RoundMeta] = [
    RoundMeta(round_name, slug, label)
    for round_name, (slug, label) in zip(FRONTIER_ROUNDS, _SLUGS_AND_LABELS)
]

_BY_ROUND_NAME: dict[str, RoundMeta] = {m.round_name: m for m in ROUND_META}
_BY_SLUG: dict[str, RoundMeta] = {m.slug: m for m in ROUND_META}
_ORDER: dict[str, int] = {m.round_name: i for i, m in enumerate(ROUND_META)}


def meta_for_round_name(round_name: str) -> RoundMeta:
    """Return the RoundMeta for a fixtures.csv round name (e.g. "Round of 32")."""
    return _BY_ROUND_NAME[round_name]


def meta_for_slug(slug: str) -> RoundMeta:
    """Return the RoundMeta for a URL slug (e.g. "round32")."""
    return _BY_SLUG[slug]


def sort_key(round_name: str) -> int:
    """Bracket order index for sorting -- lower is earlier in the bracket."""
    return _ORDER[round_name]
