# SPDX-License-Identifier: AGPL-3.0-only
"""
This module provides a unified interface for executing untrusted Python code
with resource limits, network isolation, and filesystem sandboxing.

THREAT MODEL (CC-B4):
  The default execution mode uses Docker containers with:
    - Empty network namespace (not a socket patch)
    - Read-only rootfs with size-capped tmpfs /tmp
    - Per-submission unprivileged UID
    - CPU-time + wall-clock limits (both required)
    - PID limit (pids-limit)
    - Memory limit with swap disabled

  For environments without Docker, falls back to subprocess runner
  (the original implementation) via explicit opt-in.

  T2 (gVisor) support: optional runtime=runsc configuration.

core/runner — the single restricted execution path for untrusted student code.

Every pack execution path (``run``, ``verify_worked_example``, ``leak_evidence``)
routes student code through `run_python`. Nothing executes student code in the
main process.

THREAT MODEL (stated honestly):
  This is a **resource, network, and isolation boundary, NOT adversarial
  containment.** It runs code in a separate, isolated-temp-cwd subprocess with:
    - CPU-seconds and wall-clock limits (always; wall is the hard stop),
    - an optional address-space/memory limit (RLIMIT_AS/DATA — enforced on Linux,
      best-effort on macOS where virtual-memory accounting is unreliable),
    - the network made unreachable at the process level (socket connect paths
      raise; numpy/pandas remain importable),
    - an isolated temp working directory, HOME and TMPDIR pointed at it, and
      ``python -I`` (ignore PYTHONPATH / user-site / PYTHON* env).
  A determined adversary on a shared host is NOT contained by this. The
  convergence point — and the closing step on the roadmap — is a **containerized
  runner** (matching Quad's ephemeral sandboxed graders) that adds an OS-level
  network namespace and filesystem/PID isolation. Until then this boundary is
  sized to the actual threat: a tutor or student program that is wrong, slow, or
  resource-hungry — not one mounting a sandbox escape.
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

# Defaults sized for a tutoring grader (numpy/pandas import + a tiny model).
DEFAULT_CPU_SECONDS = 10
DEFAULT_WALL_SECONDS = 20.0
DEFAULT_MEMORY_MB: int | None = 256  # 256MB default


@dataclass
class RunnerResult:
    """Structured outcome of a sandboxed execution."""

    ok: bool  # process completed with exit code 0, no timeout
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    wall_ms: float
    error: str | None  # runner-level error (timeout / spawn failure)
    artifacts: dict[str, str] = field(default_factory=dict)


def _run_container(
    program: str,
    *,
    files: dict[str, str | bytes] | None = None,
    artifacts: list[str] | None = None,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    memory_mb: int | None = DEFAULT_MEMORY_MB,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> RunnerResult:
    """Execute student code in a Docker container with full isolation."""
    from ._sandbox import ContainerSandbox

    sandbox = ContainerSandbox(
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb or 256,
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
    memory_mb: int | None = DEFAULT_MEMORY_MB,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> RunnerResult:
    """Legacy subprocess-based runner (insecure, for local dev only)."""
    workdir, prog_path = prepare_workdir(program, files, prefix="ptf_runner_")

    try:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": workdir,
            "TMPDIR": workdir,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PTF_CPU_SECONDS": str(cpu_seconds),
            "PTF_MEMORY_MB": str(memory_mb or 0),
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
                errors="replace",  # 防止非 UTF-8 字符导致读取线程崩溃
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
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    memory_mb: int | None = DEFAULT_MEMORY_MB,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> RunnerResult:
    """
    Execute ``program`` (Python source) in the sandbox.

    Primary execution path:
    - If SANDBOX_RUNNER_ENABLED=True (default): use container (Docker)
    - If SANDBOX_ALLOW_INSECURE=True: fall back to subprocess (insecure, local dev only)

    Security: If SANDBOX_RUNNER_ENABLED=True and SANDBOX_ALLOW_INSECURE=False,
    Docker must be available. Otherwise, fail closed with a clear error.

    All student code MUST come through here.
    """
    use_container = settings.sandbox_runner_enabled
    allow_insecure = settings.sandbox_allow_insecure

    if use_container and not allow_insecure:
        if not _docker_available():
            raise RuntimeError(
                "Docker is not available but SANDBOX_RUNNER_ENABLED=true and "
                "SANDBOX_ALLOW_INSECURE=false. "
                "Please either:\n"
                "  1. Start Docker Desktop\n"
                "  2. Set SANDBOX_ALLOW_INSECURE=true (insecure, local dev only)\n"
                "  3. Set SANDBOX_RUNNER_ENABLED=false (use legacy subprocess runner)"
            )
        return _run_container(
            program,
            files=files,
            artifacts=artifacts,
            cpu_seconds=cpu_seconds,
            memory_mb=memory_mb,
            wall_seconds=wall_seconds,
        )

    if allow_insecure:
        return _run_subprocess(
            program,
            files=files,
            artifacts=artifacts,
            cpu_seconds=cpu_seconds,
            memory_mb=memory_mb,
            wall_seconds=wall_seconds,
        )

    return _run_subprocess(
        program,
        files=files,
        artifacts=artifacts,
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
        wall_seconds=wall_seconds,
    )


def _docker_available() -> bool:
    """Check if Docker is available on the system."""
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
