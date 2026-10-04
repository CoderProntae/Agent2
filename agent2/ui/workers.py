"""Qt worker threads for network, agent, terminal, and GitHub operations."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any

from PySide6.QtCore import QThread, Signal

from agent2.core.agent import AgentCancelled, AgentService
from agent2.core.git_service import GitService, GitResult
from agent2.core.ollama import OllamaClient
from agent2.core.terminal import CommandRunner
from agent2.core.usage import UsageStore
from agent2.core.workspace import ChangeRecord

logger = logging.getLogger(__name__)


class AgentWorker(QThread):
    delta = Signal(str)
    activity = Signal(str)
    terminal_output = Signal(str, str)
    change = Signal(object)
    approval_requested = Signal(str, str, str)
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        service: AgentService,
        *,
        session_id: str,
        user_message: str,
        model: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.session_id = session_id
        self.user_message = user_message
        self.model = model
        self.cancel_event = threading.Event()
        self._pending: dict[str, tuple[threading.Event, list[bool]]] = {}
        self._pending_lock = threading.Lock()

    @staticmethod
    def _approval_details(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
        titles = {
            "write_file": "Dosya oluşturma / üzerine yazma",
            "edit_file": "Dosya düzenleme",
            "delete_file": "Dosya silme",
            "rename_path": "Dosyayı yeniden adlandırma",
            "run_command": "Terminal komutu çalıştırma",
            "git_init": "Git deposu başlatma",
            "git_add": "Değişiklikleri Git'e hazırlama",
            "git_commit": "Git commit oluşturma",
            "git_create_branch": "Git dalı oluşturma",
            "git_checkout": "Git dalı değiştirme",
        }
        safe_arguments = dict(arguments)
        for key in ("content", "old_text", "new_text"):
            value = safe_arguments.get(key)
            if isinstance(value, str) and len(value) > 1800:
                safe_arguments[key] = value[:1800] + f"\n… [{len(value) - 1800} karakter gizlendi]"
        try:
            details = json.dumps(safe_arguments, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            details = repr(safe_arguments)
        return titles.get(name, name), details[:8000]

    def _ask_user(self, name: str, arguments: dict[str, Any]) -> bool:
        if self.cancel_event.is_set():
            return False
        request_id = uuid.uuid4().hex
        event = threading.Event()
        result = [False]
        with self._pending_lock:
            self._pending[request_id] = (event, result)
        title, details = self._approval_details(name, arguments)
        self.approval_requested.emit(request_id, title, details)
        deadline = time.monotonic() + 600
        while not event.wait(0.2):
            if self.cancel_event.is_set() or time.monotonic() >= deadline:
                break
        with self._pending_lock:
            self._pending.pop(request_id, None)
        return bool(result[0]) and not self.cancel_event.is_set()

    def resolve_approval(self, request_id: str, accepted: bool) -> None:
        with self._pending_lock:
            pending = self._pending.get(request_id)
            if pending:
                event, result = pending
                result[0] = bool(accepted)
                event.set()

    def reject_pending(self) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            for event, result in pending:
                result[0] = False
                event.set()

    def stop(self) -> None:
        self.cancel_event.set()
        self.reject_pending()

    def run(self) -> None:
        try:
            result = self.service.run_turn(
                session_id=self.session_id,
                user_message=self.user_message,
                model=self.model,
                approve=self._ask_user,
                on_delta=self.delta.emit,
                on_activity=self.activity.emit,
                on_terminal_output=self.terminal_output.emit,
                on_change=self.change.emit,
                cancel_event=self.cancel_event,
            )
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.completed.emit(result)
        except Exception as exc:
            if self.cancel_event.is_set() or isinstance(exc, AgentCancelled):
                self.cancelled.emit()
            else:
                logger.exception("Ajan turu başarısız")
                self.failed.emit(str(exc))
        finally:
            try:
                self.service.ollama.close()
            except Exception:
                logger.debug("Ollama istemcisi kapatılamadı", exc_info=True)


class ManualCommandWorker(QThread):
    output = Signal(str, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, runner: CommandRunner, usage: UsageStore, session_id: str, command: str, parent=None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.usage = usage
        self.session_id = session_id
        self.command = command
        self.cancel_event = threading.Event()

    def run(self) -> None:
        started = time.monotonic()
        try:
            limit = self.usage.authorize_execution(self.session_id)
            result = self.runner.run(self.command, timeout=limit, on_output=self.output.emit, cancel_event=self.cancel_event)
            self.completed.emit(result)
        except Exception as exc:
            logger.exception("Manuel terminal komutu başarısız")
            self.failed.emit(str(exc))
        finally:
            try:
                self.usage.record_active_time(self.session_id, time.monotonic() - started)
            except Exception:
                logger.exception("Terminal etkinlik süresi kaydedilemedi")

    def stop(self) -> None:
        self.cancel_event.set()


class GitSyncWorker(QThread):
    completed = Signal(object)

    def __init__(self, git: GitService, usage: UsageStore, session_id: str, direction: str,
                 repository: str, token: str, branch: str = "", parent=None) -> None:
        super().__init__(parent)
        self.git = git
        self.usage = usage
        self.session_id = session_id
        self.direction = direction
        self.repository = repository
        self.token = token
        self.branch = branch

    def run(self) -> None:
        started = time.monotonic()
        try:
            timeout = self.usage.authorize_execution(self.session_id)
            if self.direction == "pull":
                result: GitResult = self.git.github_pull(self.repository, self.token, self.branch, timeout=timeout)
            else:
                result = self.git.github_push(self.repository, self.token, self.branch, timeout=timeout)
            self.completed.emit(result)
        except Exception as exc:
            logger.exception("GitHub eşitleme hatası")
            self.completed.emit(GitResult(False, 1, stderr=str(exc)))
        finally:
            try:
                self.usage.record_active_time(self.session_id, time.monotonic() - started)
            except Exception:
                logger.exception("GitHub etkin süresi kaydedilemedi")
            self.token = ""  # Drop our Python reference after the operation.


class ModelListWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, base_url: str, usage: UsageStore | None = None, parent=None) -> None:
        super().__init__(parent)
        self.base_url = base_url
        self.usage = usage

    def run(self) -> None:
        client = OllamaClient(self.base_url, connect_timeout=3, read_timeout=10, retries=1)
        try:
            callback = self.usage.authorize_aux_request if self.usage else None
            self.completed.emit(client.list_models(before_request=callback))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            client.close()
