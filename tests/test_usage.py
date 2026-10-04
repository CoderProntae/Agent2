from __future__ import annotations

from pathlib import Path

import pytest

from agent2.core.usage import PolicyLockedError, UsageLimitError, UsageLimits, UsageStore


def test_usage_limits_and_accounting(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.sqlite3")
    store.set_limits(UsageLimits(max_daily_requests=1, max_daily_tokens=200, max_daily_executions=1,
                                 max_session_tokens=150, max_session_minutes=10, max_command_seconds=8))
    store.authorize_request("s1", 50)
    store.record_tokens("s1", 50, 25)
    daily, session_tokens, _ = store.snapshot("s1")
    assert daily.requests == 1
    assert daily.total_tokens == 75
    assert session_tokens == 75
    with pytest.raises(UsageLimitError):
        store.authorize_request("s1", 1)
    assert store.authorize_execution("s1") == 8
    with pytest.raises(UsageLimitError):
        store.authorize_execution("s1")


def test_session_token_quota_and_developer_override(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.sqlite3")
    store.set_limits(UsageLimits(max_daily_requests=10, max_daily_tokens=1000, max_daily_executions=10,
                                 max_session_tokens=5, max_session_minutes=10, max_command_seconds=2))
    with pytest.raises(UsageLimitError):
        store.authorize_request("s1", 6)
    store.set_policy(locked=False, developer_override=True)
    store.authorize_request("s1", 500)
    store.authorize_execution("s1")
    store.set_policy(locked=True, developer_override=True)
    with pytest.raises(PolicyLockedError):
        store.authorize_request("s1", 0)


def test_admin_pin_and_reset(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.sqlite3")
    assert not store.has_admin_pin()
    with pytest.raises(ValueError):
        store.set_admin_pin("short")
    store.set_admin_pin("correct horse battery")
    assert store.has_admin_pin()
    assert store.verify_admin_pin("correct horse battery")
    assert not store.verify_admin_pin("wrong password")
    store.authorize_request("s1", 0)
    store.reset_usage()
    daily, tokens, seconds = store.snapshot("s1")
    assert daily.requests == daily.total_tokens == daily.executions == 0
    assert tokens == 0
    assert seconds == 0
