# SPDX-License-Identifier: AGPL-3.0-only

"""
Container-based sandbox for untrusted student code (CC-B4).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid

from . import RunnerResult
from ._utils import cleanup_workdir, collect_artifacts, prepare_workdir, safe_decode

MAX_RESULT_BYTES = 1 * 1024 * 1024


class ContainerSandbox:
    """Docker-based sandbox for executing untrusted Python code."""

    def __init__(
        self,
        cpu_seconds: int = 10,
        memory_mb: int = 256,
        wall_seconds: float = 20.0,
        use_gvisor: bool = False,
    ):
        self.cpu_seconds = cpu_seconds
        self.memory_mb = memory_mb
        self.wall_seconds = wall_seconds
        self.use_gvisor = use_gvisor

    def run(
        self, program: str, files: dict[str, str | bytes], artifacts: list[str]
    ) -> RunnerResult:
        """
        Execute the program in a container and return RunnerResult.

        Security properties:
        - Read-only root filesystem (--read-only)
        - CPU time limit via --ulimit cpu
        - Wall clock timeout via subprocess timeout
        - Process limit via --pids-limit
        - Memory limit via --memory
        - No network via --network none
        - Unique container name for reliable cleanup
        """
        workdir = prepare_workdir(program, files, prefix="ptf_container_")
        output_dir = tempfile.mkdtemp(prefix="ptf_output_")

        container_name = f"ptf-sandbox-{uuid.uuid4().hex[:8]}"

        try:
            cmd = self._build_docker_command(workdir, output_dir, container_name)

            t0 = time.perf_counter()
            timed_out = False
            error: str | None = None
            stdout: str = ""
            stderr: str = ""
            exit_code: int | None = None

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=self.wall_seconds,
                )
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                exit_code = result.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = safe_decode(exc.stdout)
                stderr = safe_decode(exc.stderr)
                error = f"container timeout after {self.wall_seconds}s"
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

            wall_ms = round((time.perf_counter() - t0) * 1000, 1)

            collected = collect_artifacts(output_dir, artifacts)

            if len(stdout) > MAX_RESULT_BYTES:
                stdout = stdout[:MAX_RESULT_BYTES] + "\n... (output truncated)"
            if len(stderr) > MAX_RESULT_BYTES:
                stderr = stderr[:MAX_RESULT_BYTES] + "\n... (output truncated)"

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
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            cleanup_workdir(workdir)
            cleanup_workdir(output_dir)

    def _build_docker_command(
        self, workdir: str, output_dir: str, container_name: str
    ) -> list[str]:
        """
        Build the Docker run command with sandbox flags.

        Security flags:
        - --read-only: root filesystem is read-only
        - --cap-drop ALL: drop all Linux capabilities
        - --security-opt no-new-privileges: prevent privilege escalation
        - --pids-limit 64: limit number of processes (fork bomb protection)
        - --memory + --memory-swap: memory limit with swap disabled
        - --ulimit cpu: CPU time limit (real CPU time, not just share)
        - --network none: empty network namespace (not just socket patch)
        - --tmpfs: writable temp space with size cap
        - --user: run as non-root user with host UID/GID mapping
        """
        image = "belay-sandbox:0.1.0"

        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000

        cmd = [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            f"{self.memory_mb}m",
            "--memory-swap",
            f"{self.memory_mb}m",
            "--cpus",
            "1.0",
            "--ulimit",
            f"cpu={self.cpu_seconds}:{self.cpu_seconds + 1}",
            "--network",
            "none",
            "--tmpfs",
            "/tmp:rw,size=64m",
            "--user",
            f"{uid}:{gid}",
            "--name",
            container_name,
            "-v",
            f"{workdir}:/workspace:ro",
            "-v",
            f"{output_dir}:/output:rw",
            "-w",
            "/workspace",
        ]

        if self.use_gvisor:
            cmd.extend(["--runtime", "runsc"])

        cmd.extend(
            [
                image,
                "python",
                "-u",
                "-I",
                "/workspace/__program__.py",
            ]
        )

        return cmd
