"""Wilson interval + abstention quality — the accuracy math must be right (PRD §6)."""

from __future__ import annotations

from research.score import _score_pass, wilson


def test_wilson_known_values():
    ci = wilson(18, 20)  # 90% of 20
    assert ci["p"] == 0.9
    assert 0.68 < ci["low"] < 0.71   # asymmetric, wide — the point of the exercise
    assert 0.96 < ci["high"] < 0.98
    assert ci["low"] < ci["p"] < ci["high"]


def test_wilson_empty_is_none_not_crash():
    assert wilson(0, 0)["p"] is None


def test_fpc_narrows_interval():
    wide = wilson(18, 20)
    with_fpc = wilson(18, 20, N=100)
    assert (with_fpc["high"] - with_fpc["low"]) < (wide["high"] - wide["low"])


def _rec(slug, auth, gate, mcp="none", tk="absent", build="build_now", ev=None):
    return {
        "slug": slug, "auth_schemes": auth, "gate": gate, "mcp": mcp,
        "composio_toolkit": tk, "buildability": build, "evidence": ev or [],
    }


def test_scoring_and_abstention_quality():
    gold = [
        {"slug": "a", "stratum": "easy", "auth_schemes": ["API_KEY"], "gate": "self_serve",
         "mcp": "none", "composio_toolkit": "absent", "buildability": "build_now"},
        {"slug": "trap", "stratum": "ambiguous", "auth_schemes": ["UNKNOWN"], "gate": "unknown",
         "mcp": "unknown", "composio_toolkit": "unknown", "buildability": "unknown"},
    ]
    records = {
        "a": _rec("a", ["API_KEY"], "self_serve"),                 # perfect row
        "trap": _rec("trap", ["UNKNOWN"], "unknown", "unknown", "unknown", "unknown"),  # good unknown
    }
    out = _score_pass(records, gold, pop=100)
    assert out["whole_row"]["k"] == 2  # both rows fully correct
    assert out["abstention"]["good_unknown"] == 1  # abstained where gold was also unknown
    assert out["abstention"]["bad_unknown"] == 0


def test_bad_unknown_is_counted():
    gold = [{"slug": "b", "stratum": "gated", "auth_schemes": ["OAUTH2"], "gate": "partner_gated",
             "mcp": "none", "composio_toolkit": "absent", "buildability": "build_gated"}]
    records = {"b": _rec("b", ["UNKNOWN"], "unknown", build="unknown")}  # agent bailed on a knowable app
    out = _score_pass(records, gold, pop=100)
    assert out["abstention"]["bad_unknown"] == 1
    assert out["abstention"]["good_unknown"] == 0
