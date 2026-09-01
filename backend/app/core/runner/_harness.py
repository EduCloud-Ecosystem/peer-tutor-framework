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

    # Extract standard JSON-serializable output variables
    extracted_vars = {}
    for key, value in namespace.items():
        if not key.startswith("__") and isinstance(value, (int, float, str, bool, list, dict)):
            extracted_vars[key] = value

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
        source,
        files=files or {},
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
