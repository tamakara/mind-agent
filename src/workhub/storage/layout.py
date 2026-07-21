import csv
import os
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path


class DataLayoutError(RuntimeError):
    """Raised when a secure data layout cannot be created."""


@dataclass(frozen=True, slots=True)
class WorkHubDataLayout:
    root: Path
    database: Path
    knowledge_originals: Path
    knowledge_staging: Path
    knowledge_index: Path
    logs: Path


def initialize_workhub_data_layout(root: Path) -> WorkHubDataLayout:
    root = _prepare_root(root)
    layout = WorkHubDataLayout(
        root=root,
        database=root / "app.db",
        knowledge_originals=root / "knowledge" / "originals",
        knowledge_staging=root / "knowledge" / "staging",
        knowledge_index=root / "knowledge" / "index",
        logs=root / "logs",
    )

    for directory in (
        layout.root,
        layout.knowledge_originals,
        layout.knowledge_staging,
        layout.knowledge_index,
        layout.logs,
    ):
        _ensure_directory(directory)
    _ensure_database_file(layout.database)
    return layout


def _prepare_root(root: Path) -> Path:
    expanded = root.expanduser()
    if expanded.is_symlink():
        raise DataLayoutError(f"Data root must not be a symbolic link: {expanded}")
    return expanded.resolve(strict=False)


def _ensure_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise DataLayoutError(f"Managed directory is not a regular directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(path, is_directory=True)


def _ensure_database_file(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise DataLayoutError(f"Database path is not a regular file: {path}")
    path.touch(exist_ok=True)
    _restrict_permissions(path, is_directory=False)


def _restrict_permissions(path: Path, *, is_directory: bool) -> None:
    if os.name == "nt":
        _restrict_windows_acl(path, is_directory=is_directory)
        return
    path.chmod(0o700 if is_directory else 0o600)


@cache
def _current_windows_sid() -> str:
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise DataLayoutError("Unable to resolve the current Windows user SID")
    rows = list(csv.reader(result.stdout.splitlines()))
    if len(rows) != 1 or len(rows[0]) < 2 or not rows[0][1].startswith("S-"):
        raise DataLayoutError("Unexpected output while resolving the current Windows user SID")
    return rows[0][1]


def _restrict_windows_acl(path: Path, *, is_directory: bool) -> None:
    sid = _current_windows_sid()
    rights = "(OI)(CI)F" if is_directory else "F"
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:{rights}", "/q"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise DataLayoutError(f"Unable to restrict permissions for {path}")
