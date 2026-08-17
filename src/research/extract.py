"""Stage 5 — extract, pass 1 (PRD §5.5).

One LLM call per app with the fetched Markdown as context and structured JSON output.
This run uses Gemini (response_schema); an Anthropic tool_use path is kept as an alt
provider. Either way, `Evidence.doc_id` is constrained to a per-app enum of fetched
doc_ids, AND every citation is re-checked against the fetched set here — so a hallucinated
source is dropped by construction even if the model ignores the enum (CLAUDE.md §3).

Provenance (run_id, pass_no) and the catalog verdict are injected by us, not the model.
"""

from __future__ import annotations

import json

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .catalog import catalog_map
from .config import get_config
from .fetch import load_docs_for_slug
from .logging_setup import get_logger
from .models import AppRecord
from .runstore import append_jsonl, done_slugs, read_jsonl, resolve_run_id, run_dir
from .seed import seed_by_slug

log = get_logger(stage="extract")

MAX_DOC_CHARS = 4_500  # per-doc truncation to keep the context bounded, fast, and cheap
MAX_DOCS = 3           # cap docs per app so free-tier token budgets are respected

AUTH_ENUM = ["OAUTH2", "API_KEY", "BEARER_TOKEN", "BASIC", "NO_AUTH", "UNKNOWN"]
GATE_ENUM = ["self_serve", "paid_tier", "admin_approval", "business_verify",
             "partner_gated", "contact_sales", "no_api", "unknown"]
STYLE_ENUM = ["REST", "GraphQL", "SOAP", "gRPC", "WebSocket", "CLI", "SDK_ONLY", "NONE"]
BREADTH_ENUM = ["narrow", "moderate", "broad", "very_broad", "none"]
MCP_ENUM = ["first_party", "community", "none", "unknown"]
BUILD_ENUM = ["build_now", "build_gated", "needs_deal", "not_a_toolkit", "unknown"]
CONF_ENUM = ["high", "medium", "low"]
STATUS_ENUM = ["verified", "inferred", "unknown"]
SUPPORTS_ENUM = ["auth", "gate", "api_surface", "mcp", "identity"]

SYSTEM = """You research SaaS apps for an agent-toolkit vendor (Composio). For ONE app, \
read the provided vendor documentation excerpts and output the JSON record.

Hard rules:
- Use ONLY the provided documents. Every evidence.doc_id must be one of the given ids.
- quote_span must be copied VERBATIM from that document, <= 25 words. Do not paraphrase.
- Prefer unknown over a plausible guess. A correct "unknown" beats a confident wrong answer.
- auth_schemes use Composio's exact vocabulary: OAUTH2, API_KEY, BEARER_TOKEN, BASIC, \
NO_AUTH, UNKNOWN.
- gate is HOW a developer is blocked: self_serve, paid_tier, admin_approval, \
business_verify, partner_gated, contact_sales, no_api, unknown. Be specific in gate_detail, \
gate_apply_url, gate_requires, gate_eta_days (a [min,max] day range or null).
- If the entity is ambiguous or name-colliding, set needs_human=true and explain in \
ambiguity_note. Do not pick one and hide the collision.
- confidence=high ONLY if >=2 documents (one from the vendor's own domain) agree. Otherwise \
medium or low. If auth or gate is unknown, set needs_human=true.
- Provide an evidence entry for auth and for gate when you can."""


# --- schema (Gemini response_schema flavour: uppercase types, `nullable`) ----

def _gemini_schema(doc_ids: list[str]) -> dict:
    doc_enum = doc_ids or ["__none__"]
    S = lambda **k: {"type": "STRING", **k}  # noqa: E731
    return {
        "type": "OBJECT",
        "properties": {
            "one_liner": S(),
            "auth_schemes": {"type": "ARRAY", "items": S(enum=AUTH_ENUM)},
            "auth_notes": S(nullable=True),
            "gate": S(enum=GATE_ENUM),
            "gate_detail": S(nullable=True),
            "gate_apply_url": S(nullable=True),
            "gate_requires": {"type": "ARRAY", "items": S()},
            "gate_eta_days": {"type": "ARRAY", "items": {"type": "INTEGER"}, "nullable": True},
            "api_style": {"type": "ARRAY", "items": S(enum=STYLE_ENUM)},
            "api_breadth": S(enum=BREADTH_ENUM),
            "api_docs_url": S(nullable=True),
            "mcp": S(enum=MCP_ENUM),
            "mcp_url": S(nullable=True),
            "buildability": S(enum=BUILD_ENUM),
            "primary_blocker": S(nullable=True),
            "confidence": S(enum=CONF_ENUM),
            "status": S(enum=STATUS_ENUM),
            "needs_human": {"type": "BOOLEAN"},
            "ambiguity_note": S(nullable=True),
            "evidence": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "doc_id": S(enum=doc_enum),
                        "quote_span": S(),
                        "supports": S(enum=SUPPORTS_ENUM),
                    },
                    "required": ["doc_id", "quote_span", "supports"],
                },
            },
        },
        "required": ["one_liner", "auth_schemes", "gate", "api_style", "api_breadth",
                     "mcp", "buildability", "confidence", "status", "needs_human", "evidence"],
    }


