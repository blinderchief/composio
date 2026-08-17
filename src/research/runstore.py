"""Per-run artifact I/O. Every stage is a pure function over the previous stage's file
on disk (PRD §5), which is what makes the pipeline resumable and idempotent.

Layout: data/runs/<run_id>/{candidates.jsonl, fetchlog.jsonl, pass1.jsonl, pass2.jsonl,
fixes.jsonl}. `latest` is resolved to a real timestamped id so re-runs append, not clash.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .config import RUNS_DIR


def resolve_run_id(run: str) -> str:
    """`latest` -> the newest existing run, or a fresh timestamped id if none exists."""
    if run and run != "latest":
        return run
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(p.name for p in RUNS_DIR.iterdir() if p.is_dir())
    if existing:
        return existing[-1]
    return datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")


def run_dir(run_id: str) -> Path:
    d = RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def done_slugs(path: Path, key: str = "slug") -> set[str]:
    """Slugs already present in a stage's output — used to skip completed work."""
    return {row[key] for row in read_jsonl(path) if key in row}
