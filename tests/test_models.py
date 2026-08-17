"""The schema invariants are correctness rules, not style (CLAUDE.md §3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research.models import AppRecord


def _base(**over):
    d = dict(
        slug="x", name="X", category="C", one_liner="one",
        auth_schemes=["API_KEY"], gate="self_serve", api_style=["REST"],
        api_breadth="moderate", mcp="none", buildability="build_now",
        confidence="medium", status="verified", needs_human=False,
        sources_agreeing=0,
    )
    d.update(over)
    return d


def test_high_confidence_requires_two_sources():
    with pytest.raises(ValidationError):
        AppRecord(**_base(confidence="high", sources_agreeing=1))
    # two sources is fine
    AppRecord(**_base(confidence="high", sources_agreeing=2))


def test_unknown_auth_must_flag_human():
    with pytest.raises(ValidationError):
        AppRecord(**_base(auth_schemes=["UNKNOWN"], needs_human=False))
    AppRecord(**_base(auth_schemes=["UNKNOWN"], gate="unknown", needs_human=True))