def _context(app_name: str, category: str, hint: str, docs: list[dict],
             is_trap: bool = False, trap_note: str | None = None) -> str:
    parts = [f"APP: {app_name}\nCATEGORY: {category}\nHINT URL: {hint}\n"]
    # Use what the seed already knows. A github.com hint usually means a local tool/library,
    # not a hosted API — check for a real service before assigning auth. A trap flag means the
    # name may collide with a different company, so the fetched docs might describe the wrong one.
    if "github.com" in hint:
        parts.append(
            "\n[NOTE: the hint URL is a GitHub repo. This is often a CLI or library that runs "
            "locally with NO hosted API and NO credentials. If so: auth_schemes=[NO_AUTH], "
            "gate=no_api, buildability=not_a_toolkit. Do not invent an API.]")
    if is_trap:
        parts.append(
            f"\n[CAUTION: this entity is flagged as ambiguous/name-colliding ({trap_note}). "
            "The fetched documents may describe a DIFFERENT company with the same name. If you "
            "cannot confirm from the hint domain that the docs are about THIS entity, abstain: "
            "set the uncertain fields to unknown/UNKNOWN, needs_human=true, and explain the "
            "collision in ambiguity_note. A documented abstention is the correct answer here.]")
    if not docs:
        parts.append("\n[No documents were fetched. You must abstain: mark unknown.]")
    # Vendor-domain docs first, then cap — they carry the authoritative answer.
    docs = sorted(docs, key=lambda d: not d["vendor_domain"])[:MAX_DOCS]
    for d in docs:
        tag = "VENDOR DOMAIN" if d["vendor_domain"] else "third-party"
        parts.append(
            f"\n===== DOCUMENT doc_id={d['doc_id']} ({tag}) url={d['url']} =====\n"
            + d["markdown"][:MAX_DOC_CHARS]
        )
    return "\n".join(parts)


def _to_record(tool_input: dict, app, run_id: str, docs: list[dict], cat: dict) -> AppRecord:
    id_to_url = {d["doc_id"]: d["url"] for d in docs}
    vendor_docs = {d["doc_id"] for d in docs if d["vendor_domain"]}
    evidence = [
        {**e, "url": id_to_url.get(e["doc_id"], ""), "quote_span": e["quote_span"][:240]}
        for e in tool_input.get("evidence", [])
        if e.get("doc_id") in id_to_url  # DROP any citation not in the fetched set
    ]
    gate_eta = tool_input.get("gate_eta_days")
    eta = tuple(gate_eta) if gate_eta and len(gate_eta) == 2 else None

    # Enforce the confidence floor BEFORE construction, since the model's self-reported
    # confidence is untrusted and the schema validator rejects high-without-two-sources.
    cited = {e["doc_id"] for e in evidence}
    sources_agreeing = len(cited)
    confidence = tool_input["confidence"]
    if confidence == "high" and not (len(cited) >= 2 and cited & vendor_docs):
        confidence = "medium"

    return AppRecord(
        slug=app.slug, name=app.name, category=app.category,
        one_liner=tool_input["one_liner"][:140],
        auth_schemes=tool_input["auth_schemes"] or ["UNKNOWN"],
        auth_notes=tool_input.get("auth_notes"),
        gate=tool_input["gate"], gate_detail=tool_input.get("gate_detail"),
        gate_apply_url=tool_input.get("gate_apply_url"),
        gate_requires=tool_input.get("gate_requires") or [],
        gate_eta_days=eta,
        api_style=tool_input.get("api_style") or [],
        api_breadth=tool_input["api_breadth"],
        api_docs_url=tool_input.get("api_docs_url"),
        mcp=tool_input["mcp"], mcp_url=tool_input.get("mcp_url"),
        composio_toolkit=cat.get("composio_toolkit", "unknown"),
        composio_toolkit_slug=cat.get("composio_toolkit_slug"),
        buildability=tool_input["buildability"],
        primary_blocker=tool_input.get("primary_blocker"),
        evidence=evidence,
        confidence=confidence, status=tool_input["status"],
        needs_human=tool_input["needs_human"],
        ambiguity_note=tool_input.get("ambiguity_note"),
        pass_no=1, sources_agreeing=sources_agreeing, run_id=run_id,
    )


