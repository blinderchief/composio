"""Deterministic verification loops, queue lanes, and catalog matching."""

from __future__ import annotations

from research.catalog import _norm
from research.models import AppRecord
from research.queue import _effort, _lane
from research.verify import _normalize


# --- claim-support normalization (loop 2) ------------------------------------

def test_claim_support_normalization_ignores_punctuation_and_case():
    quote = "Authenticate with a Bearer token."
    doc = "You must authenticate   with a   bearer TOKEN, per the docs."
    assert _normalize(quote) in _normalize(doc)


def test_claim_support_rejects_absent_quote():
    assert _normalize("requires partner approval") not in _normalize("self serve free tier")


# --- catalog slug matching (stage 2) -----------------------------------------

def test_norm_collapses_separators():
    assert _norm("linkedin-ads") == _norm("linkedin_ads") == "linkedinads"
    assert _norm("bright-data") == "brightdata"
    assert _norm("Help Scout") == _norm("help_scout")


# --- queue lane assignment (stage 7) -----------------------------------------

def _rec(**over):
    d = dict(slug="x", name="X", category="C", one_liner="o", auth_schemes=["API_KEY"],
             gate="self_serve", api_style=["REST"], api_breadth="moderate", mcp="none",
             buildability="build_now", confidence="medium", status="verified",
             needs_human=False, sources_agreeing=0)
    d.update(over)
    return AppRecord(**d)


def test_lane_ship_this_week():
    assert _lane(_rec(buildability="build_now", gate="self_serve")) == "ship_this_week"


def test_lane_outreach_for_partner_gate():
    r = _rec(buildability="build_gated", gate="partner_gated", auth_schemes=["OAUTH2"])
    assert _lane(r) == "start_outreach_now"


def test_lane_not_a_toolkit():
    assert _lane(_rec(buildability="not_a_toolkit", gate="no_api",
                      auth_schemes=["NO_AUTH"])) == "not_a_toolkit"


def test_lane_park_on_needs_human():
    r = _rec(auth_schemes=["UNKNOWN"], gate="unknown", needs_human=True, confidence="low")
    assert _lane(r) == "park"


def test_effort_oauth_bumps_narrow_to_medium():
    assert _effort(_rec(api_breadth="narrow", auth_schemes=["OAUTH2"])) == "M"
    assert _effort(_rec(api_breadth="narrow", auth_schemes=["API_KEY"])) == "S"
