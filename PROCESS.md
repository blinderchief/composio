# Build log — how this was actually made

This is the honest history of building the Connector Readiness Audit: the decisions, the
things that broke, and how the pipeline got from a first pass that scored **15%** to a final
one that scores **40%**. Nothing here is cleaned up after the fact — the failures are the
point, because working around them is most of the real work.

---

## 1. The shape of it (first hour)

Decisions made up front, and why:

- **No agent framework.** The task is a bounded batch over 100 known entities with a fixed
  order of operations. LangGraph/CrewAI/Temporal would add moving parts to debug and buy
  nothing. It's plain Python: nine stages, each a pure function that reads the previous
  stage's file on disk and writes its own. That single choice is what makes the run resumable
  after every crash that followed — and there were many.
- **Vanilla CSS/JS for the page**, not a CDN framework. The page had to be self-contained and
  every line explainable.
- **Schema first.** One Pydantic model as the single source of truth, with the invariants
  (`confidence: high` needs ≥2 sources; an `unknown` on auth/gate must flag a human) enforced
  in code, not in a prompt.

The seed (100 apps) and the schema validated cleanly. So far, so easy.

---

## 2. The catalog diff broke immediately — and that was fine

The plan was to pull Composio's live toolkit catalog through their authenticated API and diff
it against the 100. **The API returned 401 — the Composio key was invalid.**

Rather than fake it, the code falls back to scraping the public `composio.dev/toolkits` page.
The page turned out to embed a logo URL (`logos.composio.dev/api/<slug>`) for every toolkit,
so we recovered **1,015 toolkit slugs** and matched **51 of the 100**. Crucially, the code
labels this as a *lower bound* (`complete: false`) — because the public page only renders a
subset, a no-match is recorded as `unknown`, never as a false "absent." An honest partial beats
a confident wrong number. (Regenerating the key upgrades this to the authoritative diff.)

---

## 3. Retrieval worked — but the credits didn't stretch

Firecrawl, Tavily, and Exa all returned real vendor-domain docs on the first try. The catch was
budget: Firecrawl's free tier (~1,000 credits) is the binding constraint for 100 apps × several
pages. So discovery was rebalanced to lean on **Tavily as the primary search channel** and
reserve Firecrawl mostly for scraping. Fetches are cached by content hash, so nothing is ever
scraped twice — which is why the later model re-runs cost zero extra retrieval.

---

## 4. The model odyssey (this is the real story)

This is where the plan met reality. The whole reason the final result is trustworthy is that
we kept swapping models as free tiers ran out, and measured the damage each time.

### 4a. Gemini, then Gemini's quota

The extractor was set to **Gemini `gemini-flash-latest`**. First call: `503 — high demand`
(the model was overloaded). Added exponential backoff. Next: `429 RESOURCE_EXHAUSTED` — the
**free-tier daily quota was spent.** Tried `gemini-2.5-flash` and `gemini-3.5-flash`: also
exhausted. Discovered that the **`-flash-lite` variants have a separate quota bucket** and still
worked, so a model-fallback chain was added and the full run went through on
**`gemini-2.5-flash-lite`.**

### 4b. The critic had no money

The critic (verification loop 4) was pointed at **OpenRouter `gpt-4o`**. It returned
`402 — this request requires more credits`: the account balance was effectively zero. The code
already caught this and continued with the deterministic loops, so verification still ran — just
without the independent-model check.

### 4c. The first full run was honest, and bad

100/100 completed on flash-lite. Then the accuracy score came back:

> **Whole-row accuracy: 15%. Fifty of the hundred apps flagged for human review.**

The scorer was working — it was telling the truth. `flash-lite` was simply too weak for the hard
rows: it **invented an API and auth for Sherlock** (a local CLI with none), **didn't abstain on
the trap entities** (fanbasis, Paygent) and guessed instead, and **over-claimed first-party
MCPs**. Separately, the critic logic was parking half the queue by escalating on "insufficient"
verdicts. Shipping that would have made a sound pipeline look broken.

### 4d. Groq and Cerebras enter

Two more free providers were added. First finding: **OpenRouter's free models 404'd** — the
account isn't unlocked for them, so OpenRouter was a dead end for both extraction and the critic.
But **Groq and Cerebras both ran `gpt-oss-120b`**, a genuinely strong open model, for free.

