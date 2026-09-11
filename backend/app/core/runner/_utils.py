# SPDX-License-Identifier: AGPL-3.0-only

import os
import shutil
import tempfile


def safe_decode(data: bytes | str | None) -> str:
    """Safely decode subprocess output to string, replacing invalid characters."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _write_files_to_dir(workdir: str, program: str, files: dict[str, str | bytes] | None) -> None:
    """
    Internal helper: write program and files to a directory.
    Sets appropriate permissions for container compatibility.
    """
    # Write program
    prog_path = os.path.join(workdir, "__program__.py")
    with open(prog_path, "w", encoding="utf-8") as fh:
        fh.write(program)
    os.chmod(prog_path, 0o644)

    # Write additional files
    for name, content in (files or {}).items():
        dest = os.path.join(workdir, name)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
            os.chmod(parent, 0o777)
        if isinstance(content, bytes):
            with open(dest, "wb") as fh:
                fh.write(content)
        else:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
        os.chmod(dest, 0o644)


def prepare_workdir(
    program: str,
    files: dict[str, str | bytes] | None = None,
    prefix: str = "ptf_runner_",
) -> str:
    """
    Create a temporary working directory and write program and files.

    Returns:
        workdir_path
    """
    workdir = tempfile.mkdtemp(prefix=prefix)
    # Allow all users to read/write/execute (for container mount compatibility)
    os.chmod(workdir, 0o777)

    _write_files_to_dir(workdir, program, files)

    return workdir


def write_program_to_dir(
    workdir: str,
    program: str,
    files: dict[str, str | bytes] | None = None,
) -> None:
    """
    Write program and files to an existing directory.

    This is useful when you already have a workdir and want to populate it.
    """
    _write_files_to_dir(workdir, program, files)


def collect_artifacts(workdir: str, artifacts: list[str]) -> dict[str, str]:
    """Collect artifact files from the workdir."""
    collected: dict[str, str] = {}
    for name in artifacts or []:
        path = os.path.join(workdir, name)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    collected[name] = fh.read()
            except OSError:
                pass
    return collected


def cleanup_workdir(workdir: str) -> None:
    """Remove the temporary working directory."""
    if workdir and os.path.exists(workdir):
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = [
    "safe_decode",
    "prepare_workdir",
    "write_program_to_dir",
    "collect_artifacts",
    "cleanup_workdir",
]
