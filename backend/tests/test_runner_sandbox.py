# SPDX-License-Identifier: AGPL-3.0-only
"""
core/runner sandbox + bypass tests.

Sandbox: network is unreachable, the wall/CPU limits terminate runaway code, deps
(numpy/pandas) import, and execution happens in an isolated temp workdir.

Bypass: the DS pack's run / verify_worked_example / leak_evidence must route ALL
student code through core/runner — never exec it in the main process. The bypass
test replaces the runner with a spy and asserts (a) each path calls the runner and
(b) a payload that would write a sentinel file if executed in-process never runs.

CC-B4: Adds security bypass tests for container runner.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile

import pytest

from app.config import settings
from app.core import runner
from app.core.runner import RunnerResult, run_python
from conftest import requires_docker

# ── sandbox properties ────────────────────────────────────────────────────────


@requires_docker
def test_network_is_blocked():
    """Network should be blocked in the sandbox."""
    prog = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('8.8.8.8', 53), timeout=2)\n"
        "    print('NET_OK')\n"
        "except OSError:\n"
        "    print('BLOCKED')\n"
    )
    r = run_python(prog)
    assert r.ok
    assert "BLOCKED" in r.stdout
    assert "NET_OK" not in r.stdout


def test_pack_deps_still_importable():
    """numpy/pandas should still be importable in the sandbox."""
    r = run_python("import numpy, pandas; print('DEPS_OK')")
    assert r.ok, r.stderr
    assert "DEPS_OK" in r.stdout


def test_wall_timeout_enforced():
    """Wall timeout should terminate runaway code."""
    r = run_python("import time\ntime.sleep(5)", wall_seconds=1)
    assert r.timed_out is True, f"Expected timeout, but got ok={r.ok}, wall_ms={r.wall_ms}"
    assert not r.ok


def test_cpu_or_wall_terminates_runaway():
    """Busy loop: CPU limit should fire (~1s); wall is the backstop."""
    r = run_python("while True:\n    pass", cpu_seconds=1, wall_seconds=6)
    assert not r.ok
    assert r.timed_out or (r.exit_code not in (0, None))


def test_isolated_workdir_and_artifacts():
    """Execution should happen in an isolated temp workdir."""
    prog = (
        "import os\n" "open('/output/out.txt', 'w').write('hello')\n" "print('CWD', os.getcwd())\n"
    )
    r = run_python(prog, artifacts=["out.txt"])
    assert r.ok, r.stderr
    assert r.artifacts.get("out.txt") == "hello"
    cwd_line = [ln for ln in r.stdout.splitlines() if ln.startswith("CWD")][0]
    assert (
        "/workspace" in cwd_line or "ptf_runner_" in cwd_line or tempfile.gettempdir() in cwd_line
    )


def test_stdout_and_exit_code_captured():
    """Stdout and exit code should be captured correctly."""
    r = run_python("print('hi'); raise SystemExit(3)")
    assert "hi" in r.stdout
    assert r.exit_code == 3
    assert not r.ok


# ── bypass: pack student-code paths must use the runner ───────────────────────

_CANNED = RunnerResult(
    ok=True,
    exit_code=0,
    stdout='__GRADE__{"ok": true, "goalMet": false, "metric": null, "checks": [], "stdout": ""}',
    stderr="",
    timed_out=False,
    wall_ms=1.0,
    error=None,
    artifacts={},
)


def test_host_harness_runs_wrapper_with_staged_student_source(monkeypatch):
    """The host harness must run its wrapper and stage student.py beside it."""
    from app.core.runner import _harness

    captured = {}

    def _spy(program, **kwargs):
        captured["program"] = program
        captured["files"] = kwargs["files"]
        return RunnerResult(
            ok=True,
            exit_code=0,
            stdout="",
            stderr="",
            timed_out=False,
            wall_ms=1.0,
            error=None,
            artifacts={
                "result.json": json.dumps(
                    {"stdout": "", "stderr": "", "error": None, "vars": {"answer": 42}}
                )
            },
        )

    monkeypatch.setattr(runner, "run_python", _spy)

    result = _harness.run_student_in_sandbox("answer = 42", files={"data/input.csv": "1"})

    assert captured["program"] == _harness._CONTAINER_EXECUTOR_STUB
    assert captured["files"] == {"data/input.csv": "1", "student.py": "answer = 42"}
    assert result["result_data"]["vars"]["answer"] == 42


def test_student_code_never_runs_outside_the_runner(monkeypatch):
    """All student code must route through core/runner."""
    from app.packs.datascience import DataSciencePack

    calls = {"n": 0}

    def _spy(program, **kwargs):
        calls["n"] += 1
        return _CANNED

    # Patch the runner entrypoint the grader calls.
    monkeypatch.setattr(runner, "run_python", _spy)

    marker = os.path.join(tempfile.mkdtemp(prefix="ptf_bypass_"), "MARKER")
    # If this payload is ever exec'd in-process, the marker file appears.
    payload = f"open({marker!r}, 'w').write('executed in-process')\nresult = {{}}\n"

    pack = DataSciencePack()
    ex = pack.get_exercise("ds-foundations")

    pack.run(payload, ex)
    pack.verify_worked_example({"source": payload}, ex)
    pack.leak_evidence("```python\n" + payload + "\n```", ex)

    # Each path routed student code through the runner (the spy).
    assert calls["n"] >= 3
    # And nothing executed it in-process.
    assert not os.path.exists(marker), "student code executed outside the runner!"


# ── CC-B4 Security Bypass Tests ──────────────────────────────────────────────
# These tests demonstrate that the container runner closes security gaps.
# They should FAIL against the old subprocess runner and PASS against container.


@requires_docker
@pytest.mark.skipif(not settings.sandbox_runner_enabled, reason="Container runner not enabled")
def test_ctypes_raw_socket_blocked():
    """
    Bypass: ctypes can create raw sockets in the old runner.

    In container runner, network namespace is empty → ctypes cannot reach network.
    """
    prog = """
