"""Shared helpers for the projection tests (tests/ is on sys.path: `from conftest import …`)."""

import subprocess
from pathlib import Path


def commit_all(
    root: Path,  # A directory to hold as a git work tree (initialized on first use)
    msg: str = "x",
) -> str:  # The new HEAD commit
    """Commit the tree as it stands. The archive ingest reads HEAD, never the working tree, and
    times every element from git history (design amendment 19edbe97), so a test corpus is a
    committed work tree like the real website clone."""
    root = Path(root)
    if not (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", msg, "--allow-empty"], check=True)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
