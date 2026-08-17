"""Structured JSON logging. The run log is evidence we may screenshot (CLAUDE.md §8)."""

from __future__ import annotations

import logging
import sys

import structlog


def configure(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial):  # -> structlog.BoundLogger
    return structlog.get_logger(**initial)