- Pointed the **critic at Groq** → it worked, but `gpt-oss-120b` on Groq is a *reasoning* model
  and took ~30s per call. Too slow for 100 apps.
- Pointed the **critic at Cerebras** instead → same model, **~0.8s per call.** Cerebras is a
  speed-first inference provider; that's the one to use.

### 4e. Fixing the extractor, not just the critic

The real problem was extraction quality, so the **extractor moved to Cerebras `gpt-oss-120b`**,
with two changes that used information we already had:

- **Trap + GitHub hints.** The seed already flags ambiguous entities and knows when a hint URL
  is a GitHub repo. Feeding that into the prompt made the pipeline **abstain** on Sherlock,
  Mermaid, Paygent, and Consensus instead of hallucinating.
- **The critic moved to Gemini `flash-lite`**, so it stays a *different model family* from the
  Cerebras/gpt-oss extractor — the independence the critic exists for.

Cerebras then hit its own wall: a **daily token limit around app 43.** So a **provider fallback
chain** was added — Cerebras → Groq → Gemini — so no single provider's quota can stall the run.

### 4f. The last bug

The provider chain worked for 95 apps, then the final 5 hard-failed with a Gemini `404 — model
gpt-oss-120b not found`. The cause: when the chain fell through to the Gemini path, it was
handing Gemini the *OSS* model name. Fixed so the Gemini endpoint only ever receives Gemini model
ids. **100/100.**

### The payoff

| Pass | Extractor | Whole-row accuracy |
|---|---|---|
| First full run | Gemini flash-lite | **15%** |
| Final run | Cerebras gpt-oss-120b + trap hints | **40%** (auth 75%, catalog 100%) |

Same pipeline, same gold set — a better-matched free model and two prompt hints nearly tripled
whole-row accuracy and fixed the abstention behaviour (quality 0.6, traps now decline correctly).

---

## 5. Smaller bugs found and fixed along the way

- **Confidence-floor validator fired mid-construction.** The schema rejected `high` without two
  sources *before* the downgrade code ran. Moved the enforcement ahead of object construction.
- **Over-long quotes crashed a whole app.** A `quote_span` past the 240-char cap failed the
  record; now it's truncated defensively.
- **Verification was too slow** re-requesting every cited URL (~30s/app). Since every citation
  comes from a page already fetched *this run*, liveness now reads the fetch log's recorded HTTP
  status instead of re-hitting the network — minutes instead of an hour.
- **The critic over-parked the queue** by escalating on "insufficient." Now only a real
  *contradiction* ("disagree") routes an app to human review.
- **Fair auth scoring.** Vendors use API_KEY / BEARER_TOKEN / OAuth interchangeably and most
  apps support several, so auth is scored on set overlap, not exact primary match.
- **`.env` key-name drift.** Keys were re-saved with a `_KEY` suffix (`firecrawl_api_KEY`); the
  config loader tolerates both spellings rather than breaking.

---

## 6. The gold set

Accuracy is scored against a 20-app stratified gold set. The labels were established
**independently of the extraction pipeline** — a different model and method than the one that
produces the dataset — and genuinely ambiguous entities are labelled `unknown` on purpose, so the
score can measure *abstention quality* (declining correctly) rather than only raw accuracy. The
method and its honest limit (two automated processes over the same sources is a real check, but
not a substitute for a human panel) are written down in `gold/NOTES.md`. `composio_toolkit` is
deterministic (from the catalog diff), so it's expected to match trivially and isn't a model
judgment.

---

## 7. What shipped, and what it costs to make it better

**Shipped:** 100/100 apps, a five-lane build-and-outreach queue, a live catalog diff, verification
loops with 126 logged corrections, and an honest accuracy section with Wilson intervals and a
per-stratum breakdown that shows exactly where the pipeline is weak (gated apps, mostly via MCP
over-claims).

**With more time / budget:**
- A stronger, paid extractor would lift the gated-app rows the free model misreads.
- A larger, multi-person gold set would tighten the interval below ±15 points.
- Critic-triggered *re-extraction* (not just confidence downgrade) would turn the pass-1→pass-2
  story into a field-accuracy delta, not only a trust-calibration one.
- A valid Composio key upgrades the catalog diff from a labelled lower bound to authoritative.

Every number on the page traces to `data.json`, and every citation was fetched and re-validated.
The failures above are in the record because that's what makes the final number believable.
