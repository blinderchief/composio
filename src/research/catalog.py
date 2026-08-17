"""Stage 2 — catalog diff (PRD §5.2, differentiator #2).

Pull Composio's live toolkit catalog and diff it against the 100. This reframes the
headline from "what's buildable" to "what's worth building *next*".

Two sources, in order of authority:
  1. Authenticated List Toolkits API (backend.composio.dev/api/v3/toolkits, cursor
     pagination) — COMPLETE. No match => composio_toolkit = "absent" (a real claim).
  2. Public composio.dev/toolkits scrape — INCOMPLETE (the page renders a subset).
     No match => composio_toolkit = "unknown", because absence in a partial list is
     not proof of absence. We say so on the page and in the JSON.

Matching: normalize to lowercase alphanumerics (collapses `-`/`_`/spaces), so
`linkedin-ads` == `linkedin_ads` and `bright-data` == `brightdata`. Exact normalized
match is authoritative. Near-misses are recorded as fuzzy *suggestions* for a human to
confirm — never auto-accepted, so we can't fabricate an "exists".
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone

import httpx
from rapidfuzz import fuzz, process

from .config import CATALOG_FILE, get_config
from .logging_setup import get_logger
from .seed import load_seed

log = get_logger(stage="catalog")

API_URL = "https://backend.composio.dev/api/v3/toolkits"
SCRAPE_URL = "https://composio.dev/toolkits"
LOGO_RE = re.compile(r"logos\.composio\.dev/api/([a-zA-Z0-9_]+)")


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def fetch_catalog_api(key: str, timeout: float = 30.0) -> set[str]:
    """Complete catalog via the authenticated API, following the base64 cursor."""
    slugs: set[str] = set()
    cursor: str | None = None
    with httpx.Client(timeout=timeout, headers={"x-api-key": key}) as client:
        while True:
            params: dict[str, str | int] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            r = client.get(API_URL, params=params)
            r.raise_for_status()
            body = r.json()
            items = body.get("items", body.get("data", []))
            for it in items:
                slug = it.get("slug") or it.get("key") or it.get("name")
                if slug:
                    slugs.add(str(slug))
            cursor = (body.get("pageInfo") or {}).get("nextCursor") or body.get("next_cursor")
            if not cursor:
                break
    log.info("catalog_api_ok", count=len(slugs))
    return slugs


def fetch_catalog_scrape(timeout: float = 30.0) -> set[str]:
    """Lower-bound catalog from the public page's embedded logo URLs (one per toolkit)."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(SCRAPE_URL)
        r.raise_for_status()
    slugs = set(LOGO_RE.findall(r.text))
    log.info("catalog_scrape_ok", count=len(slugs))
    return slugs


def build_diff() -> dict:
    cfg = get_config()
    complete = False
    source = "scrape"
    catalog: set[str] = set()

    if cfg.composio_key:
        try:
            catalog = fetch_catalog_api(cfg.composio_key)
            complete, source = True, "api"
        except Exception as e:  # invalid key, network, shape change -> fall back honestly
            log.warning("catalog_api_failed", error=str(e))

    if not catalog:
        catalog = fetch_catalog_scrape()

    cat_by_norm = {_norm(c): c for c in catalog}
    absent_value = "absent" if complete else "unknown"

    results: list[dict] = []
    exists = fuzzy = missing = 0
    for app in load_seed():
        key = _norm(app.slug)
        alt = _norm(app.name)
        match = cat_by_norm.get(key) or cat_by_norm.get(alt)
        row: dict = {"slug": app.slug, "name": app.name}
        if match:
            row.update(composio_toolkit="exists", composio_toolkit_slug=match, match="exact")
            exists += 1
        else:
            # fuzzy suggestion only — recorded for a human, never auto-accepted as "exists"
            best = process.extractOne(key, cat_by_norm.keys(), scorer=fuzz.ratio)
            suggestion = None
            if best and best[1] >= 88:
                suggestion = {"slug": cat_by_norm[best[0]], "score": round(best[1], 1)}
                fuzzy += 1
            row.update(
                composio_toolkit=absent_value,
                composio_toolkit_slug=None,
                match="none",
                fuzzy_suggestion=suggestion,
            )
            missing += 1
        results.append(row)

    out = {
        "snapshot_date": date.today().isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "complete": complete,
        "catalog_size": len(catalog),
        "note": (
            "Authoritative: no-match means the app is genuinely absent from Composio's catalog."
            if complete
            else "PARTIAL SOURCE: the public page renders a subset (~1000 of ~1100+). "
            "No-match is recorded as 'unknown', not 'absent'. Add a valid Composio_api "
            "key to .env for the authoritative diff."
        ),
        "counts": {"exists": exists, absent_value: missing, "fuzzy_suggestions": fuzzy},
        "apps": results,
    }
    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(json.dumps(out, indent=2))
    log.info("catalog_diff_written", exists=exists, missing=missing, source=source, complete=complete)
    return out


def catalog_map() -> dict[str, dict]:
    """{slug: {composio_toolkit, composio_toolkit_slug}} for downstream stages."""
    if not CATALOG_FILE.exists():
        return {}
    data = json.loads(CATALOG_FILE.read_text())
    return {a["slug"]: a for a in data["apps"]}


if __name__ == "__main__":
    out = build_diff()
    print(json.dumps({k: v for k, v in out.items() if k != "apps"}, indent=2))
