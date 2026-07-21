import os
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafePathError(ValueError):
    """Raised when a user-controlled relative path escapes its managed root."""


def resolve_relative_path(root: Path, relative: str | Path, *, must_exist: bool = False) -> Path:
    raw = str(relative)
    relative_path = Path(relative)
    if (
        not raw
        or relative_path.is_absolute()
        or PurePosixPath(raw).is_absolute()
        or PureWindowsPath(raw).is_absolute()
        or ".." in relative_path.parts
    ):
        raise UnsafePathError("Path must be a non-empty safe relative path")

    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise UnsafePathError("Managed root is not a directory")

    current = resolved_root
    for part in relative_path.parts:
        if part in ("", "."):
            continue
        current /= part
        if current.is_symlink():
            raise UnsafePathError("Symbolic links are not allowed in managed paths")

    try:
        resolved = current.resolve(strict=must_exist)
    except (FileNotFoundError, OSError) as exc:
        raise UnsafePathError("Managed path does not exist") from exc
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise UnsafePathError("Path escapes its managed root") from exc
    return resolved


def atomic_write_bytes(destination: Path, content: bytes, *, mode: int = 0o600) -> None:
    parent = destination.parent
    if parent.is_symlink() or not parent.is_dir():
        raise UnsafePathError("Destination parent must be a regular directory")
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise UnsafePathError("Destination must be a regular file")

    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, destination)
        _fsync_directory(parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(
    destination: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int = 0o600,
) -> None:
    atomic_write_bytes(destination, content.encode(encoding), mode=mode)


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
