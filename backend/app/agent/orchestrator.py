# SPDX-License-Identifier: AGPL-3.0-only
"""
The evaluation-first peer loop.

This is the AWS draft's architecture made real and sequential, instead of one
prompt emitting every field at once:

    Planner            decide affect + the one pedagogical move
        |
    Peer-Reasoner      write Sol's message in peer voice (+ self-confidence)
        |
    Self-Evaluation    critique the draft against the rubric
        |  needs_revision?  --yes-->  Reasoner revises (up to MAX_REFINE)  --+
        |  <--------------------------------------------------------------- +
    Governance         deterministic gate: run Sol's code through the grader;
                       strip any full solution; flag answer-seeking / escalate
        |
    Memory             merge grasped/shaky into the persistent learner model
        |
    emit + trace       return the artifact's JSON contract; append a §6 event

Stance controls which branch runs (RQ2/H2 manipulated variable):
  peer    — full loop, governance withholding_solution gate ON (default)
  oracle  — full loop, governance allows solutions; oracle prompts used
  control — loop bypassed; returns standard support message only

Calibrated uncertainty (Step 3) adds two DISTINCT post-loop mechanisms:
  ESCALATION (capability, BOTH arms identically) — if self-evaluation is still
    under-confident after the bounded refine, re-run the reasoner at higher
    reasoning effort and re-evaluate (bounded by MAX_ESCALATE).
  ABSTENTION (stance, PEER ONLY) — if still below the abstain floor after that,
    override the turn into an honest peer-voiced abstention (intervention=
    escalate, abstained=true). Oracle never abstains; it ships its best answer.

The returned object is a superset of the artifact's contract, so the existing
glass-box UI renders it unchanged; the extra `components` block is richer
telemetry the UI can ignore or surface.
"""

from __future__ import annotations

import logging
import time

from ..config import settings
from ..core.registry import get_active_pack
from ..store import Store, make_event
from . import distress as distress_mod
from . import governance, memory, planner, reasoner, self_eval
from . import overlay as overlay_mod
from . import telemetry as tel
from .context import _latest_student_message, build_context
from .llm import LLMClient
from .prompts import ABSTAIN_MESSAGE, CONTROL_MESSAGE

logger = logging.getLogger(__name__)

_GOV_PROSE = {
    "none": "—",
    "withholding_solution": "Withholding full solution (preserve learning)",
    "redirect_answer_seeking": "Answer-seeking → redirected to reasoning",
    "encourage_tone": "Encouraging tone",
    "flag_escalate": "Flagged for instructor",
}


def _control_turn(
    payload: dict, ctx: dict, store: Store, pid: str, exercise: dict, mode: str, pack
) -> dict:
    """Bypass the peer loop for the control condition.

    No planner/reasoner/self_eval calls are made. A fixed support message is
    returned and a trace event is emitted so condition integrity is auditable.
    """
    learner = store.get_learner_state(pid)
    final = {
        "affective_state": "curious",
        "affect_reasoning": "control condition — no peer loop",
        "confidence": 1.0,
        "intervention": "observe",
        "planner_note": "control condition — standard support only",
        "self_critique": "—",
        "governance": "none",
        "memory": {"grasped": learner.get("grasped", []), "shaky": learner.get("shaky", [])},
        "message": CONTROL_MESSAGE,
        "check_question": None,
        "components": {
            "planner": {"target_concept": "—"},
            "reasoner": {"raw_confidence": 1.0},
            "self_eval": {"leak_risk": "none", "reasons": []},
            "governance": {"prose": "—", "blocked": False, "reasons": []},
            # Wellbeing softener never runs in control (no reasoner draft).
            "wellbeing_softened": False,
            # No overlay is consulted in control (no reasoner); nothing declined here.
            "overlay_declined": [],
            "refines": 0,
            # Step 3 telemetry — no loop ran, so these are null/false by design.
            "reasoning_effort": None,
            "escalated": False,
            "abstained": False,
            "confidence_trajectory": {"planner": None, "reasoner": None, "self_eval": None},
            # F6 exploratory telemetry — always null for control (no reasoner).
            "misconception_id": None,
            # Self-verifying worked examples — null for control (no reasoner).
            "worked_example": None,
            # Persistent learner model — null for control (no loop).
            "learner_model": None,
            "timings_ms": {},
            # Per-component usage (additive §6): control runs no LLM calls.
            "component_usage": {},
            "model_tiers": settings.model_tiers,
            # §6 pack-agnostic envelope: pack id + generic execution provider
            # (replaces a former domain-specific execution-backend telemetry field).
            "pack": pack.id,
            "provider": settings.provider,
            "stance": "control",
        },
    }
    trace = {
        "event": ctx["event"],
        "mode": mode,
        "stance": "control",
        "source": ctx["current_functional_model"],
        "last_result": ctx["last_result"],
        "plan": None,
        "reasoner": None,
        "self_eval": None,
        "governance": None,
        "final_message": final["message"],
        "telemetry": final["components"],
    }
    store.append_event(make_event(pid, exercise["id"], mode, "turn", trace, stance="control"))
    return final


