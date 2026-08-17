"""Stage 7 — the build-and-outreach queue (PRD §4, differentiator #1).

Derived, never extracted. Turns each verified record into an ops work item with a lane,
an effort size, a lead time, and one imperative next_action a human can execute. The
global order encodes the project thesis: outreach items rank FIRST because their lead
times are long — you have to start them today even if they rank lower by value.
"""

from __future__ import annotations

import json

from .logging_setup import get_logger
from .models import AppRecord, QueueItem
from .runstore import read_jsonl, resolve_run_id, run_dir

log = get_logger(stage="queue")

# Rank lanes by "when you must start", not by value. Long-lead outreach starts now.
LANE_ORDER = {
    "start_outreach_now": 0,
    "ship_this_week": 1,
    "unblock_then_ship": 2,
    "park": 3,
    "not_a_toolkit": 4,
}

_OUTREACH_GATES = {"partner_gated", "business_verify", "contact_sales"}
_UNBLOCK_GATES = {"admin_approval", "paid_tier"}


def _lane(rec: AppRecord) -> str:
    if rec.buildability == "not_a_toolkit" or rec.gate == "no_api":
        return "not_a_toolkit"
    if rec.needs_human or rec.buildability == "unknown" or rec.confidence == "low":
        return "park"
    if rec.buildability == "build_now" or rec.gate == "self_serve":
        return "ship_this_week"
    if rec.buildability == "needs_deal" or rec.gate in _OUTREACH_GATES:
        return "start_outreach_now"
    if rec.gate in _UNBLOCK_GATES:
        return "unblock_then_ship"
    return "park"


def _effort(rec: AppRecord) -> str:
    base = {"very_broad": "L", "broad": "L", "moderate": "M", "narrow": "S", "none": "S"}
    size = base.get(rec.api_breadth, "M")
    if "OAUTH2" in [a.value if hasattr(a, "value") else a for a in rec.auth_schemes] and size == "S":
        size = "M"  # OAuth flows add integration cost even for a narrow API
    return size


def _owner(rec: AppRecord, lane: str) -> str:
    if lane == "start_outreach_now":
        return "bd"
    if lane in ("park", "not_a_toolkit"):
        return "ops"
    return "eng"


def _value_signal(rec: AppRecord) -> str:
    bits = []
    if rec.composio_toolkit == "absent":
        bits.append("net-new to catalog")
    elif rec.composio_toolkit == "exists":
        bits.append("already a toolkit")
    if rec.api_breadth in ("broad", "very_broad"):
        bits.append(f"{rec.api_breadth} API")
    if rec.mcp == "first_party":
        bits.append("has first-party MCP (wrap vs build)")
    bits.append(rec.category)
    return "; ".join(bits)


def _next_action(rec: AppRecord, lane: str) -> str:
    eta = f" Expect {rec.gate_eta_days[0]}–{rec.gate_eta_days[1]} days." if rec.gate_eta_days else ""
    if lane == "ship_this_week":
        return f"Create free/trial credentials and build the {rec.name} toolkit today; no gate."
    if lane == "start_outreach_now":
        where = f" Apply: {rec.gate_apply_url}." if rec.gate_apply_url else ""
        reqs = f" Needs: {', '.join(rec.gate_requires)}." if rec.gate_requires else ""
        return f"Start the {rec.name} approval now — {rec.gate.value}.{where}{reqs}{eta}"
    if lane == "unblock_then_ship":
        return f"Clear the {rec.gate.value} gate for {rec.name}, then build.{eta}"
    if lane == "park":
        note = rec.ambiguity_note or rec.primary_blocker or "insufficient evidence"
        return f"Hold {rec.name}: {note}. Confirm with a human before any work."
    return f"Do not build {rec.name}: {rec.primary_blocker or 'no API / wrong shape for a toolkit'}."


def build_queue(run: str = "latest") -> list[dict]:
    run_id = resolve_run_id(run)
    src = run_dir(run_id) / "pass2.jsonl"
    if not src.exists():
        src = run_dir(run_id) / "pass1.jsonl"
    items: list[QueueItem] = []
    for row in read_jsonl(src):
        rec = AppRecord(**row)
        lane = _lane(rec)
        items.append(QueueItem(
            slug=rec.slug, name=rec.name, category=rec.category, lane=lane,
            effort=_effort(rec), lead_time_days=rec.gate_eta_days,
            value_signal=_value_signal(rec), next_action=_next_action(rec, lane),
            owner_hint=_owner(rec, lane), rank=0,
        ))

    def sort_key(it: QueueItem):
        lead = -(it.lead_time_days[1]) if it.lead_time_days else 0  # longest lead first
        return (LANE_ORDER[it.lane], lead, it.name.lower())

    items.sort(key=sort_key)
    for i, it in enumerate(items, 1):
        it.rank = i

    out = [json.loads(it.model_dump_json()) for it in items]
    dest = run_dir(run_id) / "queue.json"
    dest.write_text(json.dumps(out, indent=2))
    log.info("queue_built", n=len(out))
    return out
