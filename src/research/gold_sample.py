"""Propose the 20-row stratified gold sample (GOLD_SET.md §2).

This tool NEVER writes gold/. It emits a *proposal* (data/gold_sample_proposed.yaml) that
Suyash then verifies BY HAND, before viewing any agent output, promoting each row into
gold/gold_set.yaml with his own URL + quote span. Sampling is seeded and reproducible.

The stratum candidate pools use only category, the trap flags, and the dossier's own
sampling hints (GOLD_SET.md §3) — never agent-produced field values. Choosing *which* apps
a human checks is not the same as choosing their answers.
"""

from __future__ import annotations

import random

import yaml

from .config import DATA
from .seed import load_seed

SEED = 20260817  # record the seed so the sample is reproducible (GOLD_SET.md §2)

# Dossier-sourced sampling hints (GOLD_SET.md §3). These route apps into strata for
# HUMAN verification; they are not gold values.
PAID_TIER_HINTS = {"ahrefs", "otter-ai", "grain", "pitchbook", "plaid", "snowflake", "devin"}
RECENTLY_CHANGED_HINTS = {"clay", "ahrefs", "reducto", "higgsfield"}
EASY_CATEGORIES = {"Developer Infra and Data", "Productivity and Project Management"}
GATED_CATEGORIES = {"Marketing Ads Email and Social", "Ecommerce", "Finance and Fintech"}

TARGETS = {
    "easy_self_serve": 5,
    "gated_documented": 5,
    "paid_tier": 3,
    "ambiguous": 3,
    "not_an_app": 2,
    "recently_changed": 2,
}


def propose() -> list[dict]:
    apps = load_seed()
    by_slug = {a.slug: a for a in apps}
    rng = random.Random(SEED)

    not_an_app = [a.slug for a in apps if a.is_trap and a.slug in {"sherlock", "mermaid-cli"}]
    ambiguous = [a.slug for a in apps if a.is_trap and a.slug not in not_an_app]
    recently_changed = [s for s in RECENTLY_CHANGED_HINTS if s in by_slug]
    paid_tier = [s for s in PAID_TIER_HINTS if s in by_slug]
    easy = [a.slug for a in apps if a.category in EASY_CATEGORIES]
    gated = [a.slug for a in apps if a.category in GATED_CATEGORIES]

    chosen: dict[str, str] = {}  # slug -> stratum, dedup across pools

    def take(pool: list[str], stratum: str, n: int) -> None:
        avail = [s for s in pool if s not in chosen]
        rng.shuffle(avail)
        for s in avail[:n]:
            chosen[s] = stratum

    # Order matters: fill the scarce, high-signal strata first so they aren't stolen.
    take(not_an_app, "not_an_app", TARGETS["not_an_app"])
    take(ambiguous, "ambiguous", TARGETS["ambiguous"])
    take(recently_changed, "recently_changed", TARGETS["recently_changed"])
    take(paid_tier, "paid_tier", TARGETS["paid_tier"])
    take(easy, "easy_self_serve", TARGETS["easy_self_serve"])
    take(gated, "gated_documented", TARGETS["gated_documented"])

    rows = []
    for slug, stratum in chosen.items():
        a = by_slug[slug]
        rows.append({
            "slug": slug, "name": a.name, "category": a.category, "stratum": stratum,
            "hint_url": a.hint_url,
            # fields the HUMAN fills — left explicit so the template is obvious
            "auth_schemes": "TODO", "gate": "TODO", "mcp": "TODO",
            "composio_toolkit": "TODO", "buildability": "TODO",
            "verified_by": "TODO", "verified_at": "TODO", "evidence_url": "TODO",
            "quote_span": "TODO",
        })
    return rows


def main() -> None:
    rows = propose()
    dest = DATA / "gold_sample_proposed.yaml"
    header = (
        f"# PROPOSED gold sample — seed={SEED}. Verify each row BY HAND per GOLD_SET.md,\n"
        f"# BEFORE viewing agent output, then move the finished rows into gold/gold_set.yaml.\n"
        f"# This file is a proposal only. The agent must never write gold/.\n"
    )
    dest.write_text(header + yaml.safe_dump(rows, sort_keys=False))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["stratum"]] = counts.get(r["stratum"], 0) + 1
    print(f"Proposed {len(rows)} rows (seed={SEED}) -> {dest}")
    for stratum, target in TARGETS.items():
        print(f"  {stratum:<18} {counts.get(stratum,0)}/{target}")
    print("\nNext: verify by hand, then write gold/gold_set.yaml (agents forbidden).")


if __name__ == "__main__":
    main()