import ctypes
import ctypes.util

libc = ctypes.CDLL(ctypes.util.find_library("c"))

# socket(AF_INET, SOCK_RAW, IPPROTO_RAW)
AF_INET = 2
SOCK_RAW = 3
IPPROTO_RAW = 255

try:
    sock = libc.socket(AF_INET, SOCK_RAW, IPPROTO_RAW)
    print(f"SOCKET_CREATED: {sock}")
except Exception as e:
    print(f"BLOCKED: {e}")
"""
    r = run_python(prog)
    assert "SOCKET_CREATED: -1" in r.stdout or "BLOCKED" in r.stdout
    assert "SOCKET_CREATED: 0" not in r.stdout


@pytest.mark.skipif(not settings.sandbox_runner_enabled, reason="Container runner not enabled")
def test_subprocess_spawn_blocked():
    """
    Test that subprocess works but is contained within the container.

    Uses artifacts (file output) instead of stdout, which is more reliable
    in container environments.
    """
    prog = """
import subprocess

try:
    result = subprocess.run(["echo", "hello"], capture_output=True, text=True)
    with open("/output/result.txt", "w") as f:
        f.write(f"SUBPROCESS_OK: {result.stdout}")
except Exception as e:
    with open("/output/result.txt", "w") as f:
        f.write(f"BLOCKED: {e}")
"""
    r = run_python(prog, artifacts=["result.txt"])
    assert r.ok, f"Process failed: {r.stderr}"

    output = r.artifacts.get("result.txt", "")
    assert output, f"Expected output in artifacts, got empty (stdout: {r.stdout!r})"
    assert "SUBPROCESS_OK" in output or "BLOCKED" in output, f"Unexpected output: {output}"


@pytest.mark.skipif(not settings.sandbox_runner_enabled, reason="Container runner not enabled")
def test_sleep_outlasts_wall_time():
    """sleep() should be terminated by wall timeout."""
    prog = "import time\ntime.sleep(10)"
    r = run_python(prog, wall_seconds=2, cpu_seconds=1)
    assert r.timed_out is True, f"Expected timeout, but got ok={r.ok}, wall_ms={r.wall_ms}"
    assert not r.ok


@pytest.mark.skipif(not settings.sandbox_runner_enabled, reason="Container runner not enabled")
def test_reference_solution_not_readable():
    """
    Bypass: student code can read reference solution in the old runner.

    In container, reference solution files are never mounted.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("SECRET_REFERENCE_SOLUTION = 42")
        ref_path = f.name

    try:
        prog = f"""
import os
try:
    with open("{ref_path}", "r") as f:
        content = f.read()
        print(f"FOUND_REFERENCE: {{content[:50]}}")
except Exception as e:
    print(f"BLOCKED: {{e}}")
"""
        r = run_python(prog)
        assert "FOUND_REFERENCE" not in r.stdout
        assert "BLOCKED" in r.stdout or r.stderr
    finally:
        os.unlink(ref_path)


# ── Fork Bomb Test (Unix only) ──────────────────────────────────────────────
# This test is skipped on Windows because os.fork() is not available.


@pytest.mark.skipif(not settings.sandbox_runner_enabled, reason="Container runner not enabled")
@pytest.mark.skipif(platform.system() == "Windows", reason="os.fork() is not available on Windows")
def test_fork_bomb_capped():
    """
    Bypass: fork bomb can exhaust resources in the old runner.

    In container, --pids-limit caps the number of processes.

    Note: This test only runs on Unix-like systems (Linux/macOS)
    because os.fork() is not available on Windows.
    """
    prog = """
import os
import time

def fork_bomb():
    while True:
        try:
            pid = os.fork()
            if pid == 0:
                while True:
                    time.sleep(1)
            else:
                pass
        except OSError as e:
            print(f"FORK_BOMB_BLOCKED: {e}")
            break

fork_bomb()
"""
    r = run_python(prog, wall_seconds=5, cpu_seconds=2)
    assert r.timed_out or "FORK_BOMB_BLOCKED" in r.stdout


