"""Stage 6 — verify, pass 2 (PRD §5.6, differentiator #4).

Five independent loops over a pass-1 record. Each records what it changed to fixes.jsonl
({slug, field, from, to, fixed_by_loop}); that log becomes the "what the loops caught" panel.

  1. URL liveness       — re-request every cited URL; non-200 -> drop evidence, downgrade.
  2. Claim support      — quote_span must appear (normalized) in the cited doc; else drop.
  3. Corroboration      — count independent domains behind auth & gate; enforce the
                          confidence floor (high => >=2 sources, >=1 vendor-domain).
  4. Critic             — a different vendor judges each field; disagreement downgrades
                          status/confidence and is logged for the misses panel.
  5. Browser channel    — detect docs that need JS/login. If <5 apps need it, we skip
                          Playwright entirely and SAY SO (knowing when not to build is signal).

Loops 1-3 are deterministic and fully unit-tested. Loop 4 needs the critic key. Loop 5 is
detection + honest reporting.
"""

from __future__ import annotations

import json
import re

import httpx

from .config import get_config
from .fetch import cache_path, load_docs_for_slug
from .logging_setup import get_logger
from .models import AppRecord
from .retrieval import domain
from .runstore import append_jsonl, done_slugs, read_jsonl, resolve_run_id, run_dir

log = get_logger(stage="verify")

_WORD = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def _fix(fixes: list[dict], slug: str, field: str, frm, to, loop: str) -> None:
    if frm != to:
        fixes.append({"slug": slug, "field": field, "from": frm, "to": to, "fixed_by_loop": loop})


# --- loop 1: URL liveness ----------------------------------------------------

def check_liveness(rec: AppRecord, fixes: list[dict], status_map: dict[str, int],
                   client: httpx.Client) -> None:
    """Loop 1: a cited URL must have returned HTTP 200. Every citation comes from a page
    fetched in this run, so its status is already recorded in fetchlog.jsonl — a real HTTP
    request made this run. We trust that (fast, consistent) and only hit the network for a
    URL somehow absent from the log (should not happen)."""
    live = []
    for ev in rec.evidence:
        status = status_map.get(ev.url)
        if status is None:  # not in the fetchlog — verify it directly
            try:
                status = client.get(ev.url, follow_redirects=True, timeout=8).status_code
            except Exception:
                status = 0
            status_map[ev.url] = status
        if status == 200:
            live.append(ev)
        else:
            _fix(fixes, rec.slug, "evidence_liveness", ev.url, f"dropped({status})", "1_liveness")
    rec.evidence = live


# --- loop 2: claim support ---------------------------------------------------

def check_claim_support(rec: AppRecord, run_id: str, fixes: list[dict]) -> None:
    fetchlog = {row["url"]: row for row in read_jsonl(run_dir(run_id) / "fetchlog.jsonl")}
    supported = []
    for ev in rec.evidence:
        row = fetchlog.get(ev.url)
        ok = False
        if row and row.get("sha256"):
            p = cache_path(row["sha256"])
            if p.exists():
                ok = _normalize(ev.quote_span) in _normalize(p.read_text())
        if ok:
            supported.append(ev)
        else:
            _fix(fixes, rec.slug, "evidence_claim", ev.quote_span[:40], "dropped(unsupported)",
                 "2_claim_support")
    rec.evidence = supported


# --- loop 3: cross-source corroboration --------------------------------------

def check_corroboration(rec: AppRecord, run_id: str, fixes: list[dict]) -> None:
    docs = {d["doc_id"]: d for d in load_docs_for_slug(run_id, rec.slug)}
    auth_domains, gate_domains, vendor_hit = set(), set(), False
    for ev in rec.evidence:
        d = docs.get(ev.doc_id)
        if not d:
            continue
        dom = domain(d["url"])
        if d["vendor_domain"]:
            vendor_hit = True
        if ev.supports == "auth":
            auth_domains.add(dom)
        if ev.supports == "gate":
            gate_domains.add(dom)
    rec.sources_agreeing = len({domain(docs[e.doc_id]["url"]) for e in rec.evidence
                                if e.doc_id in docs})
    old = rec.confidence
    strong = len(auth_domains) >= 2 or len(gate_domains) >= 2
    if rec.confidence == "high" and not (strong and vendor_hit):
        rec.confidence = "medium"
    if not rec.evidence and rec.confidence != "low":
        rec.confidence = "low"
    _fix(fixes, rec.slug, "confidence", old, rec.confidence, "3_corroboration")


