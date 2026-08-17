"""Stage 9 — render (PRD §5.9, §8.2).

Assemble the published dataset from the run artifacts, compute the aggregates the page
shows (auth distribution, self-serve×category matrix, buildability tiers), and emit:
  - site/data.json  — the full machine-readable dataset (records + queue + accuracy)
  - site/llms.txt   — a short plain-text index for agent consumers
  - site/index.html — the template with the dataset injected (page reads its own JSON,
                      so the chart and the table can never drift)
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .config import SITE_DIR
from .logging_setup import get_logger
from .runstore import read_jsonl, resolve_run_id, run_dir
from .seed import load_seed

log = get_logger(stage="render")

DATA_MARKER = "/*__DATA_JSON__*/null"


def _load_records(rd) -> list[dict]:
    p2 = list(read_jsonl(rd / "pass2.jsonl"))
    if p2:
        return p2
    return list(read_jsonl(rd / "pass1.jsonl"))


def _aggregates(records: list[dict]) -> dict:
    auth = Counter()
    for r in records:
        for a in r["auth_schemes"]:
            auth[a] += 1
    gates = Counter(r["gate"] for r in records)
    build = Counter(r["buildability"] for r in records)
    mcp = Counter(r["mcp"] for r in records)

    # self-serve vs gated × category matrix
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        bucket = "self_serve" if r["gate"] == "self_serve" else (
            "no_api" if r["gate"] == "no_api" else "gated")
        matrix[r["category"]][bucket] += 1

    return {
        "auth_distribution": dict(auth.most_common()),
        "gate_distribution": dict(gates.most_common()),
        "buildability": dict(build.most_common()),
        "mcp": dict(mcp.most_common()),
        "matrix": {k: dict(v) for k, v in matrix.items()},
    }


def _headline(records: list[dict], catalog: dict, queue: list[dict]) -> dict:
    n = len(records)
    already = sum(1 for r in records if r["composio_toolkit"] == "exists")
    ship_now = sum(1 for q in queue if q["lane"] == "ship_this_week")
    outreach = sum(1 for q in queue if q["lane"] == "start_outreach_now")
    not_toolkit = sum(1 for q in queue if q["lane"] == "not_a_toolkit")
    needs_human = sum(1 for r in records if r["needs_human"])
    first_party_mcp = sum(1 for r in records if r["mcp"] == "first_party")
    return {
        "total": n,
        "already_toolkits": already,
        "already_toolkits_note": ("authoritative" if catalog.get("complete")
                                  else "lower bound (partial catalog source)"),
        "ship_this_week": ship_now,
        "start_outreach_now": outreach,
        "not_a_toolkit": not_toolkit,
        "needs_human": needs_human,
        "first_party_mcp": first_party_mcp,
    }


def build_data(run: str = "latest") -> dict:
    run_id = resolve_run_id(run)
    rd = run_dir(run_id)
    records = _load_records(rd)
    seed_n = len(load_seed())

    catalog = {}
    from .config import CATALOG_FILE
    if CATALOG_FILE.exists():
        cat_full = json.loads(CATALOG_FILE.read_text())
        catalog = {k: v for k, v in cat_full.items() if k != "apps"}

    queue = json.loads((rd / "queue.json").read_text()) if (rd / "queue.json").exists() else []
    accuracy = json.loads((rd / "accuracy.json").read_text()) if (rd / "accuracy.json").exists() else {"status": "not_run"}
    browser = json.loads((rd / "browser_report.json").read_text()) if (rd / "browser_report.json").exists() else {}
    fixes = list(read_jsonl(rd / "fixes.jsonl"))

    data = {
        "meta": {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed_apps": seed_n,
            "records_present": len(records),
            "status": "complete" if len(records) == seed_n else f"partial ({len(records)}/{seed_n})",
            "catalog": catalog,
        },
        "headline": _headline(records, catalog, queue),
        "aggregates": _aggregates(records),
        "records": records,
        "queue": queue,
        "accuracy": accuracy,
        "fixes": fixes,
        "browser": browser,
    }
    return data


def render(run: str = "latest") -> dict:
    data = build_data(run)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "data.json").write_text(json.dumps(data, indent=2))

    # llms.txt — plain-text index for agent consumers
    h = data["headline"]
    (SITE_DIR / "llms.txt").write_text(
        "# Connector Readiness Audit\n\n"
        "Agentic research over 100 SaaS apps for Composio toolkit buildability, with a "
        "prioritized build-and-outreach queue and honest, gold-scored accuracy.\n\n"
        f"Status: {data['meta']['status']} (run {data['meta']['run_id']}).\n\n"
        "## Headline\n"
        f"- {h['total']} apps audited; {h['already_toolkits']} already Composio toolkits "
        f"({h['already_toolkits_note']}).\n"
        f"- {h['ship_this_week']} can ship this week with zero approvals.\n"
        f"- {h['start_outreach_now']} need outreach/approval — start those now (long lead times).\n"
        f"- {h['not_a_toolkit']} are not toolkits (no API / wrong shape).\n"
        f"- {h['needs_human']} routed to human review; {h['first_party_mcp']} have first-party MCP.\n\n"
        "## Machine-readable\n- Full dataset: ./data.json\n- Source + README: see repository.\n"
    )

    # inject into the page template so index.html reads its own JSON (no drift)
    template = SITE_DIR / "index.template.html"
    if template.exists():
        html = template.read_text().replace(DATA_MARKER, json.dumps(data), 1)
        (SITE_DIR / "index.html").write_text(html)
        # Also emit a fully self-contained single file (JS inlined) for hosts with a strict
        # CSP or a one-file share (e.g. an Artifact). No external requests at all.
        app_js = SITE_DIR / "app.js"
        if app_js.exists():
            standalone = html.replace(
                '<script src="./app.js"></script>',
                "<script>\n" + app_js.read_text() + "\n</script>",
            )
            (SITE_DIR / "standalone.html").write_text(standalone)
        log.info("index_rendered")
    log.info("rendered", records=len(data["records"]), status=data["meta"]["status"])
    return data


if __name__ == "__main__":
    d = render()
    print(json.dumps({"meta": d["meta"], "headline": d["headline"]}, indent=2))
