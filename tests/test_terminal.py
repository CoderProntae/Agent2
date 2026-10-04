from __future__ import annotations

import sys
from pathlib import Path

from agent2.core.terminal import CommandRunner
from agent2.core.workspace import WorkspaceService


def python_executable() -> str:
    return '"python"' if sys.platform == "win32" else "python"


def test_command_runner_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    runner = CommandRunner(WorkspaceService(tmp_path))
    command = f'{python_executable()} -c "import sys; print(\'hello\'); print(\'problem\', file=sys.stderr); sys.exit(3)"'
    result = runner.run(command, timeout=5)
    assert result.exit_code == 3
    assert "hello" in result.stdout
    assert "problem" in result.stderr
    assert not result.succeeded


def test_command_runner_blocks_shell_and_unknown_executables(tmp_path: Path) -> None:
    runner = CommandRunner(WorkspaceService(tmp_path))
    assert runner.run("python -c \"print(1)\" && python -c \"print(2)\"").exit_code == 126
    assert runner.run("powershell -Command Write-Host unsafe").exit_code == 126
    command = f'{python_executable()} -c "print(1); print(2)"'
    assert runner.run(command).stdout.strip().splitlines() == ["1", "2"]


def test_command_timeout_is_reported(tmp_path: Path) -> None:
    runner = CommandRunner(WorkspaceService(tmp_path))
    command = f'{python_executable()} -c "import time; time.sleep(2)"'
    result = runner.run(command, timeout=1)
    assert result.timed_out
    assert result.exit_code == 124
