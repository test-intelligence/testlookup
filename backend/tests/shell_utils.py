"""Small helpers for running POSIX shell fixtures on Windows."""
from __future__ import annotations

import os
from pathlib import Path


def bash_environment(bash: str) -> dict[str, str]:
    """Return an environment where Git Bash can find its POSIX utilities."""
    env = os.environ.copy()
    if os.name != "nt":
        return env

    bash_path = Path(bash).resolve()
    git_root = next(
        (parent for parent in bash_path.parents if parent.name.lower() == "git"),
        None,
    )
    if git_root is not None:
        utility_dirs = [git_root / "usr" / "bin", git_root / "bin"]
        env["PATH"] = os.pathsep.join(
            [*(str(path) for path in utility_dirs if path.is_dir()), env.get("PATH", "")]
        )
    return env
