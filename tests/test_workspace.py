from __future__ import annotations

from pathlib import Path

import pytest

from agent2.core.workspace import WorkspaceError, WorkspaceService


def test_file_lifecycle_and_diff(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    change = workspace.write_file("src/app.py", "print('hello')\n")
    assert change.operation == "oluşturuldu"
    assert workspace.read_file("src/app.py") == "print('hello')\n"

    edited = workspace.edit_file("src/app.py", "hello", "agent")
    assert "-print('hello')" in edited.unified_diff()
    assert "+print('agent')" in edited.unified_diff()

    renamed = workspace.rename_path("src/app.py", "src/main.py")
    assert renamed.path == "src/main.py"
    assert workspace.read_file("src/main.py") == "print('agent')\n"
    deleted = workspace.delete_file("src/main.py")
    assert deleted.operation == "silindi"
    assert not (tmp_path / "src/main.py").exists()


def test_rejects_parent_traversal_and_absolute_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = WorkspaceService(root)
    with pytest.raises(WorkspaceError):
        workspace.resolve("../outside.txt")
    with pytest.raises(WorkspaceError):
        workspace.resolve(tmp_path / "outside.txt")


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    try:
        (root / "linked").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this platform/user.")
    workspace = WorkspaceService(root)
    with pytest.raises(WorkspaceError):
        workspace.read_file("linked")


def test_edit_requires_unique_match_and_rename_does_not_overwrite(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    workspace.write_file("a.txt", "same same")
    with pytest.raises(WorkspaceError):
        workspace.edit_file("a.txt", "same", "other")
    workspace.write_file("b.txt", "target")
    with pytest.raises(WorkspaceError):
        workspace.rename_path("a.txt", "b.txt")
    assert workspace.read_file("b.txt") == "target"
