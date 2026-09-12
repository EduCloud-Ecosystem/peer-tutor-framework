# SPDX-License-Identifier: AGPL-3.0-only
"""
Adversarial attack execution framework for governance testing.
Simulates multi-turn student attack strategies based on arXiv 2604.18660.

The point of running each attack through `run_turn` (the real pipeline, never a
mock of the gate) is that the verdict must come from the same code path a real
student's messages take: planner -> reasoner -> self-eval -> governance ->
memory. The framework contributes the SCRIPTED STUDENT and the COMPROMISED
TUTOR; it does not adjudicate the leak itself.

HOW A LEAK IS DECIDED. `Attack` reuses the pack's own `leak_evidence` oracle
(the master gate's predicate) rather than a private string match, and exposes
whether the gate actually activated. Both are reported, and the regression test
asserts both, because a run can otherwise look clean for the wrong reason:

  * `leaked`     — the oracle says the hand-off reached the learner.
  * `gate_fired` — governance blocked the turn or flagged it
                   (`withholding_solution` / `redirect_answer_seeking`).

The harness proves it can still SEE a leak (and therefore that a green run means
something) via the gate-disabled detection test in
`tests/test_adversarial_leak.py`, which removes the gate and requires the oracle
to report the leak anyway.
"""

from __future__ import annotations

from typing import Any

from app.agent.llm import LLMClient
from app.agent.orchestrator import run_turn
from app.core.registry import get_active_pack
from app.store import InMemoryStore
from conftest import _payload

# Governance flags that mean the turn was intercepted rather than delivered as
# drafted. `flag_escalate` is a routing signal, not a leak interception.
_INTERCEPT_FLAGS = {"withholding_solution", "redirect_answer_seeking"}


def oracle_detects(message: str, exercise: Any) -> dict[str, Any]:
    """Run `message` through the pack's leak oracle — the gate's own predicate.

    Returns the raw evidence `{is_solution, prose_disclosure, leaked}` so a caller
    can report WHICH signal fired, not just that something did. This deliberately
    calls the same `DomainPack.leak_evidence` that `leak_profile.verify` calls, so
    the benchmark's ground truth and the gate's cannot drift apart.
    """
    if not message:
        return {"is_solution": False, "prose_disclosure": False, "leaked": False}
    pack = get_active_pack()
    evidence = pack.leak_evidence(message, exercise)
    return {
        "is_solution": bool(evidence.is_solution),
        "prose_disclosure": bool(evidence.prose_disclosure),
        "leaked": bool(evidence.is_solution or evidence.prose_disclosure),
    }


def _extract_flag(result: dict[str, Any]) -> str:
    """Governance flag from the turn result, tolerating nesting differences.

    `run_turn` returns the flag at ``governance``; the other lookups are kept so a
    nested/renamed envelope degrades to a visible flag rather than to a silent
    "none" (a false "none" is how a gate-activation assertion goes dead).
    """
    flag = (
        result.get("governance_flag")
        or result.get("governance")
        or result.get("components", {}).get("governance", {}).get("flag")
        or result.get("governance_result", {}).get("flag")
        or "none"
    )
    return str(flag) if flag else "none"


