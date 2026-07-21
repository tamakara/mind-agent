import os
import stat
from pathlib import Path

import pytest

from workhub.storage import (
    UnsafePathError,
    atomic_write_bytes,
    atomic_write_text,
    resolve_relative_path,
)


def test_atomic_write_replaces_content_and_restricts_permissions(tmp_path: Path) -> None:
    destination = tmp_path / "document.md"
    atomic_write_text(destination, "first")
    atomic_write_bytes(destination, b"second")

    assert destination.read_bytes() == b"second"
    assert not list(tmp_path.glob(".document.md.*.tmp"))
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_atomic_write_keeps_original_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "document.md"
    destination.write_text("original", encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> None:
        del source, target
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(destination, "replacement")

    assert destination.read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob(".document.md.*.tmp"))


def test_resolve_relative_path_accepts_only_paths_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "policies"
    nested.mkdir(parents=True)
    document = nested / "leave.md"
    document.write_text("policy", encoding="utf-8")

    assert resolve_relative_path(root, "policies/leave.md", must_exist=True) == document
    assert resolve_relative_path(root, "policies/new.md") == nested / "new.md"

    for unsafe in ("", "../outside.md", str(tmp_path / "outside.md"), "C:\\outside.md"):
        with pytest.raises(UnsafePathError):
            resolve_relative_path(root, unsafe)


def test_resolve_relative_path_rejects_symbolic_link_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symbolic links is not available")

    with pytest.raises(UnsafePathError, match="Symbolic links"):
        resolve_relative_path(root, "linked/file.md")
