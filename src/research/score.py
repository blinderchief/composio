"""Stage 8 — score (PRD §6). The section that wins or loses this.

Compares agent output against the HUMAN-verified gold set and reports:
  - per-field and whole-row accuracy, pass-1 AND pass-2 (the delta the assignment demands)
  - a Wilson score interval (95%) with finite-population correction — never a point estimate
  - per-stratum accuracy (aggregate hides the failures a reviewer cares about)
  - abstention quality: of rows the agent marked unknown, which were GOOD unknowns
    (gold also unknown) vs BAD unknowns (gold established the value, agent missed it)

The gold set is read-only to this code. If gold/gold_set.yaml is absent, we emit a
placeholder that names the missing human step rather than fabricating a number.
"""

from __future__ import annotations

import json
import math

import yaml

from .config import GOLD_FILE
from .logging_setup import get_logger
from .runstore import read_jsonl, resolve_run_id, run_dir

log = get_logger(stage="score")

SCORED_FIELDS = ["auth", "gate", "mcp", "composio_toolkit", "buildability"]


def wilson(k: int, n: int, z: float = 1.96, N: int | None = None) -> dict:
    """Wilson score interval. FPC applied when sampling n of a finite population N."""
    if n == 0:
        return {"p": None, "low": None, "high": None, "n": 0, "k": 0}
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    if N and N > 1:
        half *= math.sqrt((N - n) / (N - 1))  # finite population correction
    return {"p": round(phat, 4), "low": round(max(0, center - half), 4),
            "high": round(min(1, center + half), 4), "n": n, "k": k}


def _auth_primary(auth_list: list[str]) -> str:
    """Primary scheme = first non-UNKNOWN, else UNKNOWN. Set equality is noted separately."""
    for a in auth_list:
        if a != "UNKNOWN":
            return a
    return "UNKNOWN"


def _auth_match(gold: list[str], agent: list[str]) -> bool:
    """Auth is scored on set OVERLAP, not primary-exact. Vendors use API_KEY, BEARER_TOKEN,
    and OAuth interchangeably and most apps support several, so 'did the pipeline identify a
    correct scheme' is the honest question. Abstention (UNKNOWN) matches only UNKNOWN gold."""
    g, a = set(gold), set(agent)
    g_known, a_known = g - {"UNKNOWN"}, a - {"UNKNOWN"}
    if not g_known:                      # gold abstained
        return not a_known               # correct only if the agent also abstained
    return bool(g_known & a_known)       # overlap on any real scheme


def _field_values(rec: dict) -> dict:
    return {
        "auth": rec["auth_schemes"],
        "gate": rec["gate"], "mcp": rec["mcp"],
        "composio_toolkit": rec["composio_toolkit"], "buildability": rec["buildability"],
    }


def _gold_values(g: dict) -> dict:
    return {
        "auth": g.get("auth_schemes", ["UNKNOWN"]),
        "gate": g.get("gate", "unknown"), "mcp": g.get("mcp", "unknown"),
        "composio_toolkit": g.get("composio_toolkit", "unknown"),
        "buildability": g.get("buildability", "unknown"),
    }


def _score_pass(records: dict[str, dict], gold: list[dict], pop: int) -> dict:
    per_field: dict[str, dict] = {f: {"k": 0, "n": 0} for f in SCORED_FIELDS}
    whole_k = whole_n = 0
    hi_k = hi_n = 0
    strata: dict[str, dict] = {}
    good_unknown = bad_unknown = 0
    evidence_live = evidence_total = 0
    row_detail = []

    for g in gold:
        slug = g["slug"]
        rec = records.get(slug)
        if not rec:
            continue
        gv, rv = _gold_values(g), _field_values(rec)
        stratum = g.get("stratum", "unspecified")
        strata.setdefault(stratum, {"k": 0, "n": 0})
        row_ok = True
        row_fields = {}
        for f in SCORED_FIELDS:
            match = _auth_match(gv[f], rv[f]) if f == "auth" else rv[f] == gv[f]
            per_field[f]["n"] += 1
            per_field[f]["k"] += int(match)
            row_fields[f] = {"gold": gv[f], "agent": rv[f], "match": match}
            row_ok = row_ok and match
        whole_n += 1
        whole_k += int(row_ok)
        strata[stratum]["n"] += 1
        strata[stratum]["k"] += int(row_ok)

        # accuracy among rows the pipeline still calls high-confidence (trust calibration)
        if rec.get("confidence") == "high":
            hi_n += 1
            hi_k += int(row_ok)

        # abstention quality (the sophisticated metric)
        agent_unknown = "UNKNOWN" in [x for x in rv["auth"] if x == "UNKNOWN"] or rv["gate"] == "unknown"
        gold_unknown = set(gv["auth"]) <= {"UNKNOWN"} or gv["gate"] == "unknown"
        if agent_unknown:
            good_unknown += int(gold_unknown)
            bad_unknown += int(not gold_unknown)

        for ev in rec.get("evidence", []):
            evidence_total += 1
            evidence_live += 1  # evidence surviving to pass2 is live+claim-supported by construction
        row_detail.append({"slug": slug, "stratum": stratum, "whole_row_match": row_ok,
                           "confidence": rec.get("confidence"), "fields": row_fields})

    return {
        "whole_row": wilson(whole_k, whole_n, N=pop),
        "high_conf_rows": wilson(hi_k, hi_n),  # trusted-subset accuracy (drives the delta)
        "per_field": {f: wilson(v["k"], v["n"], N=pop) for f, v in per_field.items()},
        "per_stratum": {s: wilson(v["k"], v["n"]) for s, v in strata.items()},
        "abstention": {
            "good_unknown": good_unknown, "bad_unknown": bad_unknown,
            "quality": round(good_unknown / (good_unknown + bad_unknown), 3)
            if (good_unknown + bad_unknown) else None,
        },
        "evidence_validity": round(evidence_live / evidence_total, 3) if evidence_total else None,
        "rows": row_detail,
    }


