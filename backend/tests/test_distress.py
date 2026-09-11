# SPDX-License-Identifier: AGPL-3.0-only
"""
Slice G — distress-routing layer of the wellbeing floor (safety-critical, hermetic).

The tutor never counsels, diagnoses, assesses severity, names methods, or emits crisis
content. On an explicit distress signal (only when DISTRESS_ROUTING_ENABLED) it
short-circuits to a deterministic, kind frame that surfaces institution-configured
support and routes to a human, and suppresses normal tutoring. Off by default.
"""

from __future__ import annotations

import json
import logging

from app.agent import distress, run_turn
from app.agent import goals as goals_mod
from app.config import settings
from app.core.registry import get_active_pack
from app.store import InMemoryStore
from conftest import _CallStub, _payload

_EX = get_active_pack().get_exercise("ds-foundations")
_DISTRESS_MSG = "honestly I want to die, I can't do this anymore"
_DISTRESS_SNIPPET = "want to die"  # the distinctive input snippet (privacy probe)
_DESPAIR_MSG = "I feel hopeless and want to give up on this degree"  # NOT a crisis signal


def _events(store, pid):
    return [json.loads(l) for l in store.export_jsonl(pid).splitlines() if l]


def _enable(monkeypatch, *, configured: bool, trace: bool = True):
    monkeypatch.setattr(settings, "distress_routing_enabled", True)
    monkeypatch.setattr(settings, "distress_trace_enabled", trace)
    if configured:
        monkeypatch.setattr(
            settings,
            "distress_support_message",
            "Campus Wellbeing is available 24/7 at the student portal.",
        )
        monkeypatch.setattr(
            settings, "distress_escalation_target", "the on-call student support coordinator"
        )
    # else: leave the [FILL-IN] defaults in place


# ── detection seam: explicit-crisis-only, conservative ────────────────────────


def test_detector_triggers_on_explicit_crisis_only():
    for t in (
        "I want to die",
        "I'm going to kill myself",
        "I feel suicidal",
        "thinking about self-harm",
        "no reason to live",
        "better off dead",
    ):
        assert distress.has_distress_signal(t) is True, t
    # NEGATIVE control — academic despair / frustration / idioms must NOT trigger
    for t in (
        _DESPAIR_MSG,
        "I'm so stressed about this course",
        "this homework is killing me",
        "I want to give up on this problem",
    ):
        assert distress.has_distress_signal(t) is False, t


# ── invariant 2: disabled by default → byte-identical, no detection, no event ─


def test_disabled_default_runs_normal_tutoring_byte_identical():
    # settings.distress_routing_enabled defaults False (not enabled here).
    assert settings.distress_routing_enabled is False
    store = InMemoryStore()
    stub = _CallStub()
    out = run_turn(_payload("p_off", _DISTRESS_MSG), stub, store)
    # normal tutoring ran (planner/reasoner called) and produced the stub message
    assert "planner" in stub.roles and "reasoner" in stub.roles
    assert out["message"] == "What single number should each category collapse to?"
    # no distress detection ran: the trace carries the normal turn (+ Slice F retrieval),
    # and crucially NO distress event. Behavior is identical to pre-Slice-G.
    types = [e["event_type"] for e in _events(store, "p_off")]
    assert "distress" not in types and "turn" in types


# ── enabled + configured: route, suppress, content-free event ─────────────────


def test_enabled_configured_routes_and_suppresses(monkeypatch):
    _enable(monkeypatch, configured=True)
    store = InMemoryStore()
    stub = _CallStub()
    out = run_turn(_payload("p_cfg", _DISTRESS_MSG), stub, store)
    # tutoring SUPPRESSED — no planner/reasoner/self_eval call at all
    assert stub.roles == []
    # frame surfaces the configured support + escalation, routes to a human
    assert "Campus Wellbeing is available" in out["message"]
    assert "on-call student support coordinator" in out["message"]
    assert out["intervention"] == "escalate" and out["governance"] == "flag_escalate"
    # content-free distress event written; no normal turn event
    evs = _events(store, "p_cfg")
    assert [e["event_type"] for e in evs] == ["distress"]
    assert evs[0]["payload"] == {"triggered": True, "configured": True, "routed": True}


# ── invariant 3: enabled but NOT configured → safe generic frame, no [FILL-IN] ─


def test_enabled_unconfigured_safe_generic_no_fillin(monkeypatch, caplog):
    _enable(monkeypatch, configured=False)  # FILL-IN defaults remain
    store = InMemoryStore()
    with caplog.at_level(logging.WARNING, logger="peer_tutor.distress"):
        out = run_turn(_payload("p_unc", _DISTRESS_MSG), _CallStub(), store)
    # the FILL-IN placeholder is NEVER rendered to a learner
    assert "[FILL-IN" not in out["message"]
    # safe generic wording instead, pointing outward (not isolating)
    assert "someone you trust" in out["message"] or "support channels" in out["message"]
    # operator config warning logged (no PII, no learner text)
    assert any(r.levelno == logging.WARNING and "FILL-IN" in r.getMessage() for r in caplog.records)
    # event records configured/routed = False
    evs = _events(store, "p_unc")
    assert evs[0]["payload"] == {"triggered": True, "configured": False, "routed": False}


# ── negative control (the boundary): academic despair runs normal tutoring ────