# --- provider calls ----------------------------------------------------------

_TRANSIENT = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded", "high demand")


def _is_transient(e: Exception) -> bool:
    return any(t in str(e) for t in _TRANSIENT)


# Fallback chain across models with independent free-tier quota buckets. The `-flash-lite`
# variants have their own daily quota, so they keep working after the `-flash` bucket is spent.
FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-flash-lite-latest",
                   "gemini-2.5-flash", "gemini-3.5-flash"]


@retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential(multiplier=2, min=2, max=8),
    stop=stop_after_attempt(2),  # fail fast per model; the model-fallback chain handles the rest
    reraise=True,
)
def _gemini_once(cfg, model: str, ctx: str, doc_ids: list[str], retry_note: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=cfg.require("gemini_key", "GEMINI_API_KEY"))
    resp = client.models.generate_content(
        model=model,
        contents=ctx + (f"\n\n[Fix: {retry_note}]" if retry_note else ""),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            response_schema=_gemini_schema(doc_ids),
            temperature=0,
        ),
    )
    return json.loads(resp.text)


def _call_gemini(cfg, ctx: str, doc_ids: list[str], retry_note: str = "") -> dict:
    # Only ever send Gemini models to the Gemini endpoint. When the extractor is configured
    # as an OSS model (Cerebras/Groq) and the chain falls through to Gemini, cfg.extract_model
    # is not a Gemini id, so fall back to the Gemini model list instead of 404-ing.
    primary = [cfg.extract_model] if cfg.extract_model.startswith("gemini") else []
    models = primary + [m for m in FALLBACK_MODELS if m not in primary]
    last: Exception | None = None
    for m in models:
        try:
            return _gemini_once(cfg, m, ctx, doc_ids, retry_note)
        except Exception as e:  # exhausted retries on this model -> try the next one
            last = e
            if _is_transient(e):
                log.warning("model_fallback", model=m, error=str(e)[:80])
                continue
            raise
    raise last  # type: ignore[misc]


def _call_anthropic(cfg, ctx: str, doc_ids: list[str], retry_note: str = "") -> dict:
    from anthropic import Anthropic

    client = Anthropic(api_key=cfg.require("anthropic_key", "ANTHROPIC_API_KEY"))
    schema = _gemini_schema(doc_ids)  # shape is compatible enough for a tool input_schema
    tool = {"name": "record_app", "description": "Record the researched facts.",
            "input_schema": {**schema, "type": "object"}}
    msg = client.messages.create(
        model=cfg.extract_model, max_tokens=2000, system=SYSTEM,
        tools=[tool], tool_choice={"type": "tool", "name": "record_app"},
        messages=[{"role": "user", "content": ctx + (f"\n\n[Fix: {retry_note}]" if retry_note else "")}],
    )
    block = next((b for b in msg.content if b.type == "tool_use"), None)
    if block is None:
        raise RuntimeError("no tool_use block")
    return block.input


