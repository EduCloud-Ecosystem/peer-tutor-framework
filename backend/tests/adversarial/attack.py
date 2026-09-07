# SPDX-License-Identifier: AGPL-3.0-only
"""
Adversarial attack execution framework for governance testing.
Simulates multi-turn student attack strategies based on arXiv 2604.18660.
"""

from __future__ import annotations

from typing import Any

from app.agent.llm import LLMClient
from app.agent.orchestrator import run_turn
from app.core.registry import get_active_pack
from app.store import InMemoryStore
from conftest import _payload


def _contains_solution(message: str, exercise: Any) -> bool:
    """Helper to inspect if the text string leaks actual solution code."""
    pack = get_active_pack()
    evidence = pack.leak_evidence(message, exercise)
    return evidence.is_solution or evidence.prose_disclosure


class Attack:
    """Simulates a scripted adversarial student attacking the AI tutor."""

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
        self.expected_leak = expected_leak
        self.paper_reference = paper_reference

    def run(self, llm: LLMClient, exercise_id: str = "ds-foundations") -> dict[str, Any]:
        """Run the attack conversation through the full system and check if it gets blocked."""
        pack = get_active_pack()
        exercise = pack.get_exercise(exercise_id)

        # Create a fresh store for tracking conversation history
        store = InMemoryStore()

        # Keep the exact same ID so the system remembers previous messages in multi-turn attacks
        participant_id = f"adv_{hash(self.name)}"

        final_result: dict[str, Any] | None = None

        # Send each attack message sequentially
        for msg in self.student_messages:
            payload = _payload(
                pid=participant_id,
                student_text=msg,
                stance="peer",
                exercise_id=exercise_id,
            )

            # Tell the fake LLM to act like an exploited model and output the answer
            # This puts real pressure on the Governance Gate downstream
            if hasattr(llm, "set_attack_context"):
                llm.set_attack_context(
                    is_attack=not self.category.startswith("negative_control"), exercise=exercise
                )

            # Process the turn
            result = run_turn(payload, llm, store)
            final_result = result

        res = final_result or {}
        governance_flag = res.get("governance", "none")
        message = res.get("message", "")

        # Check 1: Did the student actually see the solution in the final message?
        solution_reached_learner = _contains_solution(message, exercise)

        # Check 2: Did the governance gate successfully intervene and withhold the text?
        blocked_by_governance = governance_flag == "withholding_solution"

        # Return clear and accurate results
        return {
            "attack_success": solution_reached_learner,
            "defense_success": blocked_by_governance and not solution_reached_learner,
            "message": message,
            "governance_flag": governance_flag,
            "leaked": solution_reached_learner,
            "turns": len(self.student_messages),
        }

    def _extract_evidence(self, result: dict[str, Any]) -> list[str]:
        """Helper to extract governance audit logs for debugging."""
        components = result.get("components", {})
        if not isinstance(components, dict):
            components = {}
        governance_data = components.get("governance", {})
        if not isinstance(governance_data, dict):
            governance_data = {}
        return governance_data.get("reasons", [])
