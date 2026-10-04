"""Durable, bounded chat/session history stored in the shared local SQLite DB."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import database_path, make_private


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ConversationSummary:
    id: str
    title: str
    created_at: str
    updated_at: str


class SessionStore:
    MAX_MESSAGES = 120
    MAX_MESSAGE_CHARS = 60_000

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        make_private(self.path.parent, is_dir=True)
        self._initialize()
        make_private(self.path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def _database(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._database() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, messages_json TEXT NOT NULL)"
            )

    def new_session(self, title: str = "Yeni sohbet") -> str:
        session_id = uuid.uuid4().hex
        now = _utc_now()
        with self._database() as db:
            db.execute(
                "INSERT INTO conversations(id,title,created_at,updated_at,messages_json) VALUES(?,?,?,?,?)",
                (session_id, title[:80], now, now, "[]"),
            )
        return session_id

    def list_sessions(self, limit: int = 100) -> list[ConversationSummary]:
        with self._database() as db:
            rows = db.execute(
                "SELECT id,title,created_at,updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [ConversationSummary(row["id"], row["title"], row["created_at"], row["updated_at"]) for row in rows]

    def load(self, session_id: str) -> list[dict[str, Any]]:
        with self._database() as db:
            row = db.execute("SELECT messages_json FROM conversations WHERE id=?", (session_id,)).fetchone()
        if not row:
            return []
        try:
            messages = json.loads(row["messages_json"])
        except json.JSONDecodeError:
            return []
        if not isinstance(messages, list):
            return []
        return [message for message in messages if isinstance(message, dict)]

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant", "tool"}:
            raise ValueError("Sohbet mesajı biçimi geçersiz.")
        # Keep unexpected or very large tool outputs from bloating local history.
        normalized = dict(message)
        content = normalized.get("content", "")
        if isinstance(content, str) and len(content) > self.MAX_MESSAGE_CHARS:
            normalized["content"] = content[:self.MAX_MESSAGE_CHARS] + "\n[Mesaj geçmişi için kırpıldı.]"
        now = _utc_now()
        with self._database() as db:
            row = db.execute("SELECT title,messages_json FROM conversations WHERE id=?", (session_id,)).fetchone()
            if not row:
                db.execute(
                    "INSERT INTO conversations(id,title,created_at,updated_at,messages_json) VALUES(?,?,?,?,?)",
                    (session_id, "Yeni sohbet", now, now, "[]"),
                )
                title = "Yeni sohbet"
                messages: list[dict[str, Any]] = []
            else:
                title = row["title"]
                try:
                    messages = json.loads(row["messages_json"])
                except json.JSONDecodeError:
                    messages = []
            messages.append(normalized)
            if title == "Yeni sohbet" and normalized.get("role") == "user":
                title = " ".join(str(normalized.get("content", "")).split())[:72] or title
            if len(messages) > self.MAX_MESSAGES:
                messages = messages[-self.MAX_MESSAGES:]
                # Do not leave an orphaned tool reply at the beginning of history.
                while messages and messages[0].get("role") == "tool":
                    messages.pop(0)
            encoded = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
            db.execute(
                "UPDATE conversations SET title=?,updated_at=?,messages_json=? WHERE id=?",
                (title, now, encoded, session_id),
            )

    def delete_session(self, session_id: str) -> None:
        with self._database() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (session_id,))
