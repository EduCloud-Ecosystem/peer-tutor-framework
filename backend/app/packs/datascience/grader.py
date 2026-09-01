# SPDX-License-Identifier: AGPL-3.0-only
"""
Spec-driven grader. Stages the exercise's declarative spec + data fixtures,
and evaluates the student source ON THE HOST by delegating to the pack's `_harness`.
"""

from __future__ import annotations

import json
import os

from ...core.domain import Exercise, RunResult
from ._harness import grade_student_code

_HERE = os.path.dirname(__file__)
_SPECS_DIR = os.path.join(_HERE, "specs")


def spec_path(exercise_id: str) -> str:
    return os.path.join(_SPECS_DIR, f"{exercise_id}.json")


def load_spec(exercise_id: str) -> dict:
    with open(spec_path(exercise_id), encoding="utf-8") as fh:
        spec: dict = json.load(fh)
        return spec


def has_spec(exercise_id: str) -> bool:
    return os.path.exists(spec_path(exercise_id))


def _stage_data(spec: dict) -> dict:
    files = {}
    for rel in spec.get("data_files", []):
        with open(os.path.join(_SPECS_DIR, rel), encoding="utf-8") as fh:
            files[rel] = fh.read()
    return files


def _summary(grade: dict) -> str:
    if grade.get("error"):
        return f"error: {grade['error']}"
    checks = grade.get("checks", [])
    n_pass = sum(1 for c in checks if c.get("ok"))
    parts = [f"{n_pass}/{len(checks)} checks passed"]
    for c in checks:
        mark = "ok" if c.get("ok") else "FAIL"
        detail = f" ({c['detail']})" if c.get("detail") else ""
        parts.append(f"{c['type']}: {mark}{detail}")
    return "; ".join(parts)


def grade(source: str, exercise: Exercise) -> RunResult:
    """Grade ``source`` against ``exercise``'s spec safely on the host."""
    ex_id = exercise.get("id", "")
    spec = load_spec(ex_id)
    data_files = _stage_data(spec)

    grade_obj = grade_student_code(
        source=source, spec=spec, exercise=exercise, data_files=data_files
    )

    if not grade_obj.get("ok") and not grade_obj.get("checks"):
        err = grade_obj.get("error") or "grader produced no verdict"
        return {
            "ok": False,
            "goalMet": False,
            "metric": None,
            "error": err,
            "pack": {
                "id": "datascience",
                "checks": [],
                "stdout": grade_obj.get("stdout", ""),
                "summary": err,
                "timed_out": False,
            },
        }

    return {
        "ok": bool(grade_obj.get("ok")),
        "goalMet": bool(grade_obj.get("goalMet")),
        "metric": grade_obj.get("metric"),
        "error": grade_obj.get("error"),
        "pack": {
            "id": "datascience",
            "checks": grade_obj.get("checks", []),
            "stdout": grade_obj.get("stdout", ""),
            "summary": _summary(grade_obj),
        },
    }
