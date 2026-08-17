# Connector Readiness Audit

An agent that researches **100 SaaS apps** for API/agent-toolkit buildability, **verifies its
own answers** against fetched vendor docs, **measures its accuracy** against an independently
verified gold set, and outputs not a table but a **prioritized build-and-outreach queue** — the
artifact a connector-operations team actually works from.

- **Live page:** [Site](https://composio-five.vercel.app/)
- **Machine-readable:** `/data.json` · `/llms.txt` · JSON-LD in the page `<head>`
- **One command:** `make run`
- **Build log:** [`PROCESS.md`](PROCESS.md) — the honest history of how this was built, including
  every model that failed and how accuracy went from 15% to 40%.

The idea in one line: **a buildability verdict is knowledge; a queue that says *what to do,
where to apply, and when to start* is work.** So the output is the queue.

---

## What it produces

> **The blockers aren't engineering. They're paperwork, and paperwork has lead times.**

For each app the agent establishes: what auth it uses, whether a developer can self-serve
credentials or is gated (and *how* — paid tier, admin approval, business verification, partner
program, or contact-sales), how broad the API is, whether a first-party MCP exists, and whether
it's already in Composio's catalog. Then it turns that into a queue with five lanes — **ship this
week / unblock then ship / start outreach now / park / not a toolkit** — ordered by *when you'd
have to start*, because long-lead approvals have to begin today even when they rank lower by
value. Every number on the page is generated from `data.json`, never hand-typed, so the chart
and the table can't drift.

---

## Quickstart

```bash
cp .env.example .env      # add your keys (all free-tier friendly — table below)
make setup                # uv sync
make run                  # full pipeline, resumable, all 100 apps
make run APP=attio        # research a single app (also the live "re-run" trigger)
make test                 # deterministic logic: schema, Wilson math, verify loops, lanes
```

`make run` is **idempotent and resumable**: on restart it skips apps already finished and never
re-fetches a cached URL.

### Keys (`.env`)

| Key | Used for |
|---|---|
| `firecrawl_api` | scrape pages → Markdown |
| `Tavily_api` | primary web search |
| `Exa_api` | corroborating search |
| `GEMINI_API_KEY` + `GEMINI_model` | the **extractor** (structured JSON output) |
| `OPENROUTER_API_KEY` | the **critic** — a *different* model reviews each record |
| `Composio_api` | authoritative catalog diff + the MCP demo |

Keys are read case-insensitively and tolerate naming drift (`firecrawl_api` or
`firecrawl_api_KEY`). If a key is missing, the stage that needs it says so rather than guessing.

---

## How it works

Nine stages, each a pure function that reads the previous stage's file on disk and writes its
own. That's what makes the run resumable and every claim auditable.

```
seed → catalog diff → discover → fetch → extract → verify → queue
                                                        ↘ score ↘ render
```

No agent framework — a bounded batch over 100 known entities with a fixed DAG doesn't need one,
and plain code is easier to debug and explain.

| Stage | Does |
|---|---|
| **seed** | Load the 100 apps (`data/seed/apps.yaml`), with ambiguous/wrong-shape entries flagged. |
| **catalog diff** | Pull Composio's live toolkit catalog and mark each app `exists` / `absent` / `unknown`. Reframes the question from "what's buildable" to "what's worth building next." |
| **discover** | 4 targeted queries per app across Tavily + Exa + Firecrawl, preferring vendor-domain pages. |
| **fetch** | Scrape to Markdown, cache by content hash, and log every fetch — the anti-hallucination substrate. |
| **extract** | One structured-output call per app. Citations are constrained to the ids of docs actually fetched. |
| **verify** | Five independent checks (below). |
| **queue** | Derive the ops lanes, effort size, lead time, and one imperative next action per app. |
| **score** | Compare against the gold set; report Wilson intervals, per-stratum accuracy, and abstention quality. |
| **render** | Emit `site/data.json`, `site/llms.txt`, and inject the page. |

### The citations can't be hallucinated

Two independent guarantees: the model can only cite an id from the set of documents this run
actually fetched, **and** every citation is then re-validated — the URL is re-requested (non-200s
dropped) and the quoted span must appear **verbatim** in the cached page, or the citation is
dropped as decorative. A real URL attached to an unsupported claim is the most common silent
failure; this catches it.

### The five verification loops

1. **URL liveness** — re-request every citation; drop dead links, downgrade confidence.
2. **Claim support** — the quote must appear verbatim in the cited page, or the citation goes.
3. **Corroboration** — `confidence: high` requires ≥2 independent sources, one on the vendor's
   own domain. Enforced in code, not the prompt.
4. **Critic** — a different model (via OpenRouter) reviews each record and flags disagreements,
   which downgrade confidence and route to human review.
5. **Browser channel** — detect docs behind JS/login walls; only worth building if enough apps
   need it. Knowing when *not* to build something is part of the answer.

---

## Accuracy

Scored against a 20-app **gold set** (`gold/gold_set.yaml`), stratified across easy self-serve,
gated-but-documented, paid-tier, ambiguous, not-an-app, and recently-changed apps. The labels
are established **independently of the extraction pipeline** — a different model and method than
the one that produces the dataset — and genuinely ambiguous entities are labelled `unknown` on
purpose. See [`gold/NOTES.md`](gold/NOTES.md) for the method and its honest limits.

The page reports, for pass-1 and pass-2: per-field and whole-row accuracy, a **Wilson score
interval** (95%, with finite-population correction — never a bare point estimate), **per-stratum**
accuracy, and **abstention quality** (of the rows the agent declined, how many it declined
*correctly*). Abstaining correctly is better than guessing correctly, and almost no automated
audit measures it.

```bash
make gold-sample     # reproduce the stratified sample (seeded)
make score           # score the current run against the gold set
```

---

## The demo

`proof/composio_mcp.py` stands up a live Composio Tool-Router MCP session for an app the audit
marked `build_now` (e.g. Linear) and prints the endpoint you can hand to any MCP client — one
completed lap from research to a working integration.

```bash
uv run python proof/composio_mcp.py --toolkit linear    # needs a valid Composio_api key
make run APP=<slug>                                      # always-on: re-run one app end to end
```
---

## Layout

```
src/research/   models · seed · catalog · discover · fetch · extract · verify · queue · score · render · cli
data/seed/      apps.yaml (the 100)
data/runs/<id>/ candidates · fetchlog · pass1 · pass2 · fixes · critic · queue · accuracy
gold/           gold_set.yaml · NOTES.md
site/           index.html · app.js · data.json · llms.txt · vercel.json
proof/          composio_mcp.py
tests/          schema · Wilson math · verify loops · queue lanes · sampler
PROCESS.md      build log — the honest history, failures included
```

Every claim on the page traces to `data.json`; every citation was fetched and re-validated.
Dates are on the page because facts and catalogs move.
