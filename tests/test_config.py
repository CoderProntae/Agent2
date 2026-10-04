from __future__ import annotations

from pathlib import Path

import pytest

from agent2.core.config import AppConfig, DEFAULT_MODEL, DEFAULT_OLLAMA_URL


def test_defaults_and_atomic_config_round_trip(tmp_path: Path) -> None:
    defaults = AppConfig()
    assert defaults.ollama_url == DEFAULT_OLLAMA_URL == "http://localhost:11435"
    assert defaults.model == DEFAULT_MODEL == "qwen3.5-9b-abliterated"
    path = tmp_path / "settings.json"
    configured = AppConfig(ollama_url="http://127.0.0.1:11435", model="test-model", workspace=str(tmp_path))
    configured.save(path)
    loaded = AppConfig.load(path)
    assert loaded == configured
    assert not list(tmp_path.glob(".settings-*.tmp"))


@pytest.mark.parametrize("url", [
    "ftp://localhost:11435",
    "http://user:secret@localhost:11435",
    "http://localhost:bad",
    "http://localhost:0",
    "http://localhost:11435/api",
    "http://localhost:11435?token=secret",
])
def test_invalid_ollama_base_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        AppConfig(ollama_url=url).validate()
