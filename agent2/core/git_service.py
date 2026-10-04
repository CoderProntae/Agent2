"""Workspace-scoped Git operations and optional HTTPS GitHub synchronization."""

from __future__ import annotations

import base64
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .workspace import WorkspaceError, WorkspaceService


@dataclass(slots=True)
class GitResult:
    succeeded: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class GitService:
    """Small, shell-free wrapper for Git operations rooted in the chosen workspace."""

    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def _run(self, args: list[str], *, timeout: int = 30, extra_env: dict[str, str] | None = None) -> GitResult:
        root = self.workspace.root
        if root is None:
            return GitResult(False, 126, stderr="Önce bir çalışma klasörü açın.")
        if not args or any("\x00" in item for item in args):
            return GitResult(False, 126, stderr="Geçersiz Git argümanı.")
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env.update(extra_env or {})
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                check=False,
            )
            return GitResult(completed.returncode == 0, completed.returncode,
                             completed.stdout[-200_000:], completed.stderr[-200_000:])
        except FileNotFoundError:
            return GitResult(False, 127, stderr="Git kurulu değil veya PATH içinde bulunamadı.")
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return GitResult(False, 124, stdout[-200_000:], (stderr + "\nGit komutu zaman aşımına uğradı.")[-200_000:])
        except OSError as exc:
            return GitResult(False, 126, stderr=f"Git başlatılamadı: {exc}")

    def status(self) -> GitResult:
        return self._run(["status", "--short", "--branch"])

    def diff(self, *, staged: bool = False, path: str | None = None) -> GitResult:
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
        if staged:
            args.append("--cached")
        if path:
            try:
                relative = self.workspace.relative_path(path)
            except WorkspaceError as exc:
                return GitResult(False, 126, stderr=str(exc))
            args.extend(["--", relative])
        return self._run(args)

    def log(self, limit: int = 12) -> GitResult:
        limit = max(1, min(int(limit), 100))
        return self._run(["log", f"-n{limit}", "--oneline", "--decorate"])

    def init(self, initial_branch: str = "main") -> GitResult:
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", initial_branch) or initial_branch.startswith("-"):
            return GitResult(False, 126, stderr="Dal adı geçersiz.")
        return self._run(["init", "--initial-branch", initial_branch])

    def add(self, paths: list[str]) -> GitResult:
        if not paths:
            return GitResult(False, 126, stderr="Hazırlamak için en az bir yol belirtin.")
        resolved: list[str] = []
        try:
            for path in paths:
                resolved.append(self.workspace.relative_path(path))
        except WorkspaceError as exc:
            return GitResult(False, 126, stderr=str(exc))
        return self._run(["add", "--", *resolved])

    def commit(self, message: str) -> GitResult:
        if not message.strip() or len(message) > 500:
            return GitResult(False, 126, stderr="Commit mesajı 1-500 karakter arasında olmalı.")
        return self._run(["commit", "-m", message])

    def current_branch(self) -> str:
        result = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        return result.stdout.strip() if result.succeeded else ""

    def branches(self) -> GitResult:
        return self._run(["branch", "--list", "--format=%(refname:short)"])

    def create_branch(self, name: str, *, checkout: bool = True) -> GitResult:
        checked = self._run(["check-ref-format", "--branch", name])
        if not checked.succeeded:
            return GitResult(False, 126, stderr="Dal adı Git biçim kurallarına uymuyor.")
        return self._run(["switch", "-c", name] if checkout else ["branch", name])

    def checkout(self, name: str) -> GitResult:
        checked = self._run(["check-ref-format", "--branch", name])
        if not checked.succeeded:
            return GitResult(False, 126, stderr="Dal adı Git biçim kurallarına uymuyor.")
        return self._run(["switch", name])

    def _github_url(self, repository: str) -> str:
        parsed = urlsplit(repository.strip())
        if (parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Depo adresi https://github.com/sahip/depo biçiminde olmalı.")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2 or any(part in {".", ".."} for part in parts):
            raise ValueError("GitHub depo adresinde sahip ve depo adı bulunmalı.")
        repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[0]) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
            raise ValueError("GitHub sahip/depo adı geçersiz.")
        return f"https://github.com/{parts[0]}/{repo}.git"

    def _authenticated_env(self, token: str) -> dict[str, str]:
        credential = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        return {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credential}",
        }

    def _ensure_origin(self, repository: str, token: str) -> GitResult:
        normalized = self._github_url(repository)
        inside = self._run(["rev-parse", "--is-inside-work-tree"])
        if not inside.succeeded:
            return GitResult(False, inside.exit_code, inside.stdout, "Önce çalışma klasöründe git init yapın.\n" + inside.stderr)
        remote = self._run(["remote", "get-url", "origin"])
        if remote.succeeded:
            if remote.stdout.strip() != normalized:
                update = self._run(["remote", "set-url", "origin", normalized])
                if not update.succeeded:
                    return update
        else:
            add = self._run(["remote", "add", "origin", normalized])
            if not add.succeeded:
                return add
        return GitResult(True, 0, stdout=normalized)

    @staticmethod
    def _validated_branch(branch: str) -> str:
        if not branch or len(branch) > 250 or branch.startswith("-") or branch.startswith("/"):
            raise ValueError("Dal adı geçersiz.")
        if any(char.isspace() for char in branch):
            raise ValueError("Dal adında boşluk kullanılamaz.")
        return branch

    def github_pull(self, repository: str, token: str, branch: str = "", *, timeout: int = 120) -> GitResult:
        timeout = max(1, min(int(timeout), 86_400))
        try:
            branch = self._validated_branch(branch.strip() or self.current_branch())
            self._github_url(repository)
        except ValueError as exc:
            return GitResult(False, 126, stderr=str(exc))
        if not branch:
            return GitResult(False, 126, stderr="Pull için bir dal adı belirtin veya önce yerel dal oluşturun.")
        if not self._run(["check-ref-format", "--branch", branch]).succeeded:
            return GitResult(False, 126, stderr="Dal adı Git biçim kurallarına uymuyor.")
        current = self.current_branch()
        if current and current != branch:
            return GitResult(False, 1, stderr=f"Aktif dal '{current}', ayarlanan dal '{branch}'. Pull öncesinde doğru dala geçin.")
        status = self._run(["status", "--porcelain"])
        if not status.succeeded:
            return status
        if status.stdout.strip():
            return GitResult(False, 1, stderr="Pull güvenliği için önce çalışma ağacındaki değişiklikleri commit edin veya saklayın.")
        origin = self._ensure_origin(repository, token)
        if not origin.succeeded:
            return origin
        env = self._authenticated_env(token)
        fetch = self._run(["fetch", "--prune", "origin", branch], timeout=timeout, extra_env=env)
        if not fetch.succeeded:
            return fetch
        remote_branch = self._run(["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"])
        if not remote_branch.succeeded:
            return GitResult(False, 1, stderr=f"Uzak depoda '{branch}' dalı bulunamadı.")
        has_head = self._run(["rev-parse", "--verify", "HEAD"])
        if not has_head.succeeded:
            checkout = self._run(["checkout", "-B", branch, "--track", f"origin/{branch}"])
            return checkout
        return self._run(["pull", "--ff-only", "origin", branch], timeout=timeout, extra_env=env)

    def github_push(self, repository: str, token: str, branch: str = "", *, timeout: int = 120) -> GitResult:
        timeout = max(1, min(int(timeout), 86_400))
        try:
            branch = self._validated_branch(branch.strip() or self.current_branch())
            self._github_url(repository)
        except ValueError as exc:
            return GitResult(False, 126, stderr=str(exc))
        if not branch:
            return GitResult(False, 126, stderr="Push için geçerli bir yerel dal ve en az bir commit gerekli.")
        if not self._run(["check-ref-format", "--branch", branch]).succeeded:
            return GitResult(False, 126, stderr="Dal adı Git biçim kurallarına uymuyor.")
        has_head = self._run(["rev-parse", "--verify", "HEAD"])
        if not has_head.succeeded:
            return GitResult(False, 1, stderr="Push öncesinde en az bir commit oluşturun.")
        origin = self._ensure_origin(repository, token)
        if not origin.succeeded:
            return origin
        return self._run(["push", "--set-upstream", "origin", branch], timeout=timeout,
                         extra_env=self._authenticated_env(token))
