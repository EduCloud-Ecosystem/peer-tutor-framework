# SPDX-License-Identifier: AGPL-3.0-only
"""
This module provides a unified interface for executing untrusted Python code
with resource limits, network isolation, and filesystem sandboxing.

THREAT MODEL (CC-B4):
  The default execution mode uses Docker containers with:
    - Empty network namespace
    - Read-only rootfs with size-capped tmpfs /tmp
    - Per-submission unprivileged UID
    - CPU-time + wall-clock limits (both required)
    - PID limit (pids-limit)
    - Memory limit with swap disabled

  For environments without Docker, falls back to subprocess runner
  only via explicit opt-in (SANDBOX_ALLOW_INSECURE=true).

core/runner — the single restricted execution path for untrusted student code.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

from app.config import settings

from ._utils import cleanup_workdir, collect_artifacts, prepare_workdir, safe_decode

_CHILD = os.path.join(os.path.dirname(__file__), "_child.py")

# Defaults are bound to global settings rather than hardcoded literals
DEFAULT_CPU_SECONDS: int = getattr(settings, "sandbox_cpu_seconds", 10)
DEFAULT_WALL_SECONDS: float = getattr(settings, "sandbox_wall_seconds", 20.0)
DEFAULT_MEMORY_MB: int = getattr(settings, "sandbox_memory_mb", 256)


@dataclass
class RunnerResult:
    """Structured outcome of a sandboxed execution."""

    ok: bool  # Process completed with exit code 0, no timeout
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    wall_ms: float
    error: str | None  # Runner-level error (timeout / spawn failure)
    artifacts: dict[str, str] = field(default_factory=dict)


def _run_container(
    program: str,
    *,
    files: dict[str, str | bytes] | None = None,
    artifacts: list[str] | None = None,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    memory_mb: int = DEFAULT_MEMORY_MB,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> RunnerResult:
    """Execute student code in a Docker container with full isolation."""
    from ._sandbox import ContainerSandbox

    sandbox = ContainerSandbox(
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
        wall_seconds=wall_seconds,
        use_gvisor=settings.sandbox_use_gvisor,
    )

    return sandbox.run(program, files=files or {}, artifacts=artifacts or [])


def _run_subprocess(
    program: str,
    *,
    files: dict[str, str | bytes] | None = None,
    artifacts: list[str] | None = None,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    memory_mb: int = DEFAULT_MEMORY_MB,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> RunnerResult:
    """Legacy subprocess-based runner (insecure, for local dev only)."""
    workdir = prepare_workdir(program, files, prefix="ptf_runner_")
    prog_path = os.path.join(workdir, "__program__.py")

    try:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": workdir,
            "TMPDIR": workdir,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PTF_CPU_SECONDS": str(cpu_seconds),
            "PTF_MEMORY_MB": str(memory_mb),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }

        t0 = time.perf_counter()
        timed_out = False
        error: str | None = None
        stdout: str = ""
        stderr: str = ""

        try:
            proc = subprocess.run(
                [sys.executable, "-I", _CHILD, prog_path],
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                errors="replace",  # Prevent thread crashes from non-UTF-8 output
                timeout=wall_seconds,
                start_new_session=True,
            )
            exit_code: int | None = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            stdout = safe_decode(exc.stdout)
            stderr = safe_decode(exc.stderr)
            error = f"wall timeout after {wall_seconds}s"
        wall_ms = round((time.perf_counter() - t0) * 1000, 1)

        collected = collect_artifacts(workdir, artifacts or [])

        ok = (not timed_out) and exit_code == 0
        return RunnerResult(
            ok=ok,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            wall_ms=wall_ms,
            error=error,
            artifacts=collected,
        )
    finally:
        cleanup_workdir(workdir)


def run_python(
    program: str,
    *,
    files: dict[str, str | bytes] | None = None,
    artifacts: list[str] | None = None,
    cpu_seconds: int | None = None,
    memory_mb: int | None = None,
    wall_seconds: float | None = None,
) -> RunnerResult:
    """
    Execute ``program`` (Python source) in the sandbox.

    Primary execution path:
    - If SANDBOX_RUNNER_ENABLED=True and Docker is available: use container.
    - If Docker is unavailable or disabled, raise RuntimeError unless SANDBOX_ALLOW_INSECURE=True.

    All student code MUST route through here.
    """
    cpu_seconds = cpu_seconds if cpu_seconds is not None else DEFAULT_CPU_SECONDS
    memory_mb = memory_mb if memory_mb is not None else DEFAULT_MEMORY_MB
    wall_seconds = wall_seconds if wall_seconds is not None else DEFAULT_WALL_SECONDS

    use_container = settings.sandbox_runner_enabled
    allow_insecure = settings.sandbox_allow_insecure

    if use_container and _docker_available():
        return _run_container(
            program,
            files=files,
            artifacts=artifacts,
            cpu_seconds=cpu_seconds,
            memory_mb=memory_mb,
            wall_seconds=wall_seconds,
        )

    # Fail closed when container mode cannot run and insecure mode is not explicitly enabled
    if not allow_insecure:
        raise RuntimeError(
            "Docker sandbox is unavailable or disabled, and SANDBOX_ALLOW_INSECURE is False. "
            "To execute code, start Docker or set SANDBOX_ALLOW_INSECURE=true for local dev."
        )

    # Fallback only when explicitly permitted
    return _run_subprocess(
        program,
        files=files,
        artifacts=artifacts,
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
        wall_seconds=wall_seconds,
    )


def _docker_available() -> bool:
    """Check if Docker daemon is available and responsive on the system."""
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


__all__ = [
    "run_python",
    "RunnerResult",
    "DEFAULT_CPU_SECONDS",
    "DEFAULT_WALL_SECONDS",
    "DEFAULT_MEMORY_MB",
]
