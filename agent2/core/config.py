"""Application preferences and OS-keyring-backed secret storage."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .paths import config_path, make_private

DEFAULT_OLLAMA_URL = "http://localhost:11435"
DEFAULT_MODEL = "qwen3.5-9b-abliterated"


@dataclass(slots=True)
class AppConfig:
    ollama_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_MODEL
    workspace: str = ""
    github_repository: str = ""
    github_branch: str = ""

    def validate(self) -> None:
        parsed = urlsplit(self.ollama_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Ollama adresi http(s)://ana-makine:bağlantı-noktası biçiminde olmalı.")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("Ollama temel adresinde yol, sorgu veya fragment kullanılamaz.")
        try:
            port = parsed.port  # Validate the numeric port range now, not during a later request.
        except ValueError as exc:
            raise ValueError("Ollama bağlantı noktası geçersiz.") from exc
        if port == 0:
            raise ValueError("Ollama bağlantı noktası 1-65535 arasında olmalı.")
        if not self.model.strip():
            raise ValueError("Model adı boş olamaz.")
        if len(self.model) > 200:
            raise ValueError("Model adı çok uzun.")
        if len(self.github_repository) > 500 or len(self.github_branch) > 250:
            raise ValueError("GitHub depo veya dal bilgisi çok uzun.")

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        target = path or config_path()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            config = cls(
                ollama_url=str(payload.get("ollama_url", DEFAULT_OLLAMA_URL)),
                model=str(payload.get("model", DEFAULT_MODEL)),
                workspace=str(payload.get("workspace", "")),
                github_repository=str(payload.get("github_repository", "")),
                github_branch=str(payload.get("github_branch", "")),
            )
            config.validate()
            return config
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A corrupt preferences file should not make the desktop app unlaunchable.
            return cls()

    def save(self, path: Path | None = None) -> None:
        self.validate()
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(asdict(self), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            make_private(temporary)
            os.replace(temporary, target)
            make_private(target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class SecretStore:
    """Store GitHub credentials only in the operating-system keyring.

    There is intentionally no plaintext-file fallback. Systems without a usable
    keyring can still use the local agent and Git features, but not token-based sync.
    """

    SERVICE = "Agent2"
    GITHUB_USER = "github-token"

    @staticmethod
    def _keyring():
        try:
            import keyring  # type: ignore[import-not-found]

            return keyring
        except ImportError as exc:
            raise RuntimeError("Güvenli token saklama için keyring paketi kurulu değil.") from exc

    @classmethod
    def save_github_token(cls, token: str) -> None:
        token = token.strip()
        if not token:
            raise ValueError("GitHub token boş olamaz.")
        if len(token) > 500:
            raise ValueError("GitHub token biçimi geçersiz.")
        try:
            cls._keyring().set_password(cls.SERVICE, cls.GITHUB_USER, token)
        except Exception as exc:  # OS keyring backends have platform-specific errors.
            raise RuntimeError(f"İşletim sistemi anahtarlığına yazılamadı: {exc}") from exc

    @classmethod
    def get_github_token(cls) -> str | None:
        try:
            return cls._keyring().get_password(cls.SERVICE, cls.GITHUB_USER)
        except Exception as exc:
            raise RuntimeError(f"İşletim sistemi anahtarlığına erişilemedi: {exc}") from exc

    @classmethod
    def delete_github_token(cls) -> None:
        try:
            cls._keyring().delete_password(cls.SERVICE, cls.GITHUB_USER)
        except Exception as exc:
            # Deleting a token that is already absent is a successful end state.
            if exc.__class__.__name__ not in {"PasswordDeleteError", "KeyringError"}:
                raise RuntimeError(f"Token silinemedi: {exc}") from exc