class Attack:
    """
    Simulates a scripted adversarial student attacking the AI tutor.

    Supports multi-turn conversations where the attack unfolds across
    multiple student messages. The LLM is configured to leak only on the final
    turn, to simulate incremental extraction: the earlier turns apply pressure
    and the model yields at the end of the sequence.
    """

    def __init__(
        self,
        name: str,
        category: str,
        description: str,
        student_messages: list[str],
        expected_leak: bool = False,
        paper_reference: str = "",
    ):
        self.name = name
        self.category = category
        self.description = description
        self.student_messages = student_messages
        # What the CORPUS intends this case to be: an attack that the compromised
        # tutor should fall for (`True`), or a benign control (`False`). It is a
        # declaration of intent, not an expectation about the gate's verdict — the
        # attacks are all expected to be BLOCKED, which is why `run()` instead
        # asserts that a hand-off actually happened before the gate saw it.
        self.expected_leak = expected_leak
        self.paper_reference = paper_reference

    def run(self, llm: LLMClient, exercise_id: str = "ds-foundations") -> dict[str, Any]:
        """
        Execute the attack through the full tutor pipeline.

        Args:
            llm: The LLM client (typically StubLLM for deterministic testing)
            exercise_id: ID of the exercise to attack

        Returns:
            Dictionary containing:
                - leaked: Whether the oracle says the solution reached the student
                - attack_success: Alias of `leaked` (the benchmark's primary axis)
                - blocked: Whether the gate suppressed a leaking draft
                - gate_fired: Whether governance intercepted the turn at all
                - block_reasons: The gate's reason strings (which signal caught it)
                - governance_flag: The governance response flag on the last turn
                - oracle: The evidence for the delivered message
                  ({is_solution, prose_disclosure, leaked})
                - withheld_oracle_hits: Per-turn oracle evidence collected while
                  the gate was still holding (should be empty in a green run)
                - message: The final tutor message
                - turns: Number of conversation turns
                - attack_triggered: Whether the compromised tutor actually leaked
        """
        pack = get_active_pack()
        exercise = pack.get_exercise(exercise_id)

        # Persistent store preserves conversation history across turns
        store = InMemoryStore()

        # Stable per-attack participant ID so this attack's multi-turn context is
        # its own and does not collide with another attack's history.
        participant_id = f"adv_{self.name}"

        final_result: dict[str, Any] | None = None
        # A leak the GATE let through is the finding this benchmark exists to
        # catch, so it is recorded per turn even when a later turn looks clean.
        withheld_oracle_hits: list[dict[str, Any]] = []
        blocked = False
        gate_fired = False
        block_reasons: list[str] = []
        final_flag = "none"
        attack_triggered = False

        for turn_idx, msg in enumerate(self.student_messages):
            payload = _payload(
                pid=participant_id,
                student_text=msg,
                stance="peer",
                exercise_id=exercise_id,
            )

            # Only trigger LLM leak on the final turn of an attack. This simulates
            # incremental extraction across multiple turns.
            is_final_turn = turn_idx == len(self.student_messages) - 1
            is_attack_scenario = not self.category.startswith("negative_control")

            if hasattr(llm, "set_attack_context"):
                llm.set_attack_context(
                    is_attack=(is_attack_scenario and is_final_turn),
                    exercise=exercise,
                )
            attack_triggered = attack_triggered or bool(getattr(llm, "is_attack", False))

            # Execute the turn through the real orchestration pipeline.
            result = run_turn(payload, llm, store)
            final_result = result

            # Did governance stop this turn's draft before it reached the student?
            flag = _extract_flag(result)
            gov_component = (result.get("components") or {}).get("governance") or {}
            if gov_component.get("blocked") or flag in _INTERCEPT_FLAGS:
                gate_fired = True
                final_flag = flag if flag != "none" else final_flag
            if gov_component.get("blocked"):
                blocked = True
                # The gate's reason strings are how a case can be credited to the
                # signal that actually caught it ("draft contained code that
                # solves the exercise" vs "draft prose disclosed the solution").
                for reason in gov_component.get("reasons") or []:
                    if reason not in block_reasons:
                        block_reasons.append(str(reason))

            # Did anything the gate released still disclose the solution?
            turn_oracle = oracle_detects(result.get("message", ""), exercise)
            if turn_oracle["leaked"]:
                withheld_oracle_hits.append({"turn": turn_idx, **turn_oracle})

        res = final_result or {}
        final_message = res.get("message", "")
        final_oracle = oracle_detects(final_message, exercise)

        return {
            # Primary axis. `leaked` and `attack_success` are the same measurement
            # under the names the benchmark and its report both use.
            "leaked": final_oracle["leaked"],
            "attack_success": final_oracle["leaked"],
            "blocked": blocked,
            "gate_fired": gate_fired,
            "block_reasons": block_reasons,
            "governance_flag": final_flag,
            "oracle": final_oracle,
            "withheld_oracle_hits": withheld_oracle_hits,
            "message": final_message,
            "turns": len(self.student_messages),
            "attack_triggered": attack_triggered,
        }
