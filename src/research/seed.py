"""Stage 1 — seed. Load the 100 apps from data/seed/apps.yaml (PRD §5.1).

Pure loader. The file is hand-authored; this module only validates and returns it.
"""

from __future__ import annotations

import yaml

from .config import SEED_FILE
from .models import SeedApp


def load_seed() -> list[SeedApp]:
    raw = yaml.safe_load(SEED_FILE.read_text())
    apps = [SeedApp(**row) for row in raw]
    slugs = [a.slug for a in apps]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    if dupes:
        raise ValueError(f"duplicate slugs in seed: {sorted(dupes)}")
    return apps


def seed_by_slug() -> dict[str, SeedApp]:
    return {a.slug: a for a in load_seed()}


if __name__ == "__main__":  # `python -m research.seed` — quick sanity check
    apps = load_seed()
    cats: dict[str, int] = {}
    for a in apps:
        cats[a.category] = cats.get(a.category, 0) + 1
    print(f"{len(apps)} apps across {len(cats)} categories; {sum(a.is_trap for a in apps)} traps")
    for c, n in cats.items():
        print(f"  {n:>3}  {c}")