def _distress_turn(
    ctx: dict, store: Store, pid: str, exercise: dict, mode: str, stance: str, pack
) -> dict:
    """Distress-routing short-circuit (Slice G). Deterministic, NO planner/reasoner/LLM
    call. Surfaces a kind, non-amplifying frame that routes the learner to a human and
    suppresses normal tutoring. The frame's crisis CONTENT is institution config; the
    framework supplies only neutral scaffolding (and a safe generic frame when
    unconfigured — the [FILL-IN] placeholder is never rendered). The only trace is the
    additive, content-free `distress` event (gated by DISTRESS_TRACE_ENABLED)."""
    learner = store.get_learner_state(pid)
    configured = settings.distress_configured
    final = {
        "affective_state": "distress",
        "affect_reasoning": "distress signal — routed to human support; tutoring paused",
        "confidence": 1.0,
        "intervention": "escalate",
        "planner_note": "distress routing — surfaced configured support and routed to a human",
        "self_critique": "—",
        "governance": "flag_escalate",
        "memory": {"grasped": learner.get("grasped", []), "shaky": learner.get("shaky", [])},
        "message": distress_mod.frame_from_settings(settings),
        "check_question": None,
        "components": {
            "planner": {"target_concept": "—"},
            "reasoner": {"raw_confidence": 1.0},
            "self_eval": {"leak_risk": "none", "reasons": []},
            "governance": {
                "prose": "Distress routing → human support",
                "blocked": False,
                "reasons": [],
            },
            "wellbeing_softened": False,
            "overlay_declined": [],
            # Content-free distress signal (no text, no PII, no severity, no category).
            "distress": {"triggered": True, "configured": configured, "routed": configured},
            "refines": 0,
            "reasoning_effort": None,
            "escalated": True,
            "abstained": False,
            "confidence_trajectory": {"planner": None, "reasoner": None, "self_eval": None},
            "misconception_id": None,
            "worked_example": None,
            "learner_model": None,
            "timings_ms": {},
            "component_usage": {},
            "model_tiers": settings.model_tiers,
            "pack": pack.id,
            "provider": settings.provider,
            "stance": stance,
        },
    }
    # The ONLY trace for a distress turn is the additive, content-free event: no turn
    # event, no source/message/dialogue is recorded. Payload is exactly the signal.
    if settings.distress_trace_enabled:
        store.append_event(
            make_event(
                pid,
                exercise["id"],
                mode,
                "distress",
                {"triggered": True, "configured": configured, "routed": configured},
                stance=stance,
            )
        )
    return final


def _abstain(draft: dict) -> dict:
    """Override a low-confidence PEER draft into an honest abstention.

    Replaces Sol's answer with the deterministic abstention message and caps the
    stated confidence. The concept reads (grasped/shaky — about the STUDENT, not
    Sol's own certainty) are preserved so memory still updates."""
    d = dict(draft)
    d["message"] = ABSTAIN_MESSAGE
    d["check_question"] = (
        "What does your latest run actually show — want to line it up " "against the goal together?"
    )
    d["confidence"] = min(float(draft.get("confidence", 0.3)), 0.3)
    return d


def run_turn(payload: dict, llm: LLMClient, store: Store) -> dict:
    """Activate a per-turn usage meter, then run the loop. The meter collects
    per-component latency/tokens/cost (additive §6 telemetry.component_usage)."""
    token = tel.set_meter(tel.UsageMeter())
    try:
        return _run_turn(payload, llm, store)
    finally:
        tel.reset_meter(token)


