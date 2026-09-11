# SPDX-License-Identifier: AGPL-3.0-only
"""
FastAPI surface for the peer-tutor framework.

Routes
  GET  /healthz                          liveness
  GET  /api/curriculum                   modules + exercises
  POST /api/run                          compile + execute + grade; logs a run event
  POST /api/sol/turn                     the evaluation-first peer loop
  POST /api/participant                  create/record consent (anonymized)
  GET  /api/session/{pid}/events.jsonl   export the §6 trace for analysis
  /quad/v1/*                             Quad tutor-seam sidecar (integrations/quad)

The active domain pack (TUTOR_PACK), store (memory|sql), and LLM client are
selected from config so the same app runs offline for dev or wired to the real
platform for a pilot.

Consent gating (DMP §3 / IRB)
  Every participant gets a durable Participant row regardless of consent.
  Events + LearnerState are routed via ConsentRouter:
    consent=True  → durable SqlStore (persists across restarts)
    consent=False → per-session ephemeral InMemoryStore (never persisted)
    unregistered  → ephemeral (fail-safe)
  The trace export reads ONLY the durable store; tutoring behaviour is
  byte-for-byte identical regardless of consent.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

from .agent import distress as distress_mod
from .agent import get_llm, run_turn
from .agent import goals as goals_mod
from .agent import overlay as overlay_mod
from .config import settings
from .core.registry import get_active_pack
from .schemas import (
    GoalRequest,
    OverlayRequest,
    ParticipantRequest,
    ParticipantResponse,
    ReflectionRequest,
    RunRequest,
    RunResult,
    SolTurnRequest,
    SolTurnResponse,
)
from .store import ConsentRouter, InMemoryStore, SqlStore, make_event

app = FastAPI(title="Peer-Tutor Framework", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- wiring (swappable via env) ----------------------------------------------
_durable = SqlStore() if settings.store_backend == "sql" else InMemoryStore()
_router = ConsentRouter(_durable)
_pack = get_active_pack()  # active DomainPack (TUTOR_PACK, default datascience)

# Slice H visibility: at startup, warn (no PII/content) if distress routing is enabled
# but unconfigured, so a half-armed opt-in is caught now, not at the first triggered
# turn. Visibility only — no runtime behavior change.
distress_mod.warn_if_misconfigured(settings)


def _llm():
    # Constructed per-process lazily; uses the configured provider
    # (openai_compatible by default). Raises a clear error if unreachable.
    if not hasattr(_llm, "_inst"):
        _llm._inst = get_llm()  # type: ignore[attr-defined]  # function-attribute memo cache (intentional)
    return _llm._inst  # type: ignore[attr-defined]  # function-attribute memo cache (intentional)


# Quad tutor-seam sidecar: a versioned /quad/v1 surface over the same tutor loop
# (Apache-2.0; pseudonymous identity; grades firewall). See integrations/quad.
from .integrations.quad import build_router as _build_quad_router  # noqa: E402

app.include_router(_build_quad_router(_router, _pack, _llm))


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "pack": _pack.id,
        "provider": settings.provider,
        "store": settings.store_backend,
    }


@app.get("/api/curriculum")
def get_curriculum():
    return {"modules": _pack.curriculum()}


@app.post("/api/run", response_model=RunResult)
def run(req: RunRequest):
    try:
        ex = _pack.get_exercise(req.exercise_id)
    except KeyError:
        raise HTTPException(404, f"unknown exercise: {req.exercise_id}") from None
    result = _pack.run(req.source, ex)
    # Route the trace event to durable or ephemeral based on consent.
    store = _router.store_for(req.participant_id)
    store.append_event(
        make_event(
            req.participant_id,
            req.exercise_id,
            "study",
            "run",
            {"source": req.source, "result": result},
        )
    )
    return result


@app.post("/api/sol/turn", response_model=SolTurnResponse)
def sol_turn(req: SolTurnRequest):
    try:
        ex = _pack.get_exercise(req.exercise_id)
    except KeyError:
        raise HTTPException(404, f"unknown exercise: {req.exercise_id}") from None
    payload = {
        "participant_id": req.participant_id,
        "exercise": ex,
        "event": req.event,
        "mode": req.mode,
        "stance": req.stance,
        "source": req.source,
        "result": req.result,
        "recent": [t.model_dump() for t in req.recent],
        "signals": req.signals,
        "request": req.request,
        "overlay": req.overlay,
    }
    # Route events + learner-state writes to durable or ephemeral by consent.
    store = _router.store_for(req.participant_id)
    try:
        return run_turn(payload, _llm(), store)
    except Exception as e:  # surface a clean error; the front-end shows a graceful note
        raise HTTPException(502, f"tutor unavailable: {e}") from e


@app.post("/api/participant", response_model=ParticipantResponse)
def participant(req: ParticipantRequest):
    pid = "p_" + uuid.uuid4().hex[:12]
    # Participant row always goes to the durable store (DMP §1).
    # register_participant also updates the in-process consent cache so the very
    # next /api/run or /api/sol/turn in this process sees the correct routing
    # without a round-trip to the DB.
    _router.register_participant(pid, req.anon_code, req.consent)
    return ParticipantResponse(id=pid, anon_code=req.anon_code, consent=req.consent)


@app.get("/api/session/{pid}/events.jsonl")
def export_events(pid: str):
    # Export reads ONLY the durable store; non-consenters have no trace by design.
    return Response(_router.durable.export_jsonl(pid), media_type="application/x-ndjson")


# --- learner-authored goals (opt-in; pseudonymous, never PII, never to grades) --


@app.post("/api/goals")
def set_goals(req: GoalRequest):
    """Set/update (empty text clears) the student's own goals. Stored
    pseudonymously on the learner model, routed by consent like all learner state."""
    store = _router.store_for(req.participant_id)
    artifact = goals_mod.set_goals(store, req.participant_id, req.text)
    resp = {"participant_id": req.participant_id, "goals": artifact}
    if artifact and artifact.get("floor") == "distress":  # Slice G: surface the frame
        resp["distress_support"] = distress_mod.frame_from_settings(settings)
    return resp


@app.get("/api/goals/{pid}")
def get_goals(pid: str):
    store = _router.store_for(pid)
    return {"participant_id": pid, "goals": goals_mod.get_goals(store.get_learner_state(pid))}


@app.post("/api/reflection")
def add_reflection(req: ReflectionRequest):
    """Record the student's reflection (their own words), linked to their current
    goal. Stored pseudonymously on the learner model; never surfaced to an instructor."""
    store = _router.store_for(req.participant_id)
    reflection = goals_mod.add_reflection(store, req.participant_id, req.text)
    resp = {"participant_id": req.participant_id, "reflection": reflection}
    if reflection and reflection.get("floor") == "distress":  # Slice G: surface the frame
        resp["distress_support"] = distress_mod.frame_from_settings(settings)
    return resp


@app.post("/api/overlay")
def set_overlay(req: OverlayRequest):
    """Set/replace the learner's customization overlay (opt-in; null/empty clears).
    Bounded knobs only; floor-checked and normalized server-side. Input, never
    authority: it shapes HOW the tutor helps, never loosens a floor. Pseudonymous,
    routed by consent like all learner state; never to grades."""
    store = _router.store_for(req.participant_id)
    artifact = overlay_mod.set_overlay(store, req.participant_id, req.overlay)
    return {"participant_id": req.participant_id, "overlay": artifact}
