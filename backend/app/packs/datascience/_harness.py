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

import io
from typing import Any

import numpy as np

from app.core.domain import Exercise
from app.core.runner._harness import run_student_in_sandbox


def grade_student_code(
    source: str,
    spec: dict[str, Any],
    exercise: Exercise | dict[str, Any],
    data_files: dict[str, str | bytes] | None = None,
    cpu_seconds: int = 10,
    memory_mb: int = 256,
    wall_seconds: float = 20.0,
) -> dict[str, Any]:
    """
    Grade student code by running it in the sandbox and evaluating on the host.
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
    execution_error = student_output.get("error")
    if execution_error:
        return {
            "ok": False,
            "goalMet": False,
            "metric": None,
            "error": execution_error,
            "checks": [],
            "stdout": student_output.get("stdout", ""),
            "stderr": student_output.get("stderr", ""),
        }

    checks = spec.get("checks", [])
    check_results = []
    goal_met = True
    metric_value = None

    for check in checks:
        try:
            check_result, primary = _run_check(check, student_output, data_files or {})
        except Exception as exc:  # noqa: BLE001
            check_result = {
                "ok": False,
                "type": check.get("type"),
                "detail": f"{type(exc).__name__}: {exc}"[:120],
            }
            primary = None
        check_results.append(check_result)
        if primary is not None:
            metric_value = primary
        if not check_result.get("ok", False):
            goal_met = False

    return {
        "ok": True,
        "goalMet": goal_met,
        "metric": metric_value,
        "error": None,
        "checks": check_results,
        "stdout": student_output.get("stdout", ""),
        "stderr": student_output.get("stderr", ""),
    }


def _run_check(
    check: dict[str, Any],
    student_output: dict[str, Any],
    data_files: dict[str, str | bytes],
) -> tuple[dict[str, Any], float | None]:
    """Execute a single grading check against student output on the host."""
    check_type = check.get("type")

    if check_type == "var_numeric":
        return _check_var_numeric(check, student_output), None
    if check_type == "stdout_contains":
        return _check_stdout_contains(check, student_output), None
    if check_type == "stdout_equals":
        return _check_stdout_equals(check, student_output), None
    if check_type == "var_dataframe":
        return _check_var_dataframe(check, student_output), None
    if check_type == "metric_threshold":
        result, metric = _check_metric_threshold(check, student_output, data_files)
        return result, metric if check.get("primary") else None
    if check_type == "var_threshold":
        result, value = _check_var_threshold(check, student_output)
        return result, value if check.get("primary") else None
    return {
        "ok": False,
        "type": check_type,
        "detail": f"Unknown or unsupported check type: {check_type}",
    }, None


def _vars(student_output: dict[str, Any]) -> dict[str, Any]:
    values = student_output.get("vars", {})
    return values if isinstance(values, dict) else {}


def _cmp(value: float, op: str, threshold: float) -> bool:
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == "==":
        return value == threshold
    raise ValueError(f"unknown op {op!r}")


def _metric(name: str, y_true: Any, y_pred: Any) -> float:
    truth = np.asarray(y_true, dtype=float).ravel()
    prediction = np.asarray(y_pred, dtype=float).ravel()
    if truth.shape != prediction.shape:
        raise ValueError(f"shape mismatch: truth {truth.shape} vs pred {prediction.shape}")
    if name == "r2":
        ss_res = float(((truth - prediction) ** 2).sum())
        ss_tot = float(((truth - truth.mean()) ** 2).sum())
        return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    if name == "mse":
        return float(((truth - prediction) ** 2).mean())
    if name == "mae":
        return float(np.abs(truth - prediction).mean())
    if name == "accuracy":
        return float((truth == prediction).mean())
    raise ValueError(f"unknown metric {name!r}")


def _check_var_numeric(check: dict[str, Any], student_output: dict[str, Any]) -> dict[str, Any]:
    """Check numeric variable against expected value."""
    var = check.get("var")
    expected = check.get("expected")
    tol = check.get("tol", 1e-6)

    if not isinstance(var, str):
        return {
            "ok": False,
            "type": "var_numeric",
            "detail": "Variable name missing or invalid in check spec",
        }

    actual = _vars(student_output).get(var)
    if actual is None:
        return {
            "ok": False,
            "type": "var_numeric",
            "detail": f"Variable '{var}' not found",
        }

    if isinstance(expected, dict):
        try:
            matches = isinstance(actual, dict) and all(
                abs(float(actual[key]) - float(value)) <= tol for key, value in expected.items()
            )
        except (KeyError, TypeError, ValueError):
            matches = False
    else:
        try:
            matches = expected is not None and abs(float(actual) - float(expected)) <= tol
        except (TypeError, ValueError):
            matches = False

    if not matches:
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


def _check_stdout_contains(check: dict[str, Any], student_output: dict[str, Any]) -> dict[str, Any]:
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


def _check_stdout_equals(check: dict[str, Any], student_output: dict[str, Any]) -> dict[str, Any]:
    """Check if stdout equals expected text."""
    expected = check.get("text", "")
    stdout = student_output.get("stdout", "")

    if stdout.strip() == expected.strip():
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


def _check_var_dataframe(check: dict[str, Any], student_output: dict[str, Any]) -> dict[str, Any]:
    """Compare a serialized dataframe with the expected rows."""
    import pandas as pd

    var = check.get("var")
    if not isinstance(var, str):
        raise ValueError("dataframe variable name is missing or invalid")
    actual = _vars(student_output).get(var)
    if not isinstance(actual, dict) or actual.get("__belay_type__") != "dataframe":
        return {
            "ok": False,
            "type": "var_dataframe",
            "detail": f"Variable '{var}' is not a dataframe",
        }

    observed = pd.DataFrame(actual.get("data", []), columns=actual.get("columns", []))
    expected = pd.DataFrame(check.get("expected", []))
    try:
        pd.testing.assert_frame_equal(
            observed.reset_index(drop=True),
            expected.reset_index(drop=True),
            check_dtype=False,
            atol=float(check.get("tol", 1e-6)),
            check_like=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "type": "var_dataframe",
            "detail": str(exc)[:120],
        }
    return {"ok": True, "type": "var_dataframe", "detail": ""}


def _check_metric_threshold(
    check: dict[str, Any],
    student_output: dict[str, Any],
    data_files: dict[str, str | bytes],
) -> tuple[dict[str, Any], float]:
    pred_var = check.get("pred_var")
    if not isinstance(pred_var, str):
        raise ValueError("prediction variable name is missing or invalid")
    prediction = _vars(student_output).get(pred_var)
    if prediction is None:
        raise ValueError(f"Variable '{pred_var}' not found")

    truth_file = check.get("truth_file")
    if not isinstance(truth_file, str) or truth_file not in data_files:
        raise ValueError(f"truth file {truth_file!r} was not staged")
    truth_data = data_files[truth_file]
    if isinstance(truth_data, bytes):
        truth_data = truth_data.decode("utf-8")
    metric = _metric(
        check["metric"], np.loadtxt(io.StringIO(truth_data), delimiter=","), prediction
    )
    ok = _cmp(metric, check.get("op", ">="), float(check["threshold"]))
    return {
        "ok": ok,
        "type": "metric_threshold",
        "detail": f"{check['metric']}={metric:.4f}",
    }, metric


def _check_var_threshold(
    check: dict[str, Any], student_output: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    var = check.get("var")
    if not isinstance(var, str):
        raise ValueError("threshold variable name is missing or invalid")
    raw_value = _vars(student_output).get(var)
    if raw_value is None:
        raise ValueError(f"Variable '{var}' not found")
    value = float(raw_value)
    ok = _cmp(value, check.get("op", "<="), float(check["threshold"]))
    return {
        "ok": ok,
        "type": "var_threshold",
        "detail": f"{var}={value:.4f}",
    }, value


__all__ = ["grade_student_code"]
