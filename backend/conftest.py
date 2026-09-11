# SPDX-License-Identifier: AGPL-3.0-only
"""
Shared pytest fixtures and helpers for all tests.

Provides:
- Test database setup with automatic table creation (supports SQLite & Postgres)
- Shared helpers (_payload, _CallStub, _events)
- Common fixtures (exercise_id, exercise)
"""

import json
import shutil
import subprocess

import pytest

from app.core.registry import get_active_pack
from app.store import Store
from app.store.models import Base

# ============================================================================
# Test Database Setup
# ============================================================================


def _docker_available() -> bool:
    """Check if Docker is available and running."""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return True
    except Exception:
        return False


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker unavailable; container tests run in CI",
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """
    Set up the test database for the entire test session.

    We intentionally USE THE EXISTING ENGINE configured by settings.
    Replacing the engine object would cause test files that use
    `from app.store.db import engine` to lose their reference and inspect the wrong database.
    """
    import app.store.db as db_module

    Base.metadata.create_all(bind=db_module.engine)

    yield

    db_module.engine.dispose()


@pytest.fixture(autouse=True)
def use_test_db_for_each_test():
    """
    Ensure a clean database state for each test.

    We simply drop and recreate all tables on the existing test engine.
    This guarantees:
    1. No test pollution (each test starts with a clean slate).
    2. The latest schema is always loaded.
    3. No detached engine references causing `got []` errors.
    """
    import app.store.db as db_module

    # 每次测试前清空并重建表，确保测试隔离和最新的 Schema
    Base.metadata.drop_all(bind=db_module.engine)
    Base.metadata.create_all(bind=db_module.engine)

    yield


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
