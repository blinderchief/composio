"""Single entry point for every stage (Makefile §6). `run` executes the full pipeline,
resumable: each stage skips work already present in the run's artifacts.
"""

from __future__ import annotations

import argparse
import sys

from . import logging_setup
from .logging_setup import get_logger

log = get_logger(stage="cli")


def _run_full(run: str, app: str | None, refresh: bool) -> None:
    from .catalog import build_diff
    from .discover import run_discover
    from .fetch import run_fetch
    from .extract import run_extract
    from .verify import run_verify
    from .queue import build_queue
    from .score import score
    from .render import render
    from .runstore import resolve_run_id

    run_id = resolve_run_id(run)
    log.info("pipeline_start", run_id=run_id, app=app)
    build_diff()
    run_discover(run_id, app, refresh)
    run_fetch(run_id, app, refresh)
    run_extract(run_id, app, refresh)
    run_verify(run_id, app)
    build_queue(run_id)
    score(run_id)
    render(run_id)
    log.info("pipeline_done", run_id=run_id)


def main(argv: list[str] | None = None) -> int:
    logging_setup.configure()
    p = argparse.ArgumentParser(prog="research", description="Connector Readiness Audit pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--run", default="latest")
        sp.add_argument("--app", default=None)
        sp.add_argument("--refresh", action="store_true")

    for name in ("discover", "fetch", "extract", "verify", "run"):
        add_common(sub.add_parser(name))
    for name in ("diff", "queue", "score", "render", "gold-sample"):
        sp = sub.add_parser(name)
        if name in ("queue", "score", "render"):
            sp.add_argument("--run", default="latest")

    args = p.parse_args(argv)
    cmd = args.cmd

    if cmd == "diff":
        from .catalog import build_diff
        build_diff()
    elif cmd == "discover":
        from .discover import run_discover
        run_discover(args.run, args.app, args.refresh)
    elif cmd == "fetch":
        from .fetch import run_fetch
        run_fetch(args.run, args.app, args.refresh)
    elif cmd == "extract":
        from .extract import run_extract
        run_extract(args.run, args.app, args.refresh)
    elif cmd == "verify":
        from .verify import run_verify
        run_verify(args.run, args.app)
    elif cmd == "queue":
        from .queue import build_queue
        build_queue(args.run)
    elif cmd == "score":
        from .score import score
        score(args.run)
    elif cmd == "render":
        from .render import render
        render(args.run)
    elif cmd == "gold-sample":
        from .gold_sample import main as gs
        gs()
    elif cmd == "run":
        _run_full(args.run, args.app, args.refresh)
    else:  # pragma: no cover
        p.error(f"unknown command {cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
