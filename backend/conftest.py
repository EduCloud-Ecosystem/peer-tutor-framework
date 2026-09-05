# SPDX-License-Identifier: AGPL-3.0-only
"""
Shared pytest fixtures and helpers for all tests.

Provides:
- Test database setup with automatic table creation (supports SQLite & Postgres)
- Shared helpers (_payload, _CallStub, _events)
- Common fixtures (exercise_id, exercise)
"""

import json

import pytest

from app.core.registry import get_active_pack
from app.store import Store

# ============================================================================
# Common Fixtures
# ============================================================================


@pytest.fixture
def exercise_id():
    """Return the default exercise ID used in tests."""
    return "ds-foundations"


@pytest.fixture
def exercise():
    """Return the default exercise object used in tests."""
    pack = get_active_pack()
    return pack.get_exercise("ds-foundations")


# ============================================================================
# Shared Helpers
# ============================================================================


def _payload(pid, student_text=None, stance="peer", exercise_id="ds-foundations"):
    """
    Create a payload dict for run_turn() tests.

    This helper constructs the full payload required by the tutor loop.
    It is shared across test_distress.py, test_injection_guard.py, and adversarial tests.
    """
    pack = get_active_pack()
    exercise = pack.get_exercise(exercise_id)

    recent = [{"who": "student", "text": student_text}] if student_text else []
    return {
        "participant_id": pid,
        "exercise": exercise,
        "event": "chat",
        "mode": "study",
        "stance": stance,
        "source": "import pandas as pd",
        "result": {
            "ok": True,
            "goalMet": False,
            "metric": None,
            "pack": {"id": pack.id, "summary": "0/1 checks passed"},
        },
        "recent": recent,
        "signals": None,
    }


class _CallStub:
    """
    A stub for the LLM that records which roles were called.

    This stub returns benign (non-leaking) outputs so that a non-suppressed turn
    produces a normal tutor message. Used across multiple test files.
    """

    def __init__(self):
        self.roles = []

    def json(self, *, role, tier, system, user, max_tokens=800, reasoning_effort=None):
        self.roles.append(role)

        if role == "planner":
            return {
                "affective_state": "curious",
                "affect_reasoning": "x",
                "intervention": "co_reason",
                "target_concept": "g",
                "planner_note": "n",
                "confidence": 0.8,
            }
        if role == "reasoner":
            return {
                "message": "What single number should each category collapse to?",
                "check_question": None,
                "confidence": 0.8,
                "grasped": [],
                "shaky": [],
            }
        # default to self_eval role response
        return {
            "needs_revision": False,
            "confidence": 0.8,
            "leak_risk": "none",
            "self_critique": "ok",
            "reasons": [],
        }


def _events(store: Store, pid: str) -> list[dict]:
    """
    Extract all events for a specific participant from the store.
    """
    return [json.loads(l) for l in store.export_jsonl(pid).splitlines() if l]


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "exercise_id",
    "exercise",
    "_payload",
    "_CallStub",
    "_events",
]