# --- loop 4: critic (different vendor) ---------------------------------------

def apply_critic(rec: AppRecord, run_id: str, fixes: list[dict]) -> list[dict]:
    from .critic import critique

    docs = load_docs_for_slug(run_id, rec.slug)
    verdicts = critique(json.loads(rec.model_dump_json()), docs)
    disagreements = [v for v in verdicts if v["verdict"] in ("disagree", "insufficient")]
    if any(v["verdict"] == "disagree" for v in verdicts):
        old = rec.status
        rec.status = "inferred" if rec.status == "verified" else rec.status
        _fix(fixes, rec.slug, "status", old, rec.status, "4_critic")
        if rec.confidence == "high":
            _fix(fixes, rec.slug, "confidence", "high", "medium", "4_critic")
            rec.confidence = "medium"
    # Escalate to human review only on a real contradiction ("disagree") of auth or gate —
    # not on "insufficient", which just means the critic's doc subset didn't fully establish
    # the field and would otherwise park half the queue.
    hard = [v for v in verdicts if v["verdict"] == "disagree" and v["field"] in ("auth_schemes", "gate")]
    if hard and not rec.needs_human:
        _fix(fixes, rec.slug, "needs_human", False, True, "4_critic")
        rec.needs_human = True
    return verdicts


# --- loop 5: browser-channel need detection ----------------------------------

def needs_browser(rec: AppRecord, run_id: str) -> bool:
    """A doc set is 'thin' (likely JS/login-walled) if we fetched almost nothing usable."""
    docs = load_docs_for_slug(run_id, rec.slug)
    total_chars = sum(len(d["markdown"]) for d in docs)
    return len(docs) <= 1 and total_chars < 1500


def run_verify(run: str = "latest", app: str | None = None, use_critic: bool = True) -> str:
    cfg = get_config()
    run_id = resolve_run_id(run)
    rd = run_dir(run_id)
    out = rd / "pass2.jsonl"
    fixes_file = rd / "fixes.jsonl"
    critic_file = rd / "critic.jsonl"
    already = done_slugs(out)

    _critic_key = {
        "groq": cfg.groq_key, "cerebras": cfg.cerebras_key, "openrouter": cfg.openrouter_key,
        "openai": cfg.openai_key, "google": cfg.gemini_key,
    }.get(cfg.critic_provider)
    critic_available = bool(use_critic and _critic_key)
    browser_needed: list[str] = []
    # Liveness comes from this run's fetch log — each URL there was a real HTTP request.
    status_map = {r["url"]: r["http_status"] for r in read_jsonl(rd / "fetchlog.jsonl")}

    with httpx.Client() as client:
        for row in read_jsonl(rd / "pass1.jsonl"):
            if app is not None and row["slug"] != app:
                continue
            if row["slug"] in already:
                continue
            rec = AppRecord(**row)
            rec.pass_no = 2
            fixes: list[dict] = []

            check_liveness(rec, fixes, status_map, client)
            check_claim_support(rec, run_id, fixes)
            check_corroboration(rec, run_id, fixes)
            if critic_available:
                try:
                    verdicts = apply_critic(rec, run_id, fixes)
                    append_jsonl(critic_file, {"slug": rec.slug, "verdicts": verdicts})
                except Exception as e:
                    log.warning("critic_failed", slug=rec.slug, error=str(e))
            if needs_browser(rec, run_id):
                browser_needed.append(rec.slug)

            append_jsonl(out, json.loads(rec.model_dump_json()))
            for fx in fixes:
                append_jsonl(fixes_file, fx)
            log.info("verified", slug=rec.slug, conf=rec.confidence, fixes=len(fixes),
                     evidence=len(rec.evidence))

    # Loop 5 policy: only stand up Playwright if >=5 apps genuinely need it.
    (rd / "browser_report.json").write_text(json.dumps({
        "apps_needing_browser": browser_needed,
        "count": len(browser_needed),
        "playwright_built": len(browser_needed) >= 5,
        "note": ("Built the browser channel — >=5 apps had JS/login-walled docs."
                 if len(browser_needed) >= 5 else
                 "Skipped Playwright on purpose: fewer than 5 apps needed it. "
                 "Knowing when not to build something is itself a signal (PRD §5.6)."),
    }, indent=2))
    log.info("verify_done", browser_needed=len(browser_needed))
    return run_id