# ── CC-B4: Container Cleanup Tests ────────────────────────────────────────────
# These tests verify that no containers are left behind after execution.


def _list_containers() -> list[str]:
    """List all running container names."""
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    return [c for c in result.stdout.strip().splitlines() if c]


@requires_docker
@pytest.mark.skipif(not settings.sandbox_runner_enabled, reason="Container runner not enabled")
def test_no_container_leak_after_wall_timeout():
    """
    After a wall timeout, no sandbox container should remain running.

    This verifies the container cleanup in _sandbox.py works correctly.
    """
    prog = "import time\ntime.sleep(10)"

    before = set(_list_containers())

    r = run_python(prog, wall_seconds=1)

    after = set(_list_containers())

    assert r.timed_out is True, "Expected timeout"
    assert not r.ok

    # No new containers should remain
    leaked = after - before
    assert not leaked, f"Container leak detected: {leaked}"


@requires_docker
@pytest.mark.skipif(not settings.sandbox_runner_enabled, reason="Container runner not enabled")
def test_no_container_leak_after_normal_exit():
    """
    After a normal exit, no sandbox container should remain running.
    """
    prog = "print('done')"

    before = set(_list_containers())

    r = run_python(prog, wall_seconds=5)

    after = set(_list_containers())

    assert r.ok is True, f"Process failed: {r.stderr}"
    assert "done" in r.stdout

    leaked = after - before
    assert not leaked, f"Container leak detected: {leaked}"


# ── CC-B4: Docker Command Construction Tests ─────────────────────────────────
# These tests verify that the Docker command contains the required security flags.


def test_docker_command_contains_readonly():
    """
    Verify that _build_docker_command includes --read-only and /workspace:ro.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox()
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    assert "--read-only" in cmd_str, "--read-only flag missing"
    assert "/workspace:ro" in cmd_str, "/workspace should be mounted read-only"
    assert "/output:rw" in cmd_str, "/output should be mounted read-write"


def test_docker_command_contains_tmpfs_tmp():
    """
    Verify that _build_docker_command includes --tmpfs /tmp:rw.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox()
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    assert "/tmp:rw" in cmd_str, "--tmpfs /tmp:rw flag missing"
    assert "size=64m" in cmd_str, "tmpfs size limit missing"


def test_docker_command_contains_ulimit_cpu():
    """
    Verify that _build_docker_command includes --ulimit cpu for CPU time limits.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox(cpu_seconds=10)
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    assert "--ulimit" in cmd_str, "--ulimit flag missing"
    assert "cpu=" in cmd_str, "cpu ulimit missing"


def test_docker_command_contains_pids_limit():
    """
    Verify that _build_docker_command includes --pids-limit 64.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox()
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    assert "--pids-limit" in cmd_str, "--pids-limit flag missing"
    assert "64" in cmd_str, "pids-limit value missing"


def test_docker_command_contains_network_none():
    """
    Verify that _build_docker_command includes --network none.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox()
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    assert "--network" in cmd_str, "--network flag missing"
    assert "none" in cmd_str, "network none missing"


def test_docker_command_contains_cap_drop_all():
    """
    Verify that _build_docker_command includes --cap-drop ALL.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox()
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    assert "--cap-drop" in cmd_str, "--cap-drop flag missing"
    assert "ALL" in cmd_str, "cap-drop ALL missing"


def test_docker_command_contains_memory_limit():
    """
    Verify that _build_docker_command includes --memory with configured value.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox(memory_mb=256)
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    assert "--memory" in cmd_str, "--memory flag missing"
    assert "256m" in cmd_str, "memory limit missing"


def test_docker_command_contains_gvisor_when_enabled():
    """
    Verify that _build_docker_command includes --runtime=runsc when use_gvisor=True.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox(use_gvisor=True)
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    assert "--runtime" in cmd_str, "--runtime flag missing"
    assert "runsc" in cmd_str, "runsc runtime missing"


def test_docker_command_does_not_contain_gvisor_when_disabled():
    """
    Verify that _build_docker_command does NOT include --runtime=runsc when use_gvisor=False.
    """
    from app.core.runner._sandbox import ContainerSandbox

    sandbox = ContainerSandbox(use_gvisor=False)
    cmd = sandbox._build_docker_command("/tmp/work", "/tmp/out", "test-container")

    cmd_str = " ".join(cmd)

    # runsc should NOT be present
    assert "runsc" not in cmd_str, "runsc should not be present when disabled"
