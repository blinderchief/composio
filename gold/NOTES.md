# Gold set — verification method

**Verifier:** independent assessment (Claude Opus), separate from the extraction pipeline.
**Method:** each label established against primary vendor documentation and public API
references, without reference to the pipeline's output for that app.
**Sampling seed:** 20260817 (reproducible via `make gold-sample`).
**Catalog snapshot:** 2026-08-17 (partial public source — see below).

## Why this is a meaningful check — and its honest limit

The gold labels are produced by a **different model and a different process** than the one
that produces the dataset: the dataset is extracted by Gemini and reviewed by an OpenRouter
critic; the gold labels here are an independent judgment of the correct answer for each app.
Scoring the dataset against these labels therefore measures agreement between two independent
processes over the same primary sources — a real signal, and the basis for the pass-1 → pass-2
delta and the per-stratum breakdown.

**The honest limit:** this is not the same as verification by multiple independent people.
Two automated processes reading the same public docs can share a blind spot a human panel
would catch. A larger, multi-person gold set would tighten both the labels and the reported
interval. This is stated in the accuracy caveat on the page, not hidden. The `Composio` catalog
field is deterministic (injected from the catalog diff), so it is not a model judgment and is
expected to match trivially; it is included for completeness and annotated as such.

## Design choices

- **Ambiguous entities are labelled `UNKNOWN`/`unknown` on purpose** (fanbasis, paygent-connect,
  and the uncertain fields of consensus). The correct output for a genuinely unresolvable entity
  is a documented abstention, so the gold value is `unknown`. This is what lets us measure
  *abstention quality* — good unknowns (gold also unknown) vs bad unknowns (gold established,
  pipeline missed).
- **Nuanced gates are labelled precisely.** google-ads is `partner_gated` (two-stage: a developer
  token is obtainable, but production/standard access needs a separate approval) rather than a
  flat "gated". Getting this specific is the whole point of the `AccessGate` enum.
- **Recently-changed apps** (clay, ahrefs) test staleness: clay shipped a public API in 2026;
  ahrefs moved its API tier boundary. Older sources would score these wrong.

| slug | stratum | fields established | left unknown |
|---|---|---|---|
| mermaid-cli | not_an_app | auth, gate, mcp, build | — |
| sherlock | not_an_app | auth, gate, mcp, build | — |
| fanbasis | ambiguous | — | auth, gate, mcp, build (unresolvable) |
| paygent-connect | ambiguous | — | auth, gate, mcp, build (unresolvable) |
| consensus | ambiguous | auth, mcp | gate, build (tier not published) |
| clay | recently_changed | auth, gate, mcp, build | — |
| ahrefs | recently_changed | auth, gate, mcp, build | — |
| snowflake | paid_tier | auth, gate, mcp, build | — |
| devin | paid_tier | auth, gate, mcp, build | — |
| otter-ai | paid_tier | auth, gate, mcp, build | — |
| linear | easy_self_serve | auth, gate, mcp, build | — |
| harvest | easy_self_serve | auth, gate, mcp, build | — |
| smartsheet | easy_self_serve | auth, gate, mcp, build | — |
| airtable | easy_self_serve | auth, gate, mcp, build | — |
| mongodb-atlas | easy_self_serve | auth, gate, mcp, build | — |
| google-ads | gated_documented | auth, gate, mcp, build | — |
| brex | gated_documented | auth, gate, mcp, build | — |
| squarespace | gated_documented | auth, gate, mcp, build | — |
| gumroad | gated_documented | auth, gate, mcp, build | — |
| woocommerce | gated_documented | auth, gate, mcp, build | — |
