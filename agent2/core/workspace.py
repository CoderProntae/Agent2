"""Workspace-rooted file operations with traversal and symlink protection."""

from __future__ import annotations

import difflib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorkspaceError(RuntimeError):
    """Workspace access or file-operation error."""


@dataclass(slots=True)
class ChangeRecord:
    path: str
    operation: str
    before: str
    after: str
    timestamp: str
    previous_path: str | None = None

    def unified_diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
            )
        ) or "(Değişiklik yok)"

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation": self.operation,
            "timestamp": self.timestamp,
            "previous_path": self.previous_path,
            "diff": self.unified_diff(),
        }


class WorkspaceService:
    """All file operations are resolved inside one explicitly selected directory."""

    MAX_FILE_BYTES = 1_000_000
    IGNORED_DIRECTORIES = {
        ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", "target",
    }

    def __init__(self, root: str | Path | None = None) -> None:
        self.root: Path | None = None
        if root:
            self.set_root(root)

    def set_root(self, root: str | Path) -> Path:
        selected = Path(root).expanduser().resolve(strict=True)
        if not selected.is_dir():
            raise WorkspaceError("Çalışma alanı bir klasör olmalı.")
        self.root = selected
        return selected

    def _require_root(self) -> Path:
        if self.root is None:
            raise WorkspaceError("Önce bir çalışma klasörü açın.")
        return self.root

    def resolve(self, user_path: str | Path, *, allow_root: bool = False) -> Path:
        root = self._require_root()
        raw = os.fspath(user_path)
        if not raw or "\x00" in raw:
            raise WorkspaceError("Dosya yolu boş veya geçersiz.")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        # Normalize dot-segments without following symlinks; then enforce lexical scope.
        lexical = Path(os.path.abspath(candidate))
        try:
            relative = lexical.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("Yol çalışma alanı dışına çıkamaz.") from exc
        if not allow_root and relative == Path("."):
            raise WorkspaceError("Çalışma alanı kök dizininin kendisi kullanılamaz.")

        # Refuse every symlink in the selected path. This prevents reading through a
        # workspace link into the user's home directory or writing through a link.
        current = root
        for component in relative.parts:
            current = current / component
            try:
                if current.is_symlink():
                    raise WorkspaceError("Güvenlik nedeniyle sembolik bağlantılar kullanılamaz.")
            except OSError as exc:
                raise WorkspaceError(f"Yol denetlenemedi: {exc}") from exc
        resolved = lexical.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("Çözümlenen yol çalışma alanı dışına çıkıyor.") from exc
        return lexical

    def relative_path(self, path: str | Path) -> str:
        target = self.resolve(path, allow_root=True)
        return target.relative_to(self._require_root()).as_posix() or "."

    def list_files(self, directory: str = ".", *, limit: int = 1200) -> list[dict[str, Any]]:
        if not 1 <= limit <= 5000:
            raise WorkspaceError("Dosya listesi sınırı 1-5000 arasında olmalı.")
        base = self.resolve(directory, allow_root=True)
        if not base.is_dir():
            raise WorkspaceError(f"Klasör bulunamadı: {directory}")
        entries: list[dict[str, Any]] = []
        for current, dirs, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(
                name for name in dirs
                if name not in self.IGNORED_DIRECTORIES
                and not (current_path / name).is_symlink()
            )
            names = [(name, True) for name in dirs] + [(name, False) for name in files]
            for name, is_dir in sorted(names, key=lambda pair: (not pair[1], pair[0].casefold())):
                full = current_path / name
                if full.is_symlink():
                    continue
                rel = full.relative_to(self._require_root()).as_posix()
                try:
                    size = 0 if is_dir else full.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
                entries.append({"path": rel, "name": name, "is_dir": is_dir, "size": size})
                if len(entries) >= limit:
                    return entries
        return entries

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"Dosya bulunamadı: {path}")
        size = target.stat().st_size
        cap = max_bytes or self.MAX_FILE_BYTES
        if size > cap:
            raise WorkspaceError(f"Dosya çok büyük ({size:,} bayt; sınır {cap:,} bayt).")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("Dosya UTF-8 metin değil; ikili içerik okunmuyor.") from exc
        except OSError as exc:
            raise WorkspaceError(f"Dosya okunamadı: {exc}") from exc

    def write_file(self, path: str, content: str) -> ChangeRecord:
        if not isinstance(content, str):
            raise WorkspaceError("Dosya içeriği metin olmalı.")
        encoded = content.encode("utf-8")
        if len(encoded) > self.MAX_FILE_BYTES:
            raise WorkspaceError(f"İçerik {self.MAX_FILE_BYTES:,} bayt sınırını aşıyor.")
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after creating parent directories in case an unexpected link appeared.
        target = self.resolve(path)
        if target.exists() and not target.is_file():
            raise WorkspaceError("Dosya yolu normal bir dosyaya ait olmalı.")
        existed_before = target.exists()
        before = self.read_file(path) if existed_before else ""
        fd, temporary_name = tempfile.mkstemp(prefix=".agent2-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise WorkspaceError(f"Dosya yazılamadı: {exc}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return self._change(target, "yazıldı" if existed_before else "oluşturuldu", before, content)

    def edit_file(self, path: str, old_text: str, new_text: str) -> ChangeRecord:
        if not old_text:
            raise WorkspaceError("Metin değiştirme için eski metin boş olamaz; write_file kullanın.")
        existing = self.read_file(path)
        occurrences = existing.count(old_text)
        if occurrences != 1:
            raise WorkspaceError(f"Eski metin dosyada tam bir kez bulunmalı (bulunan: {occurrences}).")
        updated = existing.replace(old_text, new_text, 1)
        return self.write_file(path, updated)

    def delete_file(self, path: str) -> ChangeRecord:
        target = self.resolve(path)
        if not target.exists() or not target.is_file():
            raise WorkspaceError("Yalnızca var olan normal dosyalar silinebilir.")
        before = self.read_file(path)
        target.unlink()
        return self._change(target, "silindi", before, "")

    def rename_path(self, source: str, destination: str) -> ChangeRecord:
        origin = self.resolve(source)
        target = self.resolve(destination)
        if not origin.exists() or not origin.is_file():
            raise WorkspaceError("Yalnızca var olan normal dosyalar yeniden adlandırılabilir.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self.resolve(destination)
        if target.exists():
            raise WorkspaceError("Hedef zaten var; üzerine yazmak için açıkça write_file kullanın.")
        before = self.read_file(source)
        previous_path = origin.relative_to(self._require_root()).as_posix()
        origin.replace(target)
        after = self.read_file(destination)
        return self._change(target, "yeniden adlandırıldı", before, after, previous_path=previous_path)

    def _change(self, path: Path, operation: str, before: str, after: str, *, previous_path: str | None = None) -> ChangeRecord:
        relative = path.relative_to(self._require_root()).as_posix()
        return ChangeRecord(relative, operation, before, after, datetime.now(timezone.utc).isoformat(), previous_path)