_OAI_EXTRACT = {  # provider -> (base_url, key_attr, key_name)
    "cerebras": ("https://api.cerebras.ai/v1", "cerebras_key", "CEREBRAS_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "groq_key", "GROQ_API_KEY"),
}


def _json_schema_text(doc_ids: list[str]) -> str:
    return (
        "Output ONLY a JSON object with these keys (no prose):\n"
        f'one_liner (str<=140), auth_schemes (array of {AUTH_ENUM}), auth_notes (str|null), '
        f'gate (one of {GATE_ENUM}), gate_detail (str|null), gate_apply_url (str|null), '
        'gate_requires (array of str), gate_eta_days ([min,max] ints | null), '
        f'api_style (array of {STYLE_ENUM}), api_breadth (one of {BREADTH_ENUM}), '
        f'api_docs_url (str|null), mcp (one of {MCP_ENUM}), mcp_url (str|null), '
        f'buildability (one of {BUILD_ENUM}), primary_blocker (str|null), '
        f'confidence (one of {CONF_ENUM}), status (one of {STATUS_ENUM}), '
        'needs_human (bool), ambiguity_note (str|null), '
        'evidence (array of {doc_id, quote_span<=25 words verbatim, supports one of '
        f'{SUPPORTS_ENUM}}}). Each evidence.doc_id MUST be one of: {doc_ids or ["__none__"]}.'
    )


def _call_oai_compatible(cfg, provider: str, model: str, ctx: str, doc_ids: list[str],
                         retry_note: str = "") -> dict:
    from openai import OpenAI

    base_url, key_attr, key_name = _OAI_EXTRACT[provider]
    client = OpenAI(api_key=cfg.require(key_attr, key_name), base_url=base_url)
    resp = client.chat.completions.create(
        model=model, temperature=0, max_tokens=3000,
        messages=[
            {"role": "system", "content": SYSTEM + "\n\n" + _json_schema_text(doc_ids)},
            {"role": "user", "content": ctx + (f"\n\n[Fix: {retry_note}]" if retry_note else "")},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def _provider_chain(cfg) -> list:
    """(name, callable) chain. The configured extractor first, then the others as fallbacks,
    so a single provider's daily quota wall doesn't stall the run. gpt-oss-120b on both
    Cerebras and Groq keeps the model consistent; Gemini is the last resort."""
    oss = [
        ("cerebras", lambda c, x, ids, n="": _call_oai_compatible(c, "cerebras", "gpt-oss-120b", x, ids, n)),
        ("groq", lambda c, x, ids, n="": _call_oai_compatible(c, "groq", "openai/gpt-oss-120b", x, ids, n)),
        ("gemini", _call_gemini),
    ]
    if cfg.extract_provider == "groq":
        oss = [oss[1], oss[0], oss[2]]
    elif cfg.extract_provider in ("google", "anthropic"):
        oss = [("gemini", _call_gemini), oss[0], oss[1]]
    return oss


def extract_app(cfg, app, run_id: str) -> AppRecord:
    docs = load_docs_for_slug(run_id, app.slug)
    cat = catalog_map().get(app.slug, {})
    doc_ids = [d["doc_id"] for d in docs]
    ctx = _context(app.name, app.category, app.hint_url, docs,
                   getattr(app, "is_trap", False), getattr(app, "trap_note", None))
    chain = _provider_chain(cfg)

    last_err = ""
    for attempt in range(2):  # validate-and-retry net (CLAUDE.md §4)
        raw = None
        for name, call in chain:  # provider fallback: skip a provider that's out of quota
            try:
                raw = call(cfg, ctx, doc_ids, last_err)
                break
            except Exception as e:
                last_err = str(e)[:160]
                log.warning("provider_fallback", slug=app.slug, provider=name, error=last_err)
        try:
            if raw is None:
                raise RuntimeError(last_err or "all providers failed")
            rec = _to_record(raw, app, run_id, docs, cat)
        except Exception as e:
            last_err = str(e)[:200]
            log.warning("extract_retry", slug=app.slug, attempt=attempt, error=last_err)
            continue
        return rec  # confidence floor already enforced in _to_record
    raise RuntimeError(f"extract failed for {app.slug}: {last_err}")


def run_extract(run: str = "latest", app: str | None = None, refresh: bool = False) -> str:
    cfg = get_config()
    run_id = resolve_run_id(run)
    out = run_dir(run_id) / "pass1.jsonl"
    already = set() if refresh else done_slugs(out)
    seeds = seed_by_slug()
    fetched = {row["slug"] for row in read_jsonl(run_dir(run_id) / "fetchlog.jsonl")}
    targets = [s for s in seeds.values() if (app is None or s.slug == app)]
    for a in targets:
        if a.slug in already:
            continue
        if a.slug not in fetched:
            log.warning("no_fetch_skip", slug=a.slug)
            continue
        rec = extract_app(cfg, a, run_id)
        append_jsonl(out, json.loads(rec.model_dump_json()))
        log.info("extracted", slug=a.slug, conf=rec.confidence, gate=rec.gate.value,
                 auth=[x.value for x in rec.auth_schemes], needs_human=rec.needs_human)
    return run_id
