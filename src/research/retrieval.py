"""Retrieval clients: Firecrawl (primary), Tavily + Exa (corroboration), Jina (fallback).

Thin, explainable HTTP wrappers — no vendor SDKs, so every request is visible and the
free-tier behaviour (backoff, concurrency) is ours to control. Each search returns a
uniform list[SearchHit]; the scraper returns Markdown + status.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_config
from .logging_setup import get_logger

log = get_logger(stage="retrieval")

# Retry only on transient failures (429 / 5xx / network), never on 4xx auth/shape errors.
_RETRYABLE = (httpx.TransportError, httpx.TimeoutException)


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    source_tool: str


@dataclass(frozen=True)
class ScrapeResult:
    url: str
    http_status: int
    markdown: str
    source_tool: str


def domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _is_retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _post(client: httpx.Client, url: str, **kw) -> httpx.Response:
    r = client.post(url, **kw)
    if _is_retryable_status(r.status_code):
        raise httpx.TransportError(f"retryable {r.status_code}")
    return r


# --- search channels ---------------------------------------------------------

def firecrawl_search(query: str, limit: int = 5) -> list[SearchHit]:
    key = get_config().require("firecrawl_key", "Firecrawl API key (firecrawl_api)")
    with httpx.Client(timeout=60) as c:
        r = _post(
            c,
            "https://api.firecrawl.dev/v2/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": query, "limit": limit},
        )
    if r.status_code >= 400:
        log.warning("firecrawl_search_err", status=r.status_code, query=query)
        return []
    data = r.json().get("data", {})
    web = data.get("web", data) if isinstance(data, dict) else data
    hits = [
        SearchHit(url=h["url"], title=h.get("title", ""), source_tool="firecrawl")
        for h in (web or [])
        if h.get("url")
    ]
    return hits


def tavily_search(query: str, limit: int = 5) -> list[SearchHit]:
    key = get_config().tavily_key
    if not key:
        return []
    with httpx.Client(timeout=60) as c:
        r = _post(
            c,
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": limit},
        )
    if r.status_code >= 400:
        log.warning("tavily_search_err", status=r.status_code)
        return []
    return [
        SearchHit(url=h["url"], title=h.get("title", ""), source_tool="tavily")
        for h in r.json().get("results", [])
        if h.get("url")
    ]


def exa_search(query: str, limit: int = 5) -> list[SearchHit]:
    key = get_config().exa_key
    if not key:
        return []
    with httpx.Client(timeout=60) as c:
        r = _post(
            c,
            "https://api.exa.ai/search",
            headers={"x-api-key": key},
            json={"query": query, "numResults": limit},
        )
    if r.status_code >= 400:
        log.warning("exa_search_err", status=r.status_code)
        return []
    return [
        SearchHit(url=h["url"], title=h.get("title", ""), source_tool="exa")
        for h in r.json().get("results", [])
        if h.get("url")
    ]


# --- scrape channels ---------------------------------------------------------

def firecrawl_scrape(url: str) -> ScrapeResult:
    key = get_config().require("firecrawl_key", "Firecrawl API key (firecrawl_api)")
    try:
        with httpx.Client(timeout=45) as c:
            r = _post(
                c,
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {key}"},
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            )
    except Exception:
        return ScrapeResult(url, 0, "", "firecrawl")
    if r.status_code >= 400:
        return ScrapeResult(url, r.status_code, "", "firecrawl")
    md = (r.json().get("data") or {}).get("markdown", "")
    return ScrapeResult(url, 200 if md else 204, md, "firecrawl")


def jina_scrape(url: str) -> ScrapeResult:
    """Keyless low-RPM fallback: r.jina.ai renders a page to Markdown."""
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as c:
            r = c.get(f"https://r.jina.ai/{url}", headers={"Accept": "text/markdown"})
    except Exception:
        return ScrapeResult(url, 0, "", "jina")
    return ScrapeResult(url, r.status_code, r.text if r.status_code < 400 else "", "jina")


def scrape(url: str) -> ScrapeResult:
    """Firecrawl first; Jina fallback if Firecrawl yields nothing usable."""
    res = firecrawl_scrape(url)
    if res.http_status == 200 and len(res.markdown) > 200:
        return res
    fallback = jina_scrape(url)
    if fallback.http_status == 200 and len(fallback.markdown) > 200:
        return fallback
    return res if res.markdown else fallback
