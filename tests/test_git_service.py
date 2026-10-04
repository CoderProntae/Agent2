from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent2.core.git_service import GitService
from agent2.core.workspace import WorkspaceService


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")
def test_local_git_init_status_stage_and_diff(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    git = GitService(workspace)
    initialized = git.init()
    assert initialized.succeeded, initialized.stderr
    workspace.write_file("hello.txt", "hello\n")
    status = git.status()
    assert status.succeeded
    assert "hello.txt" in status.stdout
    staged = git.add(["hello.txt"])
    assert staged.succeeded, staged.stderr
    diff = git.diff(staged=True)
    assert diff.succeeded
    assert "+hello" in diff.stdout


def test_github_url_validation_never_accepts_credentials_or_other_hosts(tmp_path: Path) -> None:
    git = GitService(WorkspaceService(tmp_path))
    assert git._github_url("https://github.com/owner/repo") == "https://github.com/owner/repo.git"
    assert git._github_url("https://github.com/owner/repo.git") == "https://github.com/owner/repo.git"
    for url in (
        "http://github.com/owner/repo",
        "https://user:pass@github.com/owner/repo",
        "https://example.com/owner/repo",
        "https://github.com/owner/repo/extra",
        "https://github.com/owner/%0arepo",
    ):
        with pytest.raises(ValueError):
            git._github_url(url)
