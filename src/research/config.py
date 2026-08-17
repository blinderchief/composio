"""Runtime configuration. Secrets come only from .env (CLAUDE.md §8).

Keys are read case-insensitively because the provided .env uses mixed casing
(`firecrawl_api`, `Tavily_api`, `ANTHROPIC_API_KEY`). We normalize once, here, so no
other module has to care.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SEED_FILE = DATA / "seed" / "apps.yaml"
CACHE_DIR = DATA / "cache"
RUNS_DIR = DATA / "runs"
FINAL_DIR = DATA / "final"
CATALOG_FILE = DATA / "composio_catalog.json"
GOLD_FILE = ROOT / "gold" / "gold_set.yaml"
SITE_DIR = ROOT / "site"


def _load_env() -> dict[str, str]:
    """Merge .env with the process environment, lower-casing every key so lookups are
    case-insensitive. Process env wins over the file."""
    merged: dict[str, str] = {}
    for k, v in dotenv_values(ROOT / ".env").items():
        if v is not None:
            merged[k.lower()] = v
    for k, v in os.environ.items():
        merged[k.lower()] = v
    return merged


@dataclass(frozen=True)
class Config:
    firecrawl_key: str | None
    tavily_key: str | None
    exa_key: str | None
    anthropic_key: str | None
    openai_key: str | None
    gemini_key: str | None
    openrouter_key: str | None
    groq_key: str | None
    cerebras_key: str | None
    composio_key: str | None
    extract_provider: str   # "google" (Gemini) | "anthropic"
    extract_model: str
    critic_provider: str    # "groq" | "cerebras" | "openrouter" | "openai" | "google"
    critic_model: str

    def require(self, attr: str, human_name: str) -> str:
        val = getattr(self, attr)
        if not val:
            raise RuntimeError(
                f"Missing {human_name}. Add it to .env (see .env.example)."
            )
        return val


def _pick(env: dict[str, str], *names: str) -> str | None:
    """Return the first present alias. Tolerant of naming drift in .env, e.g. both
    `firecrawl_api` and `firecrawl_api_key` (all keys are already lower-cased)."""
    for n in names:
        v = env.get(n)
        if v:
            return v
    return None


@lru_cache(maxsize=1)
def get_config() -> Config:
    env = _load_env()
    return Config(
        firecrawl_key=_pick(env, "firecrawl_api", "firecrawl_api_key"),
        tavily_key=_pick(env, "tavily_api", "tavily_api_key"),
        exa_key=_pick(env, "exa_api", "exa_api_key"),
        anthropic_key=_pick(env, "anthropic_api_key"),
        openai_key=_pick(env, "openai_api_key"),
        gemini_key=_pick(env, "gemini_api_key"),
        openrouter_key=_pick(env, "openrouter_api_key"),
        groq_key=_pick(env, "groq_api_key"),
        cerebras_key=_pick(env, "cerebras_api_key"),
        composio_key=_pick(env, "composio_api", "composio_api_key"),
        # This run: extractor = Gemini; critic = Groq (gpt-oss, a different model family,
        # free and working — OpenRouter has no balance). Both are overridable via env.
        extract_provider=env.get("extract_provider", "google").lower(),
        extract_model=env.get("extract_model") or env.get("gemini_model", "gemini-flash-latest"),
        critic_provider=env.get("critic_provider", "groq").lower(),
        critic_model=env.get("critic_model", "openai/gpt-oss-120b"),
    )
