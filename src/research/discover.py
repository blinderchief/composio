"""Stage 3 — discover (PRD §5.3).

For each app, generate targeted queries (not one generic one), run them across Firecrawl
(primary) plus Tavily and Exa (corroboration), then rank candidate URLs with a vendor-domain
preference and cap at ~8/app. The hint URL from the seed is always included.

Output: candidates.jsonl, one row per app: {slug, candidates: [{url, title, source_tools,
vendor_domain}]}. Cross-tool agreement (a URL surfaced by >1 channel) ranks highest — that
is the first, cheapest corroboration signal, before we spend a scrape on it.
"""

from __future__ import annotations

from collections import defaultdict

from .logging_setup import get_logger
from .models import SeedApp
from .retrieval import SearchHit, domain, exa_search, firecrawl_search, tavily_search
from .runstore import append_jsonl, done_slugs, resolve_run_id, run_dir
from .seed import load_seed

log = get_logger(stage="discover")

QUERY_TEMPLATES = [
    "{name} API authentication docs",
    "{name} developer get API key pricing",
    "{name} partner program API access",
    "{name} MCP server",
]
PER_APP_CAP = 6


def _vendor_domain(app: SeedApp) -> str:
    return domain(app.hint_url)


def discover_app(app: SeedApp) -> dict:
    vendor = _vendor_domain(app)
    # Credit budgeting: Firecrawl's free tier is the binding constraint and we need most
    # of it for scraping (stage 4). So Tavily (free 1k) is the primary search channel,
    # Exa corroborates, and Firecrawl searches only the single highest-signal query.
    hits: list[SearchHit] = []
    for tmpl in QUERY_TEMPLATES:
        hits += tavily_search(tmpl.format(name=app.name), limit=4)
    for tmpl in QUERY_TEMPLATES[:2]:
        hits += exa_search(tmpl.format(name=app.name), limit=3)
    hits += firecrawl_search(QUERY_TEMPLATES[0].format(name=app.name), limit=4)

    # Merge by URL, tracking which tools found each (agreement = corroboration signal).
    by_url: dict[str, dict] = defaultdict(lambda: {"title": "", "tools": set()})
    for h in hits:
        by_url[h.url]["title"] = by_url[h.url]["title"] or h.title
        by_url[h.url]["tools"].add(h.source_tool)

    def score(url: str, meta: dict) -> tuple:
        d = domain(url)
        vendor_match = d == vendor or d.endswith("." + vendor) or vendor.endswith("." + d) if vendor else False
        return (vendor_match, len(meta["tools"]))  # vendor domain first, then agreement

    ranked = sorted(by_url.items(), key=lambda kv: score(*kv), reverse=True)

    candidates: list[dict] = []
    seen_domains: set[str] = set()
    # Always seed with the hint URL — it is the assignment's own pointer.
    candidates.append(
        {"url": app.hint_url, "title": "seed hint URL", "source_tools": ["seed"],
         "vendor_domain": True}
    )
    seen_domains.add(vendor)
    for url, meta in ranked:
        if len(candidates) >= PER_APP_CAP:
            break
        if url == app.hint_url:
            continue
        d = domain(url)
        vendor_match = bool(vendor) and (d == vendor or d.endswith("." + vendor))
        # Keep vendor pages liberally; cap third-party domains to 1 each to stay diverse.
        if not vendor_match and d in seen_domains:
            continue
        seen_domains.add(d)
        candidates.append(
            {"url": url, "title": meta["title"], "source_tools": sorted(meta["tools"]),
             "vendor_domain": vendor_match}
        )
    return {"slug": app.slug, "vendor": vendor, "candidates": candidates}


def run_discover(run: str = "latest", app: str | None = None, refresh: bool = False) -> str:
    run_id = resolve_run_id(run)
    out = run_dir(run_id) / "candidates.jsonl"
    already = set() if refresh else done_slugs(out)
    apps = [a for a in load_seed() if (app is None or a.slug == app)]
    for a in apps:
        if a.slug in already:
            log.info("skip_cached", slug=a.slug)
            continue
        row = discover_app(a)
        append_jsonl(out, row)
        log.info("discovered", slug=a.slug, n=len(row["candidates"]))
    return run_id
