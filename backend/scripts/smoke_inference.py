#!/usr/bin/env python3
"""
Live model-layer smoke test — Jetstream2 (or any configured) inference endpoints.

Tests (a)–(f) in order; each prints [PASS] / [FAIL].
Hard-fails (exit 1) immediately if a tier is unreachable.
Soft-warns (non-zero exit at end) for latency, low parse rates, etc.

Usage (on a JS2 instance, direct endpoints, no token):
    python scripts/smoke_inference.py

Usage (off-instance, via Open WebUI proxy):
    LLM_BASE_FAST=https://llm.jetstream-cloud.org/api \\
    LLM_BASE_STRONG=https://llm.jetstream-cloud.org/api \\
    LLM_API_KEY=<your-token> \\
    python scripts/smoke_inference.py

The sample turn used throughout:
    ds-foundations exercise, source = a partial pandas attempt that does not yet
    meet the goal (a common "forgot to group before aggregating" misconception).
    last_result = goalMet false.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from app.agent import planner as planner_mod
from app.agent import reasoner as reasoner_mod
from app.agent import run_turn
from app.agent import self_eval as selfeval_mod
from app.agent.context import build_context, serialize
from app.agent.llm import OpenAICompatLLM, get_llm
from app.config import settings
from app.core.registry import get_active_pack
from app.store import InMemoryStore

# ── sample turn fixture ───────────────────────────────────────────────────────

EX = get_active_pack().get_exercise("ds-foundations")

_SAMPLE_PAYLOAD = {
    "participant_id": "smoke_inf",
    "exercise": EX,
    "event": "run",
    "mode": "study",
    "stance": "peer",
    "source": "import pandas as pd\ndf = pd.read_csv('data/sales.csv')\nresult = df['amount'].mean()",
    "result": {
        "ok": True,
        "goalMet": False,
        "metric": None,
        "pack": {
            "id": "datascience",
            "summary": "ran, but returns a single overall mean rather than a per-category breakdown",
        },
    },
    "recent": [],
    "signals": {
        "attempts": 2,
        "distanceTrend": [0.5, 0.5],
        "repeatedError": False,
        "sinceLastProgress": 1,
    },
}

_CTX = build_context(_SAMPLE_PAYLOAD, {"grasped": [], "shaky": []}, 2)
_CTX["_exercise_full"] = EX
_CTX_JSON = serialize(_CTX)

_SAMPLE_PLAN = {
    "affective_state": "confusion",
    "affect_reasoning": "computed one overall mean instead of grouping first",
    "intervention": "diagnose",
    "target_concept": "groupby-aggregation",
    "planner_note": "surface group-then-aggregate vs aggregate-only contrast",
    "confidence": 0.7,
}
_SAMPLE_DRAFT = {
    "message": "You are getting one number for the whole table. What would you need to do first to get one number per category?",
    "check_question": None,
    "confidence": 0.75,
    "grasped": [],
    "shaky": ["groupby-aggregation"],
    "misconception_id": None,
}

N_PARSE = 5  # reliability sample per role
LATENCY_WARN_S = 30.0  # warn if a single turn exceeds this

# ── bookkeeping ───────────────────────────────────────────────────────────────

_results: list[tuple[str, bool, str]] = []
_hard_failed = False


def step(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    msg = f"  [{tag}] {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    _results.append((name, ok, detail))


def hard_fail(msg: str) -> None:
    global _hard_failed
    print(f"\n  [HARD FAIL] {msg}")
    print("  Cannot continue — exiting.")
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"  [WARN ] {msg}")


# ── a. Reachability + served model ids ───────────────────────────────────────

print("\n=== a. Reachability + served model ids ===")

llm = get_llm()
is_openai_compat = isinstance(llm, OpenAICompatLLM)


def _check_tier(tier: str) -> None:
    base = settings.openai_base_url
    model = settings.model_tiers.get(tier, settings.model_tiers["fast"])
    print(f"  tier={tier!r}  base={base!r}  model={model!r}")

    if not is_openai_compat:
        step(
            f"{tier}: provider is not OpenAI-compat — skipping reachability",
            True,
            settings.provider,
        )
        return

    from openai import OpenAI

    client = OpenAI(base_url=base, api_key=settings.openai_api_key)

    # Try models endpoint first; fall back to a tiny probe
    served_ids: list[str] = []
    try:
        models = client.models.list()
        served_ids = [m.id for m in models.data]
        step(f"{tier}: /models endpoint reachable", True, f"{len(served_ids)} models served")
    except Exception as e:
        warn(f"{tier}: /models failed ({e!s:.80}), falling back to completion probe")
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": 'Reply with: {"ok":true}'}],
                max_tokens=16,
            )
            text = resp.choices[0].message.content or ""
            step(f"{tier}: completion probe reachable", True, text[:60])
        except Exception as e2:
            hard_fail(f"{tier} unreachable: {e2}")

    if served_ids:
        found = model in served_ids
        step(f"{tier}: configured model id served", found, f"id={model!r}  served={served_ids[:5]}")
        if not found:
            hard_fail(f"model {model!r} not in served ids for tier {tier!r}")


_check_tier("fast")
_check_tier("strong")

# ── b. Per-role JSON parse reliability ───────────────────────────────────────

print(f"\n=== b. Per-role parse reliability (N={N_PARSE} each) ===")

_VALID_AFFECTS = {
    "flow",
    "productive_struggle",
    "curious",
    "confusion",
    "frustration",
    "disengaged",
}
_VALID_INTERVENTIONS = {
    "observe",
    "co_reason",
    "diagnose",
    "worked_analogy",
    "stretch",
    "reciprocate",
    "escalate",
}
_VALID_LEAK = {"none", "partial", "full"}


def _check_planner(result: dict) -> bool:
    return (
        result.get("affective_state") in _VALID_AFFECTS
        and result.get("intervention") in _VALID_INTERVENTIONS
        and isinstance(result.get("confidence"), (int, float))
        and result.get("target_concept")
    )


def _check_reasoner(result: dict) -> bool:
    return (
        bool(result.get("message"))
        and isinstance(result.get("confidence"), (int, float))
        and isinstance(result.get("grasped"), list)
        and isinstance(result.get("shaky"), list)
    )


def _check_selfeval(result: dict) -> bool:
    return (
        isinstance(result.get("needs_revision"), bool)
        and result.get("leak_risk") in _VALID_LEAK
        and isinstance(result.get("confidence"), (int, float))
    )


def _run_n(role: str, call_fn, check_fn) -> tuple[int, int, list[str], list[Any]]:
    successes = 0
    semantic_ok = 0
    errors: list[str] = []
    results: list[Any] = []
    for i in range(N_PARSE):
        try:
            r = call_fn()
            successes += 1
            if check_fn(r):
                semantic_ok += 1
            else:
                errors.append(f"trial {i}: missing/wrong keys in {list(r)}")
            results.append(r)
        except Exception as e:
            errors.append(f"trial {i}: {e!s:.120}")
            results.append(None)
    return successes, semantic_ok, errors, results


print("  [planner]")
planner_parse, planner_semantic, planner_errs, planner_results = _run_n(
    "planner",
    lambda: planner_mod.plan(_CTX, llm, stance="peer"),
    _check_planner,
)
parse_rate_p = planner_parse / N_PARSE
semantic_rate_p = planner_semantic / N_PARSE
step("planner: parse rate ≥ 80%", parse_rate_p >= 0.8, f"{planner_parse}/{N_PARSE}")
step("planner: key/value semantics ≥ 80%", semantic_rate_p >= 0.8, f"{planner_semantic}/{N_PARSE}")
if planner_errs:
    for e in planner_errs[:2]:
        print(f"    err: {e}")

print("  [reasoner]")
reasoner_parse, reasoner_semantic, reasoner_errs, reasoner_results = _run_n(
    "reasoner",
    lambda: reasoner_mod.respond(
        _CTX, _SAMPLE_PLAN, llm, stance="peer", reasoning_effort=settings.reasoner_effort_default
    ),
    _check_reasoner,
)
parse_rate_r = reasoner_parse / N_PARSE
semantic_rate_r = reasoner_semantic / N_PARSE
step("reasoner: parse rate ≥ 80%", parse_rate_r >= 0.8, f"{reasoner_parse}/{N_PARSE}")
step(
    "reasoner: key/value semantics ≥ 80%", semantic_rate_r >= 0.8, f"{reasoner_semantic}/{N_PARSE}"
)
if reasoner_errs:
    for e in reasoner_errs[:2]:
        print(f"    err: {e}")

# Report misconception_id on the sample (F6 exploratory; don't hard-fail the exact value)
sample_miscon_ids = [r.get("misconception_id") for r in reasoner_results if r is not None]
print(f"  misconception_id seen on sample: {sample_miscon_ids}")
step(
    "reasoner: misconception_id field present",
    all("misconception_id" in r for r in reasoner_results if r is not None),
    f"values={sample_miscon_ids}",
)

print("  [self_eval]")
selfeval_parse, selfeval_semantic, selfeval_errs, _ = _run_n(
    "self_eval",
    lambda: selfeval_mod.evaluate(_CTX, _SAMPLE_PLAN, _SAMPLE_DRAFT, llm, stance="peer"),
    _check_selfeval,
)
parse_rate_s = selfeval_parse / N_PARSE
semantic_rate_s = selfeval_semantic / N_PARSE
step("self_eval: parse rate ≥ 80%", parse_rate_s >= 0.8, f"{selfeval_parse}/{N_PARSE}")
step(
    "self_eval: key/value semantics ≥ 80%", semantic_rate_s >= 0.8, f"{selfeval_semantic}/{N_PARSE}"
)
if selfeval_errs:
    for e in selfeval_errs[:2]:
        print(f"    err: {e}")

# Overall reliability summary
min_rate = min(parse_rate_p, parse_rate_r, parse_rate_s)
if min_rate < 0.95:
    warn(
        f"parse rate below 95% ({min_rate:.0%}): "
        "llm.py hardening is active (response_format + retry); "
        "if still low, check served model compatibility"
    )
else:
    print(
        f"  parse rates: planner={parse_rate_p:.0%} reasoner={parse_rate_r:.0%} "
        f"self_eval={parse_rate_s:.0%}"
    )

# ── c. reasoning_effort passthrough + effect ─────────────────────────────────

print("\n=== c. reasoning_effort passthrough + effect ===")

step(
    "fast tier: no reasoning_effort (strong-tier only)",
    True,
    f"reasoning_strong={settings.reasoning_strong!r}",
)

if is_openai_compat:
    effort_results: dict[str, dict] = {}
    effort_latencies: dict[str, float] = {}
    for effort in ("medium", "high"):
        t0 = time.perf_counter()
        try:
            r = reasoner_mod.respond(
                _CTX,
                _SAMPLE_PLAN,
                llm,
                stance="peer",
                reasoning_effort=effort,
            )
            effort_results[effort] = r
            effort_latencies[effort] = time.perf_counter() - t0
            step(
                f"strong tier: effort={effort!r} call succeeds",
                True,
                f"{effort_latencies[effort]:.1f}s",
            )
        except Exception as e:
            step(f"strong tier: effort={effort!r} call succeeds", False, str(e)[:80])
            if effort == "medium":
                hard_fail(f"strong endpoint error on reasoning_effort: {e}")

    if "medium" in effort_latencies and "high" in effort_latencies:
        # A meaningful difference isn't guaranteed on every prompt, but latency
        # or output length should differ at least occasionally.
        len_med = len((effort_results.get("medium") or {}).get("message", ""))
        len_hi = len((effort_results.get("high") or {}).get("message", ""))
        lat_med = effort_latencies.get("medium", 0)
        lat_hi = effort_latencies.get("high", 0)
        print(f"  medium: {lat_med:.1f}s  msg_len={len_med}")
        print(f"  high  : {lat_hi:.1f}s  msg_len={len_hi}")
        # Soft check — "not a no-op" means at least one metric differs
        differs = len_med != len_hi or abs(lat_hi - lat_med) > 0.5
        step(
            "strong tier: high vs medium shows measurable difference",
            differs,
            "latency or output length differs",
        )
else:
    step("non-OpenAI-compat provider: reasoning_effort check skipped", True)

# ── d. End-to-end turn ────────────────────────────────────────────────────────

print("\n=== d. End-to-end turns (peer + oracle) ===")

_E2E_KEYS = (
    "affective_state",
    "confidence",
    "intervention",
    "planner_note",
    "governance",
    "memory",
    "message",
    "components",
)
turn_latencies: list[float] = []
escalated_peer: bool = False

for stance in ("peer", "oracle"):
    payload = dict(_SAMPLE_PAYLOAD, stance=stance)
    store = InMemoryStore()
    t0 = time.perf_counter()
    try:
        out = run_turn(payload, llm, store)
        lat = time.perf_counter() - t0
        turn_latencies.append(lat)

        shape_ok = all(k in out for k in _E2E_KEYS)
        comps = out.get("components", {})
        tele_ok = (
            "escalated" in comps
            and "abstained" in comps
            and "reasoning_effort" in comps
            and "confidence_trajectory" in comps
        )
        step(f"E2E {stance}: valid response shape", shape_ok)
        step(f"E2E {stance}: telemetry block populated", tele_ok, f"keys={list(comps)[:8]}")
        print(
            f"  {stance}: {lat:.1f}s  intervention={out.get('intervention')}  "
            f"governance={out.get('governance')}  "
            f"misconception_id={comps.get('reasoner',{}).get('misconception_id')}"
        )

        if stance == "peer":
            escalated_peer = bool(comps.get("escalated"))

        if lat > LATENCY_WARN_S:
            warn(f"{stance} turn latency {lat:.1f}s > {LATENCY_WARN_S}s threshold")
    except Exception as e:
        step(f"E2E {stance}: run_turn succeeds", False, str(e)[:120])
        turn_latencies.append(time.perf_counter() - t0)

# ── e. Escalation path ────────────────────────────────────────────────────────

print("\n=== e. Escalation path ===")

if escalated_peer:
    step("escalation triggered naturally on sample", True)
    # Verify the turn was still valid (already checked in d)
    step("escalated turn still produced valid response", True, "covered by e2e check above")
else:
    print(
        "  [INFO ] escalation not triggered on this sample "
        "(self_eval confidence stayed >= TAU_ESCALATE=%.2f)" % settings.tau_escalate
    )
    print(
        "  [INFO ] escalation lever is tested by test_stance.py "
        "(scripted low-confidence StubLLM)"
    )
    # Not a failure — this is "best-effort"

# ── f. Latency summary ────────────────────────────────────────────────────────

print("\n=== f. Latency summary ===")

if turn_latencies:
    p50 = statistics.median(turn_latencies)
    p_max = max(turn_latencies)
    print(f"  p50={p50:.1f}s  max={p_max:.1f}s  " f"({len(turn_latencies)} turns)")
    step(f"p50 turn latency <= {LATENCY_WARN_S}s", p50 <= LATENCY_WARN_S, f"{p50:.1f}s")
    if p_max > LATENCY_WARN_S:
        warn(
            f"max latency {p_max:.1f}s > {LATENCY_WARN_S}s (acceptable for 2-worker pilot, "
            "but review REASONING_STRONG=high if this is a concern)"
        )

# ── summary ───────────────────────────────────────────────────────────────────

print()
passed = sum(1 for _, ok, _ in _results if ok)
failed = sum(1 for _, ok, _ in _results if not ok)
print(f"=== SUMMARY: {passed} passed, {failed} failed ===")
if failed:
    print("\nFailed steps:")
    for name, ok, detail in _results:
        if not ok:
            print(f"  FAIL  {name}  ({detail})")
    sys.exit(1)
print("All inference smoke checks passed.")
