"""SQLite-backed local quotas, usage accounting, and administrator PIN checks.

These controls are intended for cooperative local administration. A user with
ownership of the machine and application data can ultimately change local files;
this module is not a remote DRM or a tamper-proof enterprise billing system.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .paths import database_path, make_private


class UsageLimitError(RuntimeError):
    """Raised when the local policy denies an agent action."""


class PolicyLockedError(UsageLimitError):
    """Raised when an administrator has locked agent execution."""


@dataclass(slots=True)
class UsageLimits:
    max_daily_requests: int = 500
    max_daily_tokens: int = 500_000
    max_daily_executions: int = 200
    max_session_tokens: int = 100_000
    max_session_minutes: int = 240
    max_command_seconds: int = 180

    def validate(self) -> None:
        bounds = {
            "max_daily_requests": (1, 100_000),
            "max_daily_tokens": (1, 100_000_000),
            "max_daily_executions": (1, 100_000),
            "max_session_tokens": (1, 10_000_000),
            "max_session_minutes": (1, 10_080),
            "max_command_seconds": (1, 86_400),
        }
        for field, (minimum, maximum) in bounds.items():
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{field} {minimum} ile {maximum} arasında tam sayı olmalı.")

    @classmethod
    def from_json(cls, value: str | dict[str, Any]) -> "UsageLimits":
        payload = json.loads(value) if isinstance(value, str) else value
        allowed = cls.__dataclass_fields__.keys()
        cleaned = {key: int(payload[key]) for key in allowed if key in payload}
        limits = cls(**cleaned)
        limits.validate()
        return limits


@dataclass(slots=True)
class UsageSnapshot:
    day: str
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    executions: int = 0
    active_seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class UsageStore:
    """Shareable usage database used by the main app and UsageLimitEditor."""

    PIN_ITERATIONS = 310_000

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        make_private(self.path.parent, is_dir=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._database() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_daily (
                    day TEXT PRIMARY KEY,
                    requests INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    executions INTEGER NOT NULL DEFAULT 0,
                    active_seconds REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS usage_sessions (
                    session_id TEXT PRIMARY KEY,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    active_seconds REAL NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    messages_json TEXT NOT NULL
                );
                """
            )
            defaults = {
                "limits": json.dumps(asdict(UsageLimits()), separators=(",", ":")),
                "policy_locked": "false",
                "developer_override": "false",
            }
            db.executemany(
                "INSERT OR IGNORE INTO app_settings(key, value) VALUES(?, ?)",
                defaults.items(),
            )
        make_private(self.path)
        # SQLite creates these sidecar files while WAL mode is active.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists():
                make_private(sidecar)

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    @staticmethod
    def _setting(db: sqlite3.Connection, key: str, default: str = "") -> str:
        row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def _set_setting(self, key: str, value: str) -> None:
        with self._lock, self._database() as db:
            db.execute(
                "INSERT INTO app_settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_limits(self) -> UsageLimits:
        with self._lock, self._database() as db:
            raw = self._setting(db, "limits", "{}")
        try:
            return UsageLimits.from_json(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            # Invalid local policy is treated conservatively and is visible in the editor.
            return UsageLimits(max_daily_requests=1, max_daily_tokens=1, max_daily_executions=1,
                               max_session_tokens=1, max_session_minutes=1, max_command_seconds=1)

    def set_limits(self, limits: UsageLimits) -> None:
        limits.validate()
        self._set_setting("limits", json.dumps(asdict(limits), separators=(",", ":")))

    def has_admin_pin(self) -> bool:
        with self._lock, self._database() as db:
            return bool(self._setting(db, "admin_pin_hash"))

    def set_admin_pin(self, pin: str) -> None:
        if len(pin) < 8 or len(pin) > 256:
            raise ValueError("Yönetici PIN/parolası en az 8 karakter olmalı.")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, self.PIN_ITERATIONS)
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO app_settings(key, value) VALUES('admin_pin_salt', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (salt.hex(),),
            )
            db.execute(
                "INSERT INTO app_settings(key, value) VALUES('admin_pin_hash', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (digest.hex(),),
            )
            db.commit()

    def verify_admin_pin(self, pin: str) -> bool:
        with self._lock, self._database() as db:
            salt_hex = self._setting(db, "admin_pin_salt")
            expected = self._setting(db, "admin_pin_hash")
        if not salt_hex or not expected:
            return False
        try:
            salt = bytes.fromhex(salt_hex)
            expected_bytes = bytes.fromhex(expected)
        except ValueError:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, self.PIN_ITERATIONS)
        return hmac.compare_digest(actual, expected_bytes)

    def get_policy(self) -> dict[str, bool]:
        with self._lock, self._database() as db:
            return {
                "locked": self._setting(db, "policy_locked", "false") == "true",
                "developer_override": self._setting(db, "developer_override", "false") == "true",
            }

    def set_policy(self, *, locked: bool, developer_override: bool) -> None:
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            for key, value in (("policy_locked", locked), ("developer_override", developer_override)):
                db.execute(
                    "INSERT INTO app_settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, "true" if value else "false"),
                )
            db.commit()

    def reset_usage(self) -> None:
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM usage_daily")
            db.execute("DELETE FROM usage_sessions")
            db.commit()

    def snapshot(self, session_id: str | None = None) -> tuple[UsageSnapshot, int, float]:
        day = self._today()
        with self._lock, self._database() as db:
            row = db.execute("SELECT * FROM usage_daily WHERE day = ?", (day,)).fetchone()
            daily = UsageSnapshot(day=day)
            if row:
                daily = UsageSnapshot(day, int(row["requests"]), int(row["prompt_tokens"]),
                                      int(row["completion_tokens"]), int(row["executions"]),
                                      float(row["active_seconds"]))
            session = db.execute(
                "SELECT prompt_tokens, completion_tokens, active_seconds FROM usage_sessions WHERE session_id = ?",
                (session_id or "",),
            ).fetchone()
        return daily, (int(session["prompt_tokens"] + session["completion_tokens"]) if session else 0), (
            float(session["active_seconds"]) if session else 0.0
        )

    def _assert_unlocked(self, db: sqlite3.Connection) -> bool:
        if self._setting(db, "policy_locked", "false") == "true":
            raise PolicyLockedError("Yönetici ajan kullanımını kilitledi.")
        return self._setting(db, "developer_override", "false") == "true"

    def authorize_aux_request(self) -> None:
        """Count a non-chat Ollama request such as /api/tags and enforce its daily cap."""
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            override = self._assert_unlocked(db)
            limits = UsageLimits.from_json(self._setting(db, "limits", "{}"))
            day = self._today()
            db.execute("INSERT OR IGNORE INTO usage_daily(day) VALUES(?)", (day,))
            daily = db.execute("SELECT requests FROM usage_daily WHERE day=?", (day,)).fetchone()
            if not override and int(daily["requests"]) >= limits.max_daily_requests:
                db.rollback()
                raise UsageLimitError("Günlük Ollama isteği kotası doldu.")
            db.execute("UPDATE usage_daily SET requests=requests+1 WHERE day=?", (day,))
            db.commit()

    def authorize_request(self, session_id: str, estimated_input_tokens: int = 0) -> None:
        """Atomically check quotas and count an outgoing Ollama request."""
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            override = self._assert_unlocked(db)
            limits = UsageLimits.from_json(self._setting(db, "limits", "{}"))
            day = self._today()
            db.execute("INSERT OR IGNORE INTO usage_daily(day) VALUES(?)", (day,))
            db.execute(
                "INSERT OR IGNORE INTO usage_sessions(session_id, started_at) VALUES(?, ?)",
                (session_id, datetime.now(timezone.utc).isoformat()),
            )
            daily = db.execute("SELECT * FROM usage_daily WHERE day = ?", (day,)).fetchone()
            session = db.execute("SELECT * FROM usage_sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not override:
                if int(daily["requests"]) >= limits.max_daily_requests:
                    db.rollback()
                    raise UsageLimitError("Günlük istek kotası doldu.")
                total_daily = int(daily["prompt_tokens"]) + int(daily["completion_tokens"])
                if total_daily + max(0, estimated_input_tokens) > limits.max_daily_tokens:
                    db.rollback()
                    raise UsageLimitError("Günlük token kotası bu istek için yetersiz.")
                session_total = int(session["prompt_tokens"]) + int(session["completion_tokens"])
                if session_total + max(0, estimated_input_tokens) > limits.max_session_tokens:
                    db.rollback()
                    raise UsageLimitError("Oturum token kotası doldu.")
                if float(session["active_seconds"]) >= limits.max_session_minutes * 60:
                    db.rollback()
                    raise UsageLimitError("Oturum çalışma süresi kotası doldu.")
            db.execute("UPDATE usage_daily SET requests = requests + 1 WHERE day = ?", (day,))
            db.commit()

    def record_tokens(self, session_id: str, prompt_tokens: int, completion_tokens: int) -> None:
        prompt_tokens = max(0, int(prompt_tokens))
        completion_tokens = max(0, int(completion_tokens))
        day = self._today()
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO usage_daily(day) VALUES(?)", (day,))
            db.execute(
                "INSERT OR IGNORE INTO usage_sessions(session_id, started_at) VALUES(?, ?)",
                (session_id, datetime.now(timezone.utc).isoformat()),
            )
            db.execute(
                "UPDATE usage_daily SET prompt_tokens=prompt_tokens+?, completion_tokens=completion_tokens+? WHERE day=?",
                (prompt_tokens, completion_tokens, day),
            )
            db.execute(
                "UPDATE usage_sessions SET prompt_tokens=prompt_tokens+?, completion_tokens=completion_tokens+? WHERE session_id=?",
                (prompt_tokens, completion_tokens, session_id),
            )
            db.commit()

    def authorize_agent_action(self, session_id: str) -> None:
        """Recheck live policy immediately before any model-initiated tool action."""
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            override = self._assert_unlocked(db)
            if not override:
                limits = UsageLimits.from_json(self._setting(db, "limits", "{}"))
                day = self._today()
                daily = db.execute("SELECT * FROM usage_daily WHERE day=?", (day,)).fetchone()
                session = db.execute("SELECT * FROM usage_sessions WHERE session_id=?", (session_id,)).fetchone()
                if daily:
                    total_daily = int(daily["prompt_tokens"]) + int(daily["completion_tokens"])
                    if int(daily["requests"]) >= limits.max_daily_requests:
                        db.rollback()
                        raise UsageLimitError("Günlük istek kotasına ulaşıldı; ajan eylemi engellendi.")
                    if total_daily >= limits.max_daily_tokens:
                        db.rollback()
                        raise UsageLimitError("Günlük token kotasına ulaşıldı; ajan eylemi engellendi.")
                if session:
                    session_total = int(session["prompt_tokens"]) + int(session["completion_tokens"])
                    if session_total >= limits.max_session_tokens:
                        db.rollback()
                        raise UsageLimitError("Oturum token kotasına ulaşıldı; ajan eylemi engellendi.")
                    if float(session["active_seconds"]) >= limits.max_session_minutes * 60:
                        db.rollback()
                        raise UsageLimitError("Oturum çalışma süresi kotasına ulaşıldı; ajan eylemi engellendi.")
            db.commit()

    def authorize_execution(self, session_id: str) -> int:
        """Check execution quotas, increment the counter, and return the command timeout."""
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            override = self._assert_unlocked(db)
            limits = UsageLimits.from_json(self._setting(db, "limits", "{}"))
            day = self._today()
            db.execute("INSERT OR IGNORE INTO usage_daily(day) VALUES(?)", (day,))
            db.execute(
                "INSERT OR IGNORE INTO usage_sessions(session_id, started_at) VALUES(?, ?)",
                (session_id, datetime.now(timezone.utc).isoformat()),
            )
            daily = db.execute("SELECT * FROM usage_daily WHERE day=?", (day,)).fetchone()
            session = db.execute("SELECT * FROM usage_sessions WHERE session_id=?", (session_id,)).fetchone()
            if not override:
                if int(daily["executions"]) >= limits.max_daily_executions:
                    db.rollback()
                    raise UsageLimitError("Günlük yürütme kotası doldu.")
                if float(session["active_seconds"]) >= limits.max_session_minutes * 60:
                    db.rollback()
                    raise UsageLimitError("Oturum çalışma süresi kotası doldu.")
            db.execute("UPDATE usage_daily SET executions=executions+1 WHERE day=?", (day,))
            db.commit()
        return limits.max_command_seconds

    def record_active_time(self, session_id: str, seconds: float) -> None:
        seconds = max(0.0, min(float(seconds), 86_400.0))
        if seconds == 0:
            return
        day = self._today()
        with self._lock, self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO usage_daily(day) VALUES(?)", (day,))
            db.execute(
                "INSERT OR IGNORE INTO usage_sessions(session_id, started_at) VALUES(?, ?)",
                (session_id, datetime.now(timezone.utc).isoformat()),
            )
            db.execute("UPDATE usage_daily SET active_seconds=active_seconds+? WHERE day=?", (seconds, day))
            db.execute("UPDATE usage_sessions SET active_seconds=active_seconds+? WHERE session_id=?", (seconds, session_id))
            db.commit()

    def remaining_summary(self, session_id: str | None = None) -> dict[str, Any]:
        daily, session_tokens, session_seconds = self.snapshot(session_id)
        limits = self.get_limits()
        policy = self.get_policy()
        return {
            "daily": daily,
            "session_tokens": session_tokens,
            "session_seconds": session_seconds,
            "limits": limits,
            "policy": policy,
            "daily_requests_remaining": max(0, limits.max_daily_requests - daily.requests),
            "daily_tokens_remaining": max(0, limits.max_daily_tokens - daily.total_tokens),
            "daily_executions_remaining": max(0, limits.max_daily_executions - daily.executions),
        }
