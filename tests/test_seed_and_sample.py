"""Seed integrity and the stratified gold sampler (would fail if either returned empty)."""

from __future__ import annotations

from research.gold_sample import TARGETS, propose
from research.seed import load_seed


def test_seed_is_100_apps_10_categories():
    apps = load_seed()
    assert len(apps) == 100
    assert len({a.category for a in apps}) == 10
    assert sum(a.is_trap for a in apps) >= 6  # the dossier's ambiguous/wrong-shape entries


def test_gold_sample_hits_every_stratum_target():
    rows = propose()
    assert len(rows) == sum(TARGETS.values()) == 20
    got = {}
    for r in rows:
        got[r["stratum"]] = got.get(r["stratum"], 0) + 1
    assert got == TARGETS


def test_gold_sample_is_deterministic():
    assert [r["slug"] for r in propose()] == [r["slug"] for r in propose()]
