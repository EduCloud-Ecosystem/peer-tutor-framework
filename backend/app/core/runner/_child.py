# SPDX-License-Identifier: AGPL-3.0-only
"""
Sandbox child bootstrap — runs INSIDE the restricted subprocess.

Sets resource limits, makes the network unreachable, then executes the target
program as ``__main__`` in the isolated working directory (the subprocess cwd).

This is intentionally tiny and dependency-free (stdlib only) so it starts fast
and has nothing of its own to exploit. See ``app/core/runner/__init__.py`` for
the threat model: this is a resource / network / isolation boundary, not
adversarial containment.
"""

import os
import sys
from typing import Any


def _set_limits() -> None:
    try:
        import resource
    except ImportError:  # non-POSIX; limits unavailable
        return

    cpu = os.environ.get("PTF_CPU_SECONDS")
    if cpu:
        c = int(cpu)
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (c, c + 1))  # type: ignore[attr-defined]
        except (ValueError, OSError):
            pass

    mem = os.environ.get("PTF_MEMORY_MB")
    if mem and int(mem) > 0:
        nbytes = int(mem) * 1024 * 1024
        for name in ("RLIMIT_AS", "RLIMIT_DATA"):
            lim = getattr(resource, name, None)
            if lim is None:
                continue
            try:
                resource.setrlimit(lim, (nbytes, nbytes))  # type: ignore[attr-defined]
            except (ValueError, OSError):
                pass


def _block_network() -> None:
    """Make outbound network unreachable from inside the child."""
    import socket

    def _blocked(*_a: Any, **_k: Any) -> None:
        raise OSError("network access is disabled in the sandbox runner")

    socket.socket = _blocked  # type: ignore[misc, assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]
    if hasattr(socket, "create_server"):
        socket.create_server = _blocked  # type: ignore[assignment]


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write("runner child: missing program path\n")
        raise SystemExit(2)
    prog = sys.argv[1]
    _set_limits()
    _block_network()
    sys.argv = [prog]
    import runpy

    runpy.run_path(prog, run_name="__main__")


if __name__ == "__main__":
    main()
