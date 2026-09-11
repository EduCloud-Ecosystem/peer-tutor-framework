# SPDX-License-Identifier: AGPL-3.0-only
"""
Host-side grading harness (CC-B4).

This module runs strictly on the HOST process outside the Docker sandbox.
It coordinates student execution in the container and evaluates results
against reference specifications on the host to prevent test leakage
and verdict spoofing.
"""

from __future__ import annotations

import json
from typing import Any

from app.core import runner

# Lightweight execution wrapper injected into the container to safely stringify student output.
_CONTAINER_EXECUTOR_STUB = """
import json
import sys
import traceback
import io
from contextlib import redirect_stdout, redirect_stderr

MAX_COLLECTION_ITEMS = 10000
MAX_DEPTH = 8

def to_jsonable(value, depth=0):
    if depth > MAX_DEPTH:
        raise TypeError("value is nested too deeply")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise TypeError("collection is too large")
        return [to_jsonable(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise TypeError("mapping is too large")
        return {str(key): to_jsonable(item, depth + 1) for key, item in value.items()}

    # NumPy arrays/scalars expose JSON-compatible values through these methods.
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return to_jsonable(tolist(), depth + 1)
    item = getattr(value, "item", None)
    if callable(item):
        return to_jsonable(item(), depth + 1)

    # Preserve enough structure for host-side dataframe checks without shipping
    # the grading spec or reference values into the container.
    if value.__class__.__name__ == "DataFrame":
        return {
            "__belay_type__": "dataframe",
            "columns": to_jsonable(list(value.columns), depth + 1),
            "data": to_jsonable(value.values.tolist(), depth + 1),
        }
    raise TypeError(f"unsupported value type: {type(value).__name__}")

def main():
    namespace = {}
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    error_msg = None

    try:
        with open("student.py", "r", encoding="utf-8") as f:
            code = f.read()
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, namespace)
    except Exception:
        error_msg = traceback.format_exc()

    # Extract outputs that can cross the container boundary as JSON.
    extracted_vars = {}
    for key, value in namespace.items():
        if key.startswith("__"):
            continue
        try:
            extracted_vars[key] = to_jsonable(value)
        except (TypeError, ValueError, OverflowError):
            pass

    payload = {
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "error": error_msg,
        "vars": extracted_vars,
    }

    try:
        with open("/output/result.json", "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as exc:
        sys.stderr.write(f"Failed to write result artifact: {exc}\\n")

if __name__ == "__main__":
    main()
"""


def run_student_in_sandbox(
    source: str,
    files: dict[str, str | bytes] | None = None,
    artifacts: list[str] | None = None,
    cpu_seconds: int = 10,
    memory_mb: int = 256,
    wall_seconds: float = 20.0,
) -> dict[str, Any]:
    """
    Execute untrusted student code inside the sandbox container.

    The host pushes `source` into the container as `student.py` alongside
    the stub wrapper. Only execution output (stdout/stderr/vars) is returned.
    Answer specs remain on the host.
    """
    staged_files = dict(files or {})
    staged_files["student.py"] = source

    runner_res = runner.run_python(
        _CONTAINER_EXECUTOR_STUB,
        files=staged_files,
        artifacts=artifacts or ["result.json"],
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
        wall_seconds=wall_seconds,
    )

    result_data = {}
    if runner_res.ok and runner_res.artifacts.get("result.json"):
        try:
            result_data = json.loads(runner_res.artifacts["result.json"])
        except json.JSONDecodeError:
            pass

    return {
        "ok": runner_res.ok,
        "exit_code": runner_res.exit_code,
        "stdout": runner_res.stdout,
        "stderr": runner_res.stderr,
        "timed_out": runner_res.timed_out,
        "error": runner_res.error,
        "artifacts": runner_res.artifacts,
        "result_data": result_data,
    }


__all__ = ["run_student_in_sandbox"]
