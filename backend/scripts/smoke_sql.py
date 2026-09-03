#!/usr/bin/env python3
"""
Live SQL smoke test — mirrors smoke_inference.py in style.

Validates:
  (a) DB reachability  (SELECT 1; Postgres version/dialect)
  (b) Schema           (init_db() idempotent; three tables present)
  (c) Round-trip       (ConsentRouter → register_participant → save_learner_state
                        → append run+turn events with nested payload)
  (d) Read-back        (get_learner_state, attempts, export_jsonl, payload intact)
  (e) Durability       (fresh SqlStore reads the same rows)
  (f) Cleanup          (delete smoke rows so the script is re-runnable)

Each step prints [PASS] / [FAIL]; non-zero exit on any FAIL.

Usage (from repo root):
    export DATABASE_URL=postgresql+psycopg://qi:qi@localhost:5432/qimvp
    python backend/scripts/smoke_sql.py

    # or SQLite (no export needed):
    python backend/scripts/smoke_sql.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid

# Allow running from repo root OR from backend/scripts/
_HERE = os.path.dirname(os.path.abspath(__file__))
_BROOT = os.path.join(_HERE, "..")  # backend/
sys.path.insert(0, _BROOT)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./qimvp.db")
IS_PG = DATABASE_URL.startswith("postgresql")

_results: list[tuple[str, bool, str]] = []
_smoke_pids: list[str] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))
    _results.append((name, ok, detail))


def hard_fail(msg: str) -> None:
    print(f"\n  [HARD FAIL] {msg}")
    print("  Stopped.")
    sys.exit(1)


# ── a. Reachability ──────────────────────────────────────────────────────────

print("\n=== a. Reachability ===")
try:
    from sqlalchemy import create_engine, text

    _connect_args = {"check_same_thread": False} if not IS_PG else {}
    _eng = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
    with _eng.connect() as conn:
        conn.execute(text("SELECT 1"))
        if IS_PG:
            ver = conn.execute(text("SELECT version()")).scalar()
            step("Postgres reachable", True, ver[:60])
        else:
            step("SQLite reachable", True, DATABASE_URL[:60])
except Exception as e:
    hint = (
        "Is `docker compose up -d db` running? Is psycopg installed "
        "(pip install 'psycopg[binary]>=3.1')?"
        if IS_PG
        else "Check the SQLite path."
    )
    hard_fail(f"DB connect failed: {e}\n  Hint: {hint}")

if IS_PG:
    dialect = _eng.dialect.name
    step("dialect == postgresql", dialect == "postgresql", dialect)

# ── b. Schema ────────────────────────────────────────────────────────────────

print("\n=== b. Schema ===")
try:
    from app.store.db import (
        engine,  # the engine the app actually uses
        init_db,
    )

    init_db()
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {"participants", "learner_state", "events"}
    step(
        "init_db() creates all three tables", expected.issubset(tables), f"found: {sorted(tables)}"
    )
    # idempotent
    init_db()
    step("init_db() is idempotent (second call safe)", True)
except Exception as e:
    step("schema init", False, str(e))

# ── c. Round-trip via ConsentRouter → SqlStore ───────────────────────────────

print("\n=== c. Round-trip ===")

RUN = uuid.uuid4().hex[:8]
PID = f"smoke_{RUN}"
_smoke_pids.append(PID)

NESTED_PAYLOAD = {
    "event": "turn",
    "mode": "study",
    "stance": "peer",
    "final_message": "What do you think links the variables?",
    "governance": {"flag": "none", "block": False, "reasons": []},
    "telemetry": {
        "escalated": False,
        "abstained": False,
        "reasoning_effort": "medium",
        "confidence_trajectory": {"planner": 0.7, "reasoner": 0.8, "self_eval": 0.75},
        "misconception_id": "M2.1-scale-before-split",
        "stance": "peer",
    },
}

try:
    from app.store import SqlStore, make_event
    from app.store.consent import ConsentRouter

    router = ConsentRouter(SqlStore())
    router.register_participant(PID, f"code_{RUN}", consent=True)
    step("register_participant succeeds", True)

    store = router.store_for(PID)
    assert store is router.durable, "consenter should resolve to durable"
    step("store_for(consenter) is durable", True)

    store.save_learner_state(
        PID, {"grasped": ["regularization"], "shaky": ["overfitting"], "attempts": 3}
    )
    step("save_learner_state succeeds", True)

    store.append_event(
        make_event(
            PID,
            "ds-foundations",
            "study",
            "run",
            {
                "source": "import pandas as pd\ndf = pd.read_csv('data/sales.csv')",
                "result": {"ok": True, "goalMet": False, "tvd": 0.5},
            },
            stance="peer",
        )
    )
    store.append_event(
        make_event(PID, "ds-foundations", "study", "turn", NESTED_PAYLOAD, stance="peer")
    )
    step("append run + turn events", True)
except Exception as e:
    step("round-trip setup", False, str(e))

# ── d. Read-back ─────────────────────────────────────────────────────────────

print("\n=== d. Read-back ===")
try:
    from app.store import SqlStore, make_event

    s2 = SqlStore()

    state = s2.get_learner_state(PID)
    step(
        "get_learner_state round-trips",
        state["grasped"] == ["regularization"]
        and state["shaky"] == ["overfitting"]
        and state["attempts"] == 3,
        repr(state),
    )

    count = s2.attempts(PID, "ds-foundations")
    step("attempts() counts only run events", count == 1, f"got {count}")

    jsonl = s2.export_jsonl(PID)
    rows = [json.loads(l) for l in jsonl.strip().split("\n") if l.strip()]
    step("export_jsonl returns 2 rows", len(rows) == 2, f"got {len(rows)}")

    turn_row = next((r for r in rows if r.get("event_type") == "turn"), None)
    step("turn event present in export", turn_row is not None)

    if turn_row:
        tel = turn_row["payload"].get("telemetry", {})
        step(
            "nested payload intact (misconception_id)",
            tel.get("misconception_id") == "M2.1-scale-before-split",
            repr(tel.get("misconception_id")),
        )
        traj = tel.get("confidence_trajectory", {})
        step(
            "nested payload intact (confidence_trajectory)",
            traj == {"planner": 0.7, "reasoner": 0.8, "self_eval": 0.75},
            repr(traj),
        )
except Exception as e:
    step("read-back", False, str(e))

# ── e. Durability: fresh SqlStore reads the same rows ────────────────────────

print("\n=== e. Durability ===")
try:
    from app.store import SqlStore

    s3 = SqlStore()

    state3 = s3.get_learner_state(PID)
    step(
        "fresh SqlStore: learner state persists",
        state3["grasped"] == ["regularization"],
        repr(state3),
    )

    rows3 = [json.loads(l) for l in s3.export_jsonl(PID).strip().split("\n") if l.strip()]
    step("fresh SqlStore: 2 events persist", len(rows3) == 2, f"got {len(rows3)}")
except Exception as e:
    step("durability", False, str(e))

# ── f. Cleanup ───────────────────────────────────────────────────────────────

print("\n=== f. Cleanup ===")
try:
    from sqlalchemy import delete

    from app.store.db import SessionLocal
    from app.store.models import Event, LearnerState, Participant

    with SessionLocal() as s:
        for pid in _smoke_pids:
            s.execute(delete(Event).where(Event.participant_id == pid))
            s.execute(delete(LearnerState).where(LearnerState.participant_id == pid))
            s.execute(delete(Participant).where(Participant.id == pid))
        s.commit()
    step("smoke rows deleted (script re-runnable)", True)
except Exception as e:
    step("cleanup", False, str(e))

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
print("All SQL smoke checks passed.")
