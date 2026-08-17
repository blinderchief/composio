"""Single source of truth for the whole pipeline (CLAUDE.md §5, PRD §4).

The page, the queue, and the scorer all import from here. Field values are enums
wherever a taxonomy exists, never free text. Auth-scheme values are Composio's own
vocabulary, verbatim (CLAUDE.md §3).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AuthScheme(str, Enum):
    """Mirrors Composio's auth-config taxonomy. Do not extend without reason."""

    OAUTH2 = "OAUTH2"
    API_KEY = "API_KEY"
    BEARER_TOKEN = "BEARER_TOKEN"
    BASIC = "BASIC"
    NO_AUTH = "NO_AUTH"
    UNKNOWN = "UNKNOWN"


class AccessGate(str, Enum):
    """*How* a developer is blocked. The specificity here is the whole point of the
    exercise — 'gated' is not an answer; 'partner_gated, apply here, 4-16 weeks' is."""

    SELF_SERVE = "self_serve"          # free or trial creds, no human in the loop
    PAID_TIER = "paid_tier"            # money unlocks it, no approval needed
    ADMIN_APPROVAL = "admin_approval"  # a workspace admin must enable it
    BUSINESS_VERIFY = "business_verify"  # entity/identity verification (Meta, Amazon)
    PARTNER_GATED = "partner_gated"    # partner program application + review
    CONTACT_SALES = "contact_sales"    # no published path; talk to a human
    NO_API = "no_api"                  # not an app with an API at all
    UNKNOWN = "unknown"


class Buildability(str, Enum):
    BUILD_NOW = "build_now"          # self-serve creds, documented API -> toolkit today
    BUILD_GATED = "build_gated"      # buildable once a gate clears
    NEEDS_DEAL = "needs_deal"        # requires partnership/commercial conversation
    NOT_A_TOOLKIT = "not_a_toolkit"  # CLI/OSS/no API — wrong shape for a toolkit
    UNKNOWN = "unknown"


Status = Literal["verified", "inferred", "unknown"]
Confidence = Literal["high", "medium", "low"]
ApiStyle = Literal["REST", "GraphQL", "SOAP", "gRPC", "WebSocket", "CLI", "SDK_ONLY", "NONE"]
ApiBreadth = Literal["narrow", "moderate", "broad", "very_broad", "none"]
McpStatus = Literal["first_party", "community", "none", "unknown"]
CatalogStatus = Literal["exists", "absent", "unknown"]
SupportsField = Literal["auth", "gate", "api_surface", "mcp", "identity"]


class Evidence(BaseModel):
    """A citation. `doc_id` MUST exist in this run's fetchlog.jsonl — enforced by
    constraining the field to a per-app enum at extraction time (extract.py) and by
    re-validating against the fetch log in verify.py. `quote_span` must appear verbatim
    in the cached content; that is the claim-support check."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    url: str
    quote_span: str = Field(max_length=240)  # <= ~25 words, verbatim from fetched content
    supports: SupportsField


class SeedApp(BaseModel):
    """A row of data/seed/apps.yaml. The 100, handed to the pipeline, not re-derived."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    category: str
    hint_url: str
    is_trap: bool = False  # flagged in the assignment/GOLD_SET as ambiguous or wrong-shape
    trap_note: str | None = None


class AppRecord(BaseModel):
    """One researched app. The atomic unit of pass1.jsonl / pass2.jsonl / data.json."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    category: str
    one_liner: str = Field(max_length=140)

    auth_schemes: list[AuthScheme]
    auth_notes: str | None = None  # e.g. "HMAC-SHA256 request signing on top of API key"

    gate: AccessGate
    gate_detail: str | None = None       # what specifically blocks a developer
    gate_apply_url: str | None = None    # where you actually apply — the ops payload
    gate_requires: list[str] = []        # ["company page", "use case", "seller account"]
    gate_eta_days: tuple[int, int] | None = None  # realistic range; sourced or inferred

    api_style: list[ApiStyle]
    api_breadth: ApiBreadth
    api_docs_url: str | None = None

    mcp: McpStatus
    mcp_url: str | None = None

    composio_toolkit: CatalogStatus = "unknown"  # from the catalog diff (catalog.py)
    composio_toolkit_slug: str | None = None

    buildability: Buildability
    primary_blocker: str | None = None

    evidence: list[Evidence] = []
    confidence: Confidence
    status: Status
    needs_human: bool
    ambiguity_note: str | None = None  # name collisions, unresolvable entities

    # provenance
    pass_no: int = 1
    sources_agreeing: int = 0
    run_id: str = ""

    @model_validator(mode="after")
    def _enforce_confidence_floor(self) -> AppRecord:
        """CLAUDE.md §3: `confidence: high` requires >= 2 independent corroborating
        sources. Enforced in code, not just the prompt. This is a hard invariant: a
        record that violates it is a bug, not a style nit."""
        if self.confidence == "high" and self.sources_agreeing < 2:
            raise ValueError(
                f"{self.slug}: confidence=high needs >=2 sources_agreeing, "
                f"got {self.sources_agreeing}"
            )
        return self

    @model_validator(mode="after")
    def _unknown_needs_human(self) -> AppRecord:
        """An UNKNOWN on auth or gate is a routed work item, so it must be flagged."""
        if (
            AuthScheme.UNKNOWN in self.auth_schemes or self.gate == AccessGate.UNKNOWN
        ) and not self.needs_human:
            raise ValueError(f"{self.slug}: UNKNOWN auth/gate must set needs_human=True")
        return self


# --- derived: the operational payload (PRD §4) -------------------------------

QueueLane = Literal[
    "ship_this_week", "unblock_then_ship", "start_outreach_now", "park", "not_a_toolkit"
]


class QueueItem(BaseModel):
    """Derived from an AppRecord by queue.py — never extracted by the model."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    category: str
    lane: QueueLane
    effort: Literal["S", "M", "L"]                 # from api_breadth + auth complexity
    lead_time_days: tuple[int, int] | None         # from gate_eta_days
    value_signal: str                              # why it matters (net-new? breadth?)
    next_action: str                               # one imperative sentence a human executes
    owner_hint: Literal["eng", "ops", "bd"]
    rank: int                                       # position in the global build order
