"""Explicitly approved, timeout-bounded, no-shell workspace command execution."""

from __future__ import annotations

import codecs
import os
import queue
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from .workspace import WorkspaceService


@dataclass(slots=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_dict(self) -> dict[str, object]:
        return asdict(self) | {"succeeded": self.succeeded}


class CommandRunner:
    """Run a small developer-tool allowlist without a shell.

    This is defense in depth, not an OS sandbox: an approved Python/npm/git program
    can still access the current user's files and network. Callers must obtain a
    visible user confirmation before every execution.
    """

    ALLOWED_EXECUTABLES = {
        "python", "python3", "py", "pip", "pip3", "uv", "poetry", "pytest",
        "node", "npm", "npx", "pnpm", "yarn", "git", "cargo", "rustc", "go",
        "dotnet", "java", "javac", "make", "cmake", "ctest", "ruff", "black",
        "isort", "mypy", "flake8", "coverage", "tox", "pre-commit", "docker", "podman",
    }
    SHELL_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "2>", "2>&1"}
    MAX_OUTPUT_CHARS = 200_000

    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    @staticmethod
    def _split_windows(command: str) -> list[str]:
        """Use the native Windows argument parser instead of lossy POSIX shlex rules."""
        import ctypes
        from ctypes import wintypes

        argc = ctypes.c_int(0)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        parse = shell32.CommandLineToArgvW
        parse.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        parse.restype = ctypes.POINTER(wintypes.LPWSTR)
        local_free = kernel32.LocalFree
        local_free.argtypes = (wintypes.HLOCAL,)
        local_free.restype = wintypes.HLOCAL
        pointer = parse(command, ctypes.byref(argc))
        if not pointer:
            raise OSError(ctypes.get_last_error(), "CommandLineToArgvW failed")
        try:
            return [pointer[index] for index in range(argc.value)]
        finally:
            local_free(pointer)

    @classmethod
    def parse(cls, command: str) -> list[str]:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Komut boş olamaz.")
        if len(command) > 4000:
            raise ValueError("Komut 4000 karakter sınırını aşıyor.")
        try:
            if os.name == "nt":
                args = cls._split_windows(command)
            else:
                args = shlex.split(command, posix=True)
        except (ValueError, OSError) as exc:
            raise ValueError(f"Komut ayrıştırılamadı: {exc}") from exc
        if not args:
            raise ValueError("Komut boş olamaz.")
        if any(part in cls.SHELL_TOKENS for part in args):
            raise ValueError("Kabuk birleştirme/yönlendirme desteklenmiyor; komutları ayrı ayrı çalıştırın.")
        if any(separator in args[0] for separator in ("/", "\\", ":")):
            raise ValueError("Komut yürütülebiliri PATH içindeki adıyla belirtilmeli; doğrudan dosya yolu kabul edilmez.")
        executable = Path(args[0]).name.lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]
        if executable not in cls.ALLOWED_EXECUTABLES:
            allowed = ", ".join(sorted(cls.ALLOWED_EXECUTABLES))
            raise ValueError(f"'{executable}' izin verilen araçlar listesinde değil. İzin verilenler: {allowed}")
        return args

    def run(
        self,
        command: str,
        *,
        timeout: int = 180,
        on_output: Callable[[str, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        try:
            args = self.parse(command)
        except ValueError as exc:
            return CommandResult(command, 126, "", str(exc), 0.0)
        root = self.workspace.root
        if root is None:
            return CommandResult(command, 126, "", "Önce bir çalışma klasörü açın.", 0.0)
        if not 1 <= int(timeout) <= 86_400:
            return CommandResult(command, 126, "", "Zaman aşımı 1-86400 saniye arasında olmalı.", 0.0)

        environment = os.environ.copy()
        environment.update({"PYTHONUNBUFFERED": "1", "GIT_TERMINAL_PROMPT": "0"})
        if extra_env:
            environment.update({str(k): str(v) for k, v in extra_env.items()})
        creation_flags = 0
        popen_options: dict[str, object] = {}
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            popen_options["start_new_session"] = True

        started = time.monotonic()
        try:
            process = subprocess.Popen(
                args,
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                bufsize=0,
                creationflags=creation_flags,
                **popen_options,
            )
        except (OSError, ValueError) as exc:
            return CommandResult(command, 127, "", f"Komut başlatılamadı: {exc}", time.monotonic() - started)

        events: queue.Queue[tuple[str, str | None]] = queue.Queue()

        def drain(label: str, stream) -> None:
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            try:
                while chunk := stream.read(4096):
                    decoded = decoder.decode(chunk)
                    if decoded:
                        events.put((label, decoded))
                tail = decoder.decode(b"", final=True)
                if tail:
                    events.put((label, tail))
            finally:
                events.put((label, None))
                stream.close()

        readers = [
            threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        output: dict[str, list[str]] = {"stdout": [], "stderr": []}
        output_lengths = {"stdout": 0, "stderr": 0}
        completed_streams = 0
        deadline = started + timeout
        timed_out = False
        cancelled = False

        def terminate_tree() -> None:
            if process.poll() is not None:
                return
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    process.kill()
                except OSError:
                    pass

        while completed_streams < 2 or process.poll() is None:
            if process.poll() is None and cancel_event is not None and cancel_event.is_set():
                cancelled = True
                terminate_tree()
            if process.poll() is None and time.monotonic() >= deadline:
                timed_out = True
                terminate_tree()
            try:
                label, text = events.get(timeout=0.05)
            except queue.Empty:
                continue
            if text is None:
                completed_streams += 1
                continue
            available = max(0, self.MAX_OUTPUT_CHARS - output_lengths[label])
            if available:
                retained = text[:available]
                output[label].append(retained)
                output_lengths[label] += len(retained)
                if on_output:
                    try:
                        on_output(label, retained)
                    except Exception:
                        # A UI callback should not abort or deadlock a child process.
                        pass

        for reader in readers:
            reader.join(timeout=1)
        try:
            exit_code = int(process.wait(timeout=1))
        except subprocess.TimeoutExpired:
            terminate_tree()
            exit_code = int(process.wait(timeout=1))
        if timed_out:
            exit_code = 124
        elif cancelled:
            exit_code = 130
        for label in ("stdout", "stderr"):
            if output_lengths[label] >= self.MAX_OUTPUT_CHARS:
                output[label].append("\n[Çıktı sınırı aşıldı; devamı kırpıldı.]\n")
        return CommandResult(
            command=command,
            exit_code=exit_code,
            stdout="".join(output["stdout"]),
            stderr="".join(output["stderr"]),
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
        )
