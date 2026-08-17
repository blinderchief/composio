"""The critic — verification loop #4 (PRD §5.6).

A DIFFERENT model vendor reviews {record + fetched evidence} so it does not share the
extractor's priors (CLAUDE.md §4). It returns a verdict per field: agree | disagree |
insufficient, with a reason. Disagreements drive a targeted re-check in verify.py.

Provider is pluggable (CRITIC_PROVIDER=openai|google). OpenAI uses Structured Outputs;
Gemini uses response_schema. Both return the same shape.
"""

from __future__ import annotations

import json

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import get_config
from .logging_setup import get_logger

log = get_logger(stage="critic")

FIELDS = ["auth_schemes", "gate", "buildability", "mcp"]

CRITIC_SYSTEM = (
    "You are an independent verifier. Given an app record and the source documents it was "
    "built from, judge each field. Respond ONLY about what the documents support. "
    "verdict='agree' if the documents support the value; 'disagree' if they contradict it; "
    "'insufficient' if the documents don't establish it. Be skeptical of 'high' confidence."
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "verdict", "reason"],
                "properties": {
                    "field": {"enum": FIELDS},
                    "verdict": {"enum": ["agree", "disagree", "insufficient"]},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


def _prompt(record: dict, docs: list[dict]) -> str:
    ev = record.get("evidence", [])
    # Prefer the docs actually cited, keep the prompt small (cost + token-budget safety).
    cited_ids = {e["doc_id"] for e in ev}
    ranked = sorted(docs, key=lambda d: d["doc_id"] not in cited_ids)[:4]
    doc_txt = "\n\n".join(
        f"[doc {d['doc_id']} {d['url']}]\n{d['markdown'][:2500]}" for d in ranked
    )
    return (
        f"APP: {record['name']} ({record['category']})\n"
        f"CLAIMED: auth_schemes={record['auth_schemes']} gate={record['gate']} "
        f"buildability={record['buildability']} mcp={record['mcp']} "
        f"confidence={record['confidence']}\n"
        f"CITED EVIDENCE: {json.dumps(ev)}\n\n"
        f"SOURCE DOCUMENTS:\n{doc_txt or '(none)'}\n\n"
        f"Judge fields: {FIELDS}."
    )


def critique(record: dict, docs: list[dict]) -> list[dict]:
    cfg = get_config()
    if cfg.critic_provider == "google":
        return _critique_gemini(cfg, record, docs)
    return _critique_openai_compatible(cfg, record, docs)


# provider -> (base_url, key attribute). All are OpenAI-compatible; only these two differ.
# Any of them is a different model family than the Gemini extractor, so the critic stays
# independent (CLAUDE.md §4). Groq/Cerebras run gpt-oss for free and fast.
_OAI_ENDPOINTS = {
    "groq": ("https://api.groq.com/openai/v1", "groq_key", "GROQ_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "cerebras_key", "CEREBRAS_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_key", "OPENROUTER_API_KEY"),
    "openai": (None, "openai_key", "OPENAI_API_KEY"),
}


@retry(  # Groq/Cerebras free tiers rate-limit (~30 req/min); back off and retry on 429.
    retry=retry_if_exception(lambda e: "429" in str(e) or "rate" in str(e).lower()),
    wait=wait_exponential(multiplier=2, min=3, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _critique_openai_compatible(cfg, record: dict, docs: list[dict]) -> list[dict]:
    from openai import OpenAI

    base_url, key_attr, key_name = _OAI_ENDPOINTS.get(cfg.critic_provider, _OAI_ENDPOINTS["groq"])
    client = OpenAI(api_key=cfg.require(key_attr, key_name), base_url=base_url)
    resp = client.chat.completions.create(
        model=cfg.critic_model,
        max_tokens=900,  # keep well inside a small OpenRouter balance
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM
             + " Respond with JSON: {\"verdicts\":[{\"field\":..,\"verdict\":..,\"reason\":..}]}."},
            {"role": "user", "content": _prompt(record, docs)},
        ],
        response_format={"type": "json_object"},
    )
    payload = json.loads(resp.choices[0].message.content)
    verdicts = payload.get("verdicts", payload if isinstance(payload, list) else [])
    return [v for v in verdicts if v.get("field") in FIELDS]


def _critique_gemini(cfg, record: dict, docs: list[dict]) -> list[dict]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=cfg.require("gemini_key", "GEMINI_API_KEY"))
    resp = client.models.generate_content(
        model=cfg.critic_model,
        contents=_prompt(record, docs),
        config=types.GenerateContentConfig(
            system_instruction=CRITIC_SYSTEM,
            response_mime_type="application/json",
            response_schema=_SCHEMA,
        ),
    )
    return json.loads(resp.text)["verdicts"]
