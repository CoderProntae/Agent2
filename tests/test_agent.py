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
from agent2.core.usage import UsageStore
from agent2.core.workspace import WorkspaceService


class FakeOllama:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    def stream_chat(self, **kwargs):
        self.calls.append(kwargs["messages"])
        if kwargs.get("before_request"):
            kwargs["before_request"]()
        yield OllamaEvent(content="Tamamlandı.")
        yield OllamaEvent(done=True, prompt_tokens=11, completion_tokens=3)


def test_agent_stores_user_once_and_accounts_tokens(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    usage = UsageStore(database)
    sessions = SessionStore(database)
    session_id = sessions.new_session()
    project_root = tmp_path / "project"
    project_root.mkdir()
    workspace = WorkspaceService(project_root)
    ollama = FakeOllama()
    agent = AgentService(
        ollama=ollama, workspace=workspace, terminal=CommandRunner(workspace),
        git=GitService(workspace), usage=usage, sessions=sessions,
    )
    deltas: list[str] = []
    result = agent.run_turn(
        session_id=session_id,
        user_message="Merhaba",
        model="local-test",
        approve=lambda _name, _args: False,
        on_delta=deltas.append,
        on_activity=lambda _message: None,
        on_terminal_output=lambda _name, _text: None,
        on_change=lambda _change: None,
        cancel_event=threading.Event(),
    )
    assert result == "Tamamlandı."
    messages = sessions.load(session_id)
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Merhaba"
    assert deltas == ["Tamamlandı."]
    daily, session_tokens, _ = usage.snapshot(session_id)
    assert daily.requests == 1
    assert daily.prompt_tokens == 11
    assert daily.completion_tokens == 3
    assert session_tokens == 14
