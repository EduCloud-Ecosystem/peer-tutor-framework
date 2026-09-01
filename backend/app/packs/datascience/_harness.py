# SPDX-License-Identifier: AGPL-3.0-only
"""
DataScience pack grading harness.

This module contains the grading logic that runs ON THE HOST.
It is imported by core/runner/_harness.py and executes grading checks
against the student's output (which comes from the container).

Security: The grading logic runs on the host, not in the container.
Student code cannot access this logic or the reference solutions.
"""

from __future__ import annotations

from typing import Any

from app.core.runner._harness import run_student_in_sandbox


def grade_student_code(
    source: str,
    spec: dict,
    exercise: dict,
    data_files: dict[str, str | bytes] | None = None,
    cpu_seconds: int = 10,
    memory_mb: int = 256,
    wall_seconds: float = 20.0,
) -> dict[str, Any]:
    """
    Grade student code by running it in the sandbox and evaluating on the host.

    Args:
        source: Student's Python source code
        spec: Grading spec dict
        exercise: Exercise dict
        data_files: Additional data files to mount in container
        cpu_seconds: CPU time limit
        memory_mb: Memory limit
        wall_seconds: Wall clock timeout

    Returns:
        Grading result dict with keys: ok, goalMet, metric, error, checks, stdout
    """
    # 1. Prepare files for the container
    files: dict[str, str | bytes] = {}
    if data_files:
        files.update(data_files)

    # 2. Run student code in sandbox
    result = run_student_in_sandbox(
        source=source,
        files=files,
        artifacts=["result.json"],
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
        wall_seconds=wall_seconds,
    )

    if not result["ok"]:
        return {
            "ok": False,
            "goalMet": False,
            "metric": None,
            "error": result.get("error", "Student code execution failed"),
            "checks": [],
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }

    # 3. Get student output from the container
    student_output = result.get("result_data", {})
    if not student_output:
        return {
            "ok": False,
            "goalMet": False,
            "metric": None,
            "error": "No result.json produced by student code",
            "checks": [],
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }

    # 4. Run grading checks on the host (not in container!)
    checks = spec.get("checks", [])
    check_results = []
    goal_met = True

    for check in checks:
        check_result = _run_check(check, student_output)
        check_results.append(check_result)
        if not check_result.get("ok", False):
            goal_met = False

    return {
        "ok": True,
        "goalMet": goal_met,
        "metric": student_output.get("metric"),
        "error": None,
        "checks": check_results,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


def _run_check(check: dict, student_output: dict) -> dict:
    """
    Execute a single grading check against student output on the host.
    """
    check_type = check.get("type")

    if check_type == "var_numeric":
        return _check_var_numeric(check, student_output)
    elif check_type == "stdout_contains":
        return _check_stdout_contains(check, student_output)
    elif check_type == "stdout_equals":
        return _check_stdout_equals(check, student_output)
    # Add more check types as needed...
    else:
        return {
            "ok": False,
            "type": check_type,
            "detail": f"Unknown check type: {check_type}",
        }


def _check_var_numeric(check: dict, student_output: dict) -> dict:
    """Check numeric variable against expected value."""
    var = check.get("var")
    expected = check.get("expected")
    tol = check.get("tol", 1e-6)

    actual = student_output.get(var)
    if actual is None:
        return {
            "ok": False,
            "type": "var_numeric",
            "detail": f"Variable '{var}' not found",
        }

    if abs(actual - expected) > tol:
        return {
            "ok": False,
            "type": "var_numeric",
            "detail": f"{var} = {actual} != {expected}",
        }

    return {
        "ok": True,
        "type": "var_numeric",
        "detail": f"{var} == {expected}",
    }


def _check_stdout_contains(check: dict, student_output: dict) -> dict:
    """Check if stdout contains expected text."""
    expected = check.get("text", "")
    stdout = student_output.get("stdout", "")

    if expected in stdout:
        return {
            "ok": True,
            "type": "stdout_contains",
            "detail": f"stdout contains '{expected}'",
        }

    return {
        "ok": False,
        "type": "stdout_contains",
        "detail": f"stdout does not contain '{expected}'",
    }


def _check_stdout_equals(check: dict, student_output: dict) -> dict:
    """Check if stdout equals expected text."""
    expected = check.get("text", "")
    stdout = student_output.get("stdout", "")

    if stdout.strip() == expected:
        return {
            "ok": True,
            "type": "stdout_equals",
            "detail": f"stdout equals '{expected}'",
        }

    return {
        "ok": False,
        "type": "stdout_equals",
        "detail": f"stdout does not equal '{expected}'",
    }


__all__ = ["grade_student_code"]