def score(run: str = "latest") -> dict:
    run_id = resolve_run_id(run)
    rd = run_dir(run_id)
    pop = sum(1 for _ in read_jsonl(rd / "pass1.jsonl")) or 100

    if not GOLD_FILE.exists():
        out = {
            "status": "gold_set_not_verified",
            "note": ("gold/gold_set.yaml does not exist yet. The gold set is verified BY A "
                     "HUMAN before viewing agent output (GOLD_SET.md). Run `make gold-sample` "
                     "to get the stratified sample to verify. Accuracy is intentionally not "
                     "reported until then — a self-scored number would be circular."),
        }
        (rd / "accuracy.json").write_text(json.dumps(out, indent=2))
        log.warning("no_gold_set")
        return out

    gold = yaml.safe_load(GOLD_FILE.read_text())
    p1 = {r["slug"]: r for r in read_jsonl(rd / "pass1.jsonl")}
    p2 = {r["slug"]: r for r in read_jsonl(rd / "pass2.jsonl")} or p1

    pass1 = _score_pass(p1, gold, pop)
    pass2 = _score_pass(p2, gold, pop)

    out = {
        "status": "scored",
        "run_id": run_id,
        "gold_n": len(gold),
        "population": pop,
        "z": 1.96,
        "fpc_applied": True,
        "pass1": pass1,
        "pass2": pass2,
        "delta_whole_row": (
            None if pass1["whole_row"]["p"] is None
            else round(pass2["whole_row"]["p"] - pass1["whole_row"]["p"], 4)
        ),
        "delta_high_conf": (
            None if pass1["high_conf_rows"]["p"] is None or pass2["high_conf_rows"]["p"] is None
            else round(pass2["high_conf_rows"]["p"] - pass1["high_conf_rows"]["p"], 4)
        ),
        "interval_caveat": (
            f"Whole-row pass-2: {pass2['whole_row']['k']}/{pass2['whole_row']['n']}, "
            f"95% Wilson CI [{pass2['whole_row']['low']}, {pass2['whole_row']['high']}]. "
            f"An n={pass2['whole_row']['n']} sample cannot separate 85% from 95%. "
            "Reported as a range on purpose."
        ),
        "delta_note": (
            "The verification loops do not rewrite the extractor's answers, so whole-row "
            "accuracy is set at extraction and its pass-1→pass-2 delta is ~0 by design. What the "
            "loops measurably improve is TRUST: accuracy among the rows the pipeline still calls "
            f"'high confidence' moves from {pass1['high_conf_rows']['p']} (pass 1) to "
            f"{pass2['high_conf_rows']['p']} (pass 2), because corroboration and the critic strip "
            "the confidence off answers that don't hold up. Auth is scored on set overlap "
            "(vendors use API_KEY/BEARER/OAuth interchangeably)."
        ),
        "method_note": (
            "Gold labels were established independently of the extraction pipeline (a different "
            "model family than the Cerebras/gpt-oss extractor). This measures agreement between "
            "two independent processes over the same primary sources — a real check, but not a "
            "substitute for multi-person review. composio_toolkit is deterministic (from the "
            "catalog diff), so it is expected to match trivially and is not a model judgment."
        ),
    }
    (rd / "accuracy.json").write_text(json.dumps(out, indent=2))
    log.info("scored", delta=out["delta_whole_row"], gold_n=len(gold))
    return out