def test_negative_control_academic_despair_does_not_route(monkeypatch):
    _enable(monkeypatch, configured=True)
    store = InMemoryStore()
    stub = _CallStub()
    out = run_turn(_payload("p_neg", _DESPAIR_MSG), stub, store)
    assert "planner" in stub.roles and "reasoner" in stub.roles  # tutoring ran
    assert out["message"] == "What single number should each category collapse to?"
    assert "distress" not in [e["event_type"] for e in _events(store, "p_neg")]


# ── intake: a distress-signaling goal is not honored and not stored verbatim ──


def test_intake_goal_distress_not_honored_not_stored(monkeypatch):
    _enable(monkeypatch, configured=True)
    store = InMemoryStore()
    art = goals_mod.set_goals(store, "p_goal", "my goal is to stop existing, I want to die")
    assert art["honored"] is False and art["floor"] == "distress"
    assert art["text"] is None  # not stored verbatim
    stored = goals_mod.get_goals(store.get_learner_state("p_goal"))
    assert stored["text"] is None
    # the distinctive distress snippet appears NOWHERE in the trace
    assert _DISTRESS_SNIPPET not in store.export_jsonl("p_goal")
    # a content-free distress event was recorded
    assert any(e["event_type"] == "distress" for e in _events(store, "p_goal"))


def test_intake_reflection_distress_not_stored(monkeypatch):
    _enable(monkeypatch, configured=True)
    store = InMemoryStore()
    refl = goals_mod.add_reflection(store, "p_refl", "I keep thinking I want to die")
    assert refl["text"] is None and refl["floor"] == "distress"
    assert _DISTRESS_SNIPPET not in store.export_jsonl("p_refl")


def test_intake_route_surfaces_frame(monkeypatch):
    """The /quad/v1/goals route surfaces the support frame on a distress signal, the
    goal is not honored, and the verbatim signal never reaches the response."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.integrations.quad import build_router
    from app.store import ConsentRouter

    _enable(monkeypatch, configured=True)
    app = FastAPI()
    app.include_router(
        build_router(ConsentRouter(InMemoryStore()), get_active_pack(), lambda: None)
    )
    r = TestClient(app).post(
        "/quad/v1/goals", json={"pseudo_id": "gh:9", "text": "I want to die, end my life"}
    )
    assert r.status_code == 200
    body = r.json()
    assert "Campus Wellbeing is available" in body["distress_support"]  # frame surfaced
    assert body["goals"]["honored"] is False and body["goals"]["text"] is None
    assert "end my life" not in r.text  # no verbatim echo


# ── privacy: no verbatim distress text and no PII in the trace ────────────────


def test_no_verbatim_distress_text_or_pii_in_trace(monkeypatch):
    _enable(monkeypatch, configured=True)
    store = InMemoryStore()
    run_turn(_payload("p_priv", _DISTRESS_MSG), _CallStub(), store)
    export = store.export_jsonl("p_priv")
    assert _DISTRESS_SNIPPET not in export  # no verbatim distress text
    assert "import pandas" not in export  # no source either (no turn row)
    evs = _events(store, "p_priv")
    # the distress event payload is EXACTLY the content-free signal — nothing else
    assert evs[0]["payload"] == {"triggered": True, "configured": True, "routed": True}
    # row shape stays the fixed 8 fields
    assert set(evs[0]) == {
        "participant_id",
        "ts",
        "exercise_id",
        "mode",
        "event_type",
        "stance",
        "payload",
        "note",
    }


# ── startup warning (Slice H follow-up): visibility for a half-armed opt-in ───


def test_startup_warning_fires_only_when_enabled_and_unconfigured(monkeypatch, caplog):
    """The startup/config-load warning fires iff routing is enabled AND unconfigured.
    Visibility only — it changes no runtime distress behavior."""
    # enabled + unconfigured -> warns
    monkeypatch.setattr(settings, "distress_routing_enabled", True)
    with caplog.at_level(logging.WARNING, logger="peer_tutor.distress"):
        assert distress.warn_if_misconfigured(settings) is True
    assert any("DISTRESS_ROUTING_ENABLED" in r.getMessage() for r in caplog.records)

    # disabled -> silent
    caplog.clear()
    monkeypatch.setattr(settings, "distress_routing_enabled", False)
    with caplog.at_level(logging.WARNING, logger="peer_tutor.distress"):
        assert distress.warn_if_misconfigured(settings) is False
    assert caplog.records == []

    # enabled + configured -> silent
    caplog.clear()
    monkeypatch.setattr(settings, "distress_routing_enabled", True)
    monkeypatch.setattr(settings, "distress_support_message", "Campus Wellbeing, portal.")
    monkeypatch.setattr(settings, "distress_escalation_target", "support coordinator")
    with caplog.at_level(logging.WARNING, logger="peer_tutor.distress"):
        assert distress.warn_if_misconfigured(settings) is False
    assert caplog.records == []


# ── trace disable: with DISTRESS_TRACE_ENABLED false, no distress event ───────


def test_trace_disabled_writes_no_distress_event(monkeypatch):
    _enable(monkeypatch, configured=True, trace=False)
    store = InMemoryStore()
    out = run_turn(_payload("p_notrace", _DISTRESS_MSG), _CallStub(), store)
    # routing still happens (frame returned), but nothing is written to the trace
    assert "Campus Wellbeing is available" in out["message"]
    assert store.export_jsonl("p_notrace").strip() == ""
