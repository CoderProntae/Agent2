from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("httpx")

from agent2.core.agent import AgentService
from agent2.core.git_service import GitService
from agent2.core.ollama import OllamaEvent
from agent2.core.sessions import SessionStore
from agent2.core.terminal import CommandRunner
from agent2.core.tools import ToolContext, ToolRouter
from agent2.core.usage import UsageStore
from agent2.core.workspace import WorkspaceService


class ToolThenAnswerOllama:
    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, **kwargs):
        kwargs["before_request"]()
        self.calls += 1
        if self.calls == 1:
            yield OllamaEvent(done=True, prompt_tokens=10, completion_tokens=2, tool_calls=[{
                "id": "call-1",
                "type": "function",
                "function": {"name": "write_file", "arguments": {"path": "generated.txt", "content": "created by agent\n"}},
            }])
        else:
            yield OllamaEvent(content="Dosya oluşturuldu ve doğrulandı.")
            yield OllamaEvent(done=True, prompt_tokens=20, completion_tokens=6)


def test_rejected_filesystem_command_needs_no_approval_or_quota(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    workspace = WorkspaceService(project)
    usage = UsageStore(tmp_path / "usage.sqlite3")
    session_id = "test-session"
    approvals: list[str] = []
    router = ToolRouter(ToolContext(
        workspace=workspace,
        terminal=CommandRunner(workspace),
        git=GitService(workspace),
        usage=usage,
        session_id=session_id,
        approve=lambda name, _args: approvals.append(name) or True,
        activity=lambda _message: None,
        terminal_output=lambda _stream, _text: None,
        change_callback=lambda _change: None,
        cancel_event=threading.Event(),
    ))

    result = router.invoke("run_command", {"command": "mkdir test_projesi"})

    assert result["ok"] is False
    assert "write_file" in result["error"]
    assert approvals == []
    daily, _, _ = usage.snapshot(session_id)
    assert daily.executions == 0
    assert not (project / "test_projesi").exists()


class EmptyAfterToolOllama:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def stream_chat(self, **kwargs):
        kwargs["before_request"]()
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            yield OllamaEvent(done=True, prompt_tokens=10, completion_tokens=2, tool_calls=[{
                "id": "call-1",
                "type": "function",
                "function": {"name": "write_file", "arguments": {"path": "project/hello.txt", "content": "hello\n"}},
            }])
        elif len(self.calls) == 2:
            # Reproduce Qwen returning a successful but content-free final event.
            yield OllamaEvent(done=True, prompt_tokens=20, completion_tokens=4)
        else:
            assert kwargs.get("tools") is None
            yield OllamaEvent(content="project/hello.txt oluşturuldu.")
            yield OllamaEvent(done=True, prompt_tokens=30, completion_tokens=5)


def test_agent_recovers_with_tools_disabled_after_empty_final_response(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    usage = UsageStore(db)
    sessions = SessionStore(db)
    session_id = sessions.new_session()
    project = tmp_path / "project"
    project.mkdir()
    workspace = WorkspaceService(project)
    model = EmptyAfterToolOllama()
    agent = AgentService(
        ollama=model, workspace=workspace, terminal=CommandRunner(workspace), git=GitService(workspace),
        usage=usage, sessions=sessions,
    )
    result = agent.run_turn(
        session_id=session_id, user_message="Dosya oluştur", model="qwen3.5-9b-abliterated",
        approve=lambda _name, _args: True, on_delta=lambda _text: None,
        on_activity=lambda _message: None, on_terminal_output=lambda _stream, _text: None,
        on_change=lambda _change: None, cancel_event=threading.Event(),
    )

    assert result == "project/hello.txt oluşturuldu."
    assert len(model.calls) == 3
    assert model.calls[-1]["tools"] is None
    assert "\n\n" in model.calls[-1]["messages"][0]["content"]
    assert (project / "project" / "hello.txt").read_text(encoding="utf-8") == "hello\n"
    messages = sessions.load(session_id)
    assert [message["role"] for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[-1]["content"] == result
    daily, _, _ = usage.snapshot(session_id)
    assert daily.requests == 3


def test_agent_tool_cycle_requires_approval_and_reports_diff(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    usage = UsageStore(db)
    sessions = SessionStore(db)
    session_id = sessions.new_session()
    project = tmp_path / "project"
    project.mkdir()
    workspace = WorkspaceService(project)
    model = ToolThenAnswerOllama()
    changes = []
    approvals: list[str] = []

    def approve(name: str, _args: dict[str, Any]) -> bool:
        approvals.append(name)
        return True

    agent = AgentService(
        ollama=model, workspace=workspace, terminal=CommandRunner(workspace), git=GitService(workspace),
        usage=usage, sessions=sessions,
    )
    result = agent.run_turn(
        session_id=session_id, user_message="Dosya oluştur", model="local-test", approve=approve,
        on_delta=lambda _text: None, on_activity=lambda _message: None,
        on_terminal_output=lambda _stream, _text: None, on_change=changes.append,
        cancel_event=threading.Event(),
    )
    assert result == "Dosya oluşturuldu ve doğrulandı."
    assert approvals == ["write_file"]
    assert (project / "generated.txt").read_text(encoding="utf-8") == "created by agent\n"
    assert len(changes) == 1 and changes[0].path == "generated.txt"
    assert model.calls == 2
    assert [message["role"] for message in sessions.load(session_id)] == ["user", "assistant", "tool", "assistant"]


def test_agent_does_not_write_when_user_denies_approval(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    usage = UsageStore(db)
    sessions = SessionStore(db)
    session_id = sessions.new_session()
    project = tmp_path / "project"
    project.mkdir()
    workspace = WorkspaceService(project)
    model = ToolThenAnswerOllama()
    agent = AgentService(
        ollama=model, workspace=workspace, terminal=CommandRunner(workspace), git=GitService(workspace),
        usage=usage, sessions=sessions,
    )
    agent.run_turn(
        session_id=session_id, user_message="Dosya oluştur", model="local-test",
        approve=lambda _name, _args: False, on_delta=lambda _text: None,
        on_activity=lambda _message: None, on_terminal_output=lambda _stream, _text: None,
        on_change=lambda _change: None, cancel_event=threading.Event(),
    )
    assert not (project / "generated.txt").exists()