def _run_turn(payload: dict, llm: LLMClient, store: Store) -> dict:
    pid = payload["participant_id"]
    exercise = payload["exercise"]
    mode = payload.get("mode", "study")
    stance = payload.get("stance", "peer")

    pack = get_active_pack()
    # A turn MAY carry a per-learner customization overlay (the turn path accepts it
    # as input). Route it through the single floor-checking path and persist it where
    # goals ride, before the context is built, so it shapes this turn. Absent overlay
    # leaves any stored overlay untouched (behavior unchanged when not provided).
    if payload.get("overlay") is not None:
        overlay_mod.set_overlay(store, pid, payload.get("overlay"))
    learner = store.get_learner_state(pid)
    attempts = store.attempts(pid, exercise["id"])
    ctx = build_context(payload, learner, attempts)
    ctx["_exercise_full"] = exercise  # governance grades against the real target

    # Distress-routing layer of the wellbeing floor (Slice G) — OFF by default. When
    # enabled, scan the learner's latest message FIRST (before the control branch and
    # the tutoring loop, in any stance); on an explicit distress signal, short-circuit
    # to a deterministic support frame that routes to a human and suppress tutoring.
    # When disabled, no detection runs and behavior is byte-identical to today.
    if settings.distress_routing_enabled:
        student_msg = _latest_student_message(ctx.get("recent_dialogue", []))
        if distress_mod.has_distress_signal(student_msg, distress_mod.extra_terms(settings)):
            return _distress_turn(ctx, store, pid, exercise, mode, stance, pack)

    if stance == "control":
        return _control_turn(payload, ctx, store, pid, exercise, mode, pack)

    timings: dict[str, float] = {}

    def timed(label, fn, *a, **k):
        t = time.perf_counter()
        out = fn(*a, **k)
        timings[label] = round((time.perf_counter() - t) * 1000, 1)
        return out

    # --- the loop (runs for both peer and oracle; stance only changes prompts,
    #     the self-eval rubric, and which governance gates fire) ---
    plan = timed("planner_ms", planner.plan, ctx, llm, stance=stance)
    draft = timed(
        "reasoner_ms",
        reasoner.respond,
        ctx,
        plan,
        llm,
        stance=stance,
        reasoning_effort=settings.reasoner_effort_default,
    )
    evaluation = timed("self_eval_ms", self_eval.evaluate, ctx, plan, draft, llm, stance=stance)

    # (1) Bounded refine — fix a failing rubric (quality), at the default effort.
    refines = 0
    while evaluation["needs_revision"] and refines < settings.max_refine:
        draft = reasoner.respond(
            ctx,
            plan,
            llm,
            critique=evaluation,
            stance=stance,
            reasoning_effort=settings.reasoner_effort_default,
        )
        evaluation = self_eval.evaluate(ctx, plan, draft, llm, stance=stance)
        refines += 1
    timings["refines"] = refines

    # (2) ESCALATION — capability lever, IDENTICAL for peer & oracle (vary stance,
    #     hold capability). Still under-confident? re-run the reasoner at higher
    #     reasoning effort and re-evaluate, bounded by MAX_ESCALATE.
    reasoning_effort = settings.reasoner_effort_default
    escalations = 0
    while evaluation["confidence"] < settings.tau_escalate and escalations < settings.max_escalate:
        reasoning_effort = settings.reasoner_effort_escalated
        draft = reasoner.respond(
            ctx, plan, llm, critique=evaluation, stance=stance, reasoning_effort=reasoning_effort
        )
        evaluation = self_eval.evaluate(ctx, plan, draft, llm, stance=stance)
        escalations += 1
    escalated = escalations > 0
    timings["escalations"] = escalations

    # (2b) SELF-VERIFYING WORKED EXAMPLE — gated to worked_analogy turns.
    # Verification runs BEFORE confidence_trajectory capture so the final trajectory
    # reflects the draft that will actually be shown (post-retry if needed).
    # Governance still runs LAST: a verified example is non-leaking by construction
    # (verify_worked_example reuses is_goal_meeting), so it always passes the gate.
    we_telemetry = None
    if plan.get("intervention") == "worked_analogy" and draft.get("worked_example"):
        we = pack.verify_worked_example(draft["worked_example"], exercise)
        retries = 0
        while not we["ok"] and retries < settings.max_worked_example_retry:
            draft = reasoner.respond(
                ctx,
                plan,
                llm,
                critique={
                    "reasons": [f"worked_example {we['reason']}"],
                    "leak_risk": "none",
                    "self_critique": "regenerate a verifiable, non-solution example",
                },
                stance=stance,
                reasoning_effort=reasoning_effort,
            )
            evaluation = self_eval.evaluate(ctx, plan, draft, llm, stance=stance)
            we = pack.verify_worked_example(draft.get("worked_example") or {}, exercise)  # type: ignore[arg-type]  # worked_example is the WorkedExample-shaped dict
            retries += 1
        if we["ok"]:
            src = draft["worked_example"]["source"].strip()
            draft["message"] += (
                "\n\nHere's a small related example you can run:\n```\n" + src + "\n```"
            )
        else:
            draft["message"] += (
                "\n\n(Let's reason it through instead — "
                "I couldn't pin down a clean example to show.)"
            )
        we_telemetry = {
            "verified": we["ok"],
            "reason": we["reason"],
            "retries": retries,
            "shown": we["ok"],
            "claim_ok": we.get("claim_ok"),
        }
        timings["we_retries"] = retries

    # The confidence trajectory is captured HERE — from the post-loop agents and
    # before any abstention override — so it records what each agent actually
    # reported (the raw material for Step 4's calibration measures).
    confidence_trajectory = {
        "planner": round(float(plan.get("confidence", 0.0)), 2),
        "reasoner": round(float(draft.get("confidence", 0.0)), 2),
        "self_eval": round(float(evaluation.get("confidence", 0.0)), 2),
    }
    reasoner_conf = float(draft.get("confidence", 0.0))

    # (3) ABSTENTION — stance behavior, PEER ONLY. Still below the floor after
    #     refine + escalation? override into an honest, peer-voiced abstention.
    #     The override is independent of the planner's chosen move. Oracle never
    #     abstains — it ships its best answer.
    abstained = False
    if stance == "peer" and evaluation["confidence"] < settings.tau_abstain:
        draft = _abstain(draft)
        plan["intervention"] = "escalate"  # keep governance's escalate flag consistent
        abstained = True

    # Governance runs LAST, unchanged. In an abstention there is no solution to
    # strip; the no-leak gate is independent of escalate/abstain.
    #
    # THE CONTRACT BOUNDARY IS INSIDE `governance.check`, on purpose. The leak
    # DECISION travels as a request document in / verdict document out through
    # `leak_profile` (SPEC.md in the sibling verifier-contract repo); what
    # `check` returns to this response path is Belay's own `{flag, block,
    # reasons}`, which is not a verdict and does not belong in the contract
    # (SPEC.md §10). Calling the profile from here instead would push the wire
    # format into the response path and duplicate the reason-string
    # reconstruction, and it would put the `block` action — Belay's policy, not
    # the contract's judgement — on the wrong side of the call.
    gov = governance.check(ctx, plan, draft, evaluation, stance=stance)
    if gov["block"]:
        draft = governance.safe_rewrite(draft, gov, exercise)

    # Wellbeing floor — DEFENSE-IN-DEPTH, NOT a deterministic gate (peer only).
    # Tone has no ground-truth oracle, so unlike the supreme leak gate this is a
    # cautious post-hoc heuristic with false negatives by nature. It runs AFTER the
    # leak gate and only ever softens an obviously berating draft — it never relaxes
    # the gate. The realistic wellbeing protections are pre-hoc (intake is_harmful,
    # never-honor-framing, persona stance); this is the last-resort net.
    wellbeing_softened = False
    if stance == "peer":
        draft, wellbeing_softened = governance.soften_if_berating(draft)

    # Calibrated headline confidence comes from self-evaluation, but a suppressed
    # solution (or a softened berating draft) must not leave a high confidence on screen.
    headline_confidence = evaluation["confidence"]
    if gov["block"] or wellbeing_softened:
        headline_confidence = min(headline_confidence, draft["confidence"])

    mem_state = memory.update(
        store,
        pid,
        draft,
        exercise_id=exercise["id"],
        result=ctx["last_result"],
        misconception_id=draft.get("misconception_id"),
        repeated_error=bool(ctx["attempt_signals"].get("repeatedError")),
        plan=plan,
    )

    # Learner-model telemetry for the UI and §5e measures.
    new_concepts = mem_state.get("concepts", {})
    shaky_cids = [cid for cid, v in new_concepts.items() if v.get("state") == "shaky"]
    grasped_cids = [cid for cid, v in new_concepts.items() if v.get("state") == "grasped"]
    lm_telemetry = {
        "due_review": [d["id"] for d in ctx.get("due_review", [])],
        "revisit_concept": plan.get("revisit_concept"),
        "n_shaky": len(shaky_cids),
        "n_grasped": len(grasped_cids),
        "shaky_concepts": shaky_cids,
    }

    final = {
        # ---- artifact-compatible contract ----
        "affective_state": plan["affective_state"],
        "affect_reasoning": plan["affect_reasoning"],
        "confidence": round(headline_confidence, 2),  # calibrated, from self-eval
        "intervention": plan["intervention"],
        "planner_note": plan["planner_note"],
        "self_critique": evaluation["self_critique"],
        "governance": gov["flag"],
        "memory": {"grasped": mem_state["grasped"], "shaky": mem_state["shaky"]},
        "message": draft["message"],
        "check_question": draft.get("check_question"),
        "worked_example": we_telemetry,  # telemetry only; UI may ignore
        # ---- richer telemetry (UI may ignore) ----
        "components": {
            "planner": {
                "target_concept": plan["target_concept"],
                "confidence": confidence_trajectory["planner"],
            },
            "reasoner": {
                "raw_confidence": round(reasoner_conf, 2),
                "misconception_id": draft.get("misconception_id"),
            },
            "self_eval": {
                "leak_risk": evaluation["leak_risk"],
                "reasons": evaluation["reasons"],
                # Goal-alignment quality signal (additive §6; None without goals).
                "goal_alignment": evaluation.get("goal_alignment"),
            },
            "governance": {
                "prose": _GOV_PROSE.get(gov["flag"], "—"),
                "blocked": gov["block"],
                "reasons": gov["reasons"],
            },
            # Wellbeing defense-in-depth (additive §6): a post-hoc berating-softener,
            # NOT a deterministic gate. True iff an obviously berating draft was softened.
            "wellbeing_softened": wellbeing_softened,
            # Per-learner customization overlay (additive §6): which submitted fields the
            # floor check DECLINED this turn (observable; never the raw declined value).
            "overlay_declined": (ctx.get("overlay") or {}).get("declined", []),
            "refines": refines,
            # ---- calibrated-uncertainty telemetry (Step 3) ----
            "reasoning_effort": reasoning_effort,  # effort of the final reasoner draft
            "escalated": escalated,
            "abstained": abstained,
            "confidence_trajectory": confidence_trajectory,
            # ---- F6 exploratory telemetry ----
            "misconception_id": draft.get("misconception_id"),
            # ---- Self-verifying worked example telemetry ----
            "worked_example": we_telemetry,
            # ---- Persistent learner model telemetry ----
            "learner_model": lm_telemetry,
            "timings_ms": timings,
            # Per-component usage (additive §6): latency, tokens, cost per component.
            "component_usage": tel.current_meter().by_component(),  # type: ignore[union-attr]  # the per-turn meter is set by run_turn for the turn's duration
            "model_tiers": settings.model_tiers,
            # §6 pack-agnostic envelope: pack id + generic execution provider
            # (replaces a former domain-specific execution-backend telemetry field).
            "pack": pack.id,
            "provider": settings.provider,
            "stance": stance,
        },
    }

    trace = {
        "event": ctx["event"],
        "mode": mode,
        "stance": stance,
        "source": ctx["current_functional_model"],
        "last_result": ctx["last_result"],
        "plan": plan,
        "reasoner": {
            k: draft.get(k)
            for k in ("message", "confidence", "grasped", "shaky", "misconception_id")
        },
        "self_eval": evaluation,
        "governance": gov,
        "escalated": escalated,
        "abstained": abstained,
        "final_message": final["message"],
        "telemetry": final["components"],
    }
    store.append_event(make_event(pid, exercise["id"], mode, "turn", trace, stance=stance))

    # Additive §6 events (opt-in; fire only when goals/reflect apply, so default
    # behavior with no goals is unchanged and produces no extra events).
    goals_art = ctx.get("goals")
    if goals_art and goals_art.get("honored"):
        store.append_event(
            make_event(
                pid,
                exercise["id"],
                mode,
                "goal_alignment_check",
                {
                    "goal_text": goals_art.get("text"),
                    "alignment": evaluation.get("goal_alignment"),
                    "intervention": plan["intervention"],
                },
                stance=stance,
            )
        )
    if plan.get("intervention") == "reflect":
        store.append_event(
            make_event(
                pid,
                exercise["id"],
                mode,
                "reflect",
                {
                    "prompt": final["message"],
                    "goal_text": (goals_art or {}).get("text"),
                    "concept": plan.get("target_concept"),
                },
                stance=stance,
            )
        )
    # Slice F: trace the leak-over-retrieval gate when retrieval ran this turn.
    # Records counts + kept ids + dropped {id, reason} ONLY — never passage text.
    retr = ctx.get("_retrieval")
    if retr:
        store.append_event(
            make_event(
                pid,
                exercise["id"],
                mode,
                "retrieval",
                {
                    "exercise_id": retr["exercise_id"],
                    "retrieved": retr["retrieved"],
                    "kept": retr["kept"],
                    "dropped": retr["dropped"],
                },
                stance=stance,
            )
        )
    return final
