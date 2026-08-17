"""Stage 4 — fetch (PRD §5.4). The anti-hallucination substrate.

Scrape each candidate URL to Markdown, cache by content hash, and write fetchlog.jsonl:
{doc_id, url, http_status, sha256, fetched_at, bytes, source_tool}. `doc_id` is a short,
stable hash of the URL — the ONLY thing extraction is allowed to cite. Nothing downstream
may reference a URL that is not in this log with HTTP 200 (CLAUDE.md §3).

Idempotent: a URL already in the cache is not re-fetched unless refresh=True.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .config import CACHE_DIR
from .logging_setup import get_logger
from .retrieval import scrape
from .runstore import append_jsonl, read_jsonl, resolve_run_id, run_dir
from .seed import load_seed

log = get_logger(stage="fetch")


def doc_id_for(url: str) -> str:
    return "d" + hashlib.sha256(url.encode()).hexdigest()[:11]


def cache_path(sha256: str):
    return CACHE_DIR / f"{sha256}.md"


def _cached_urls(fetchlog) -> dict[str, dict]:
    return {row["url"]: row for row in read_jsonl(fetchlog)}


def run_fetch(run: str = "latest", app: str | None = None, refresh: bool = False) -> str:
    run_id = resolve_run_id(run)
    rd = run_dir(run_id)
    candidates_file = rd / "candidates.jsonl"
    fetchlog = rd / "fetchlog.jsonl"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    seen = {} if refresh else _cached_urls(fetchlog)
    seed_slugs = {a.slug for a in load_seed()}

    def fetch_one(slug: str, cand: dict) -> dict | None:
        url = cand["url"]
        res = scrape(url)
        sha = hashlib.sha256(res.markdown.encode()).hexdigest() if res.markdown else ""
        if res.markdown:
            cache_path(sha).write_text(res.markdown)
        return {
            "doc_id": doc_id_for(url), "slug": slug, "url": url,
            "http_status": res.http_status, "sha256": sha,
            "bytes": len(res.markdown.encode()), "source_tool": res.source_tool,
            "vendor_domain": cand.get("vendor_domain", False),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    # Small pool: enough to hide per-URL latency, low enough for free-tier concurrency.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for row in read_jsonl(candidates_file):
            if app is not None and row["slug"] != app:
                continue
            if row["slug"] not in seed_slugs:
                continue
            todo = [c for c in row["candidates"] if c["url"] not in seen]
            for entry in pool.map(lambda c: fetch_one(row["slug"], c), todo):
                append_jsonl(fetchlog, entry)
                seen[entry["url"]] = entry
                log.info("fetched", slug=entry["slug"], url=entry["url"],
                         status=entry["http_status"], bytes=entry["bytes"],
                         tool=entry["source_tool"])
    return run_id


def load_docs_for_slug(run_id: str, slug: str) -> list[dict]:
    """Return [{doc_id, url, vendor_domain, markdown}] for successfully fetched docs."""
    rd = run_dir(run_id)
    docs = []
    for row in read_jsonl(rd / "fetchlog.jsonl"):
        if row["slug"] != slug or row["http_status"] != 200 or not row["sha256"]:
            continue
        p = cache_path(row["sha256"])
        if p.exists():
            docs.append({
                "doc_id": row["doc_id"], "url": row["url"],
                "vendor_domain": row["vendor_domain"], "markdown": p.read_text(),
            })
    return docs
