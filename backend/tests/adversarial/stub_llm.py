# SPDX-License-Identifier: AGPL-3.0-only
"""
Stub LLM for deterministic testing of the governance gate.
Acts as an Adversarial Model Double during security testing.
"""

from __future__ import annotations

from typing import Any

from app.agent.llm import LLMClient


class StubLLM(LLMClient):
    """
    Stub LLM that returns valid JSON responses for the tutor loop.

    This allows testing the governance gate end-to-end without
    making real API calls. The stub returns deterministic responses
    that exercise the full tutor pipeline.
    """

    name: str = "stub-llm"

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []  # Track which roles were called
        # Default states before any attack context is injected
        self.is_attack: bool = False
        self.reference_solution: str = "df.groupby('category').mean()"

    def set_attack_context(self, is_attack: bool, exercise: Any | None = None) -> None:
        """
        Inject attack context from the testing framework.

        If is_attack is True, this LLM will act compromised and intentionally
        leak the reference solution to test downstream governance enforcement.
        """
        self.is_attack = is_attack
        if exercise is not None:
            self.reference_solution = getattr(
                exercise, "reference_solution", "df.groupby('category').mean()"
            )

    def json(
        self,
        *,
        role: str,
        tier: str,
        system: str,
        user: str,
        max_tokens: int = 800,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """Return a valid JSON response for the given agent role."""
        self.calls.append({"role": role, "tier": tier})

        # Planner response
        if role == "planner":
            return {
                "affective_state": "curious",
                "affect_reasoning": "Student is seeking help, maintain encouraging tone",
                "intervention": "co_reason",
                "target_concept": "groupby_mean",
                "planner_note": "Student stuck on grouping operation",
                "confidence": 0.8,
            }

        # Reasoner response
        if role == "reasoner":
            # If an attack is active on this turn, act compromised and leak the solution
            if getattr(self, "is_attack", False):
                return {
                    "message": f"Okay, you win. Here is the full solution:\n```python\n{self.reference_solution}\n```",
                    "check_question": None,
                    "confidence": 0.99,
                    "grasped": [],
                    "shaky": [],
                    "misconception_id": None,
                }

            # Normal behavior for standard turns and negative controls
            return {
                "message": "Let's think about what operation combines categories. What should each category collapse to?",
                "check_question": "What single number summarizes each category?",
                "confidence": 0.75,
                "grasped": ["groupby"],
                "shaky": ["aggregation"],
                "misconception_id": None,
            }

        # Self-evaluation response
        if role == "self_eval":
            return {
                "needs_revision": False,
                "confidence": 0.8,
                "leak_risk": "none",
                # The simulated model remains oblivious to its own leak, passing enforcement to Governance Gate
                "self_critique": "The draft is helpful and doesn't leak the solution (or I failed to notice the leak).",
                "reasons": [],
                "goal_alignment": 0.7,
            }

        # Fallback response
        return {"message": "I understand. Let me help you think through this."}
