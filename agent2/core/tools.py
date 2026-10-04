"""Structured agent tools for rooted file, terminal, and Git workflows."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .git_service import GitService
from .terminal import CommandRunner
from .usage import UsageLimitError, UsageStore
from .workspace import ChangeRecord, WorkspaceError, WorkspaceService

logger = logging.getLogger(__name__)
ApprovalCallback = Callable[[str, dict[str, Any]], bool]
ActivityCallback = Callable[[str], None]
TerminalCallback = Callable[[str, str], None]
ChangeCallback = Callable[[ChangeRecord], None]


def _tool(name: str, description: str, properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = list(required)
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _tool("list_files", "Çalışma alanındaki dosya ve klasörleri listele. Ağır ve gizli klasörler atlanır.", {
        "path": {"type": "string", "description": "Kökten göreli klasör yolu; varsayılan '.'"},
        "limit": {"type": "integer", "description": "En fazla 1200 kayıt"},
    }),
    _tool("read_file", "Çalışma alanındaki UTF-8 metin dosyasını oku (en fazla 1 MB).", {
        "path": {"type": "string"},
    }, ("path",)),
    _tool("write_file", "Çalışma alanında dosya oluştur veya tamamen üzerine yaz; gerekli üst klasörleri kendisi oluşturur. Her çağrı kullanıcı onayı gerektirir.", {
        "path": {"type": "string"}, "content": {"type": "string"},
    }, ("path", "content")),
    _tool("edit_file", "Dosyada yalnızca tam bir kez bulunan eski metni yeni metinle değiştir. Her çağrı onay gerektirir.", {
        "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
    }, ("path", "old_text", "new_text")),
    _tool("delete_file", "Çalışma alanındaki normal bir dosyayı sil. Her çağrı kullanıcı onayı gerektirir.", {
        "path": {"type": "string"},
    }, ("path",)),
    _tool("rename_path", "Bir dosyayı yeniden adlandır. Var olan hedefin üzerine yazmaz; onay gerekir.", {
        "source": {"type": "string"}, "destination": {"type": "string"},
    }, ("source", "destination")),
    _tool("run_command", "Yalnızca izin listesindeki geliştirme komutlarını kabuksuz çalıştır (mkdir gibi dosya sistemi komutları izinli değildir; dosyalar için write_file/list_files kullan). Her komut için onay gerekir; program kullanıcı hesabının yetkilerine sahiptir.", {
        "command": {"type": "string"}, "timeout": {"type": "integer", "description": "İstenen saniye; yönetici üst sınırını aşamaz"},
    }, ("command",)),
    _tool("git_status", "Git çalışma ağacının durumunu göster (salt okunur).", {}),
    _tool("git_diff", "Çalışma ağacındaki veya hazırlanmış Git farklarını göster (salt okunur).", {
        "staged": {"type": "boolean"}, "path": {"type": "string"},
    }),
    _tool("git_log", "Son Git commit'lerini göster (salt okunur).", {"limit": {"type": "integer"}}),
    _tool("git_branches", "Yerel Git dallarını listele (salt okunur).", {}),
    _tool("git_init", "Çalışma alanında yeni Git deposu başlat. Onay gerekir.", {
        "initial_branch": {"type": "string"},
    }),
    _tool("git_add", "Belirtilen çalışma alanı yollarını Git index'ine ekle. Onay gerekir.", {
        "paths": {"type": "array", "items": {"type": "string"}},
    }, ("paths",)),
    _tool("git_commit", "Hazırlanmış değişiklikleri commit et. Onay gerekir.", {
        "message": {"type": "string"},
    }, ("message",)),
    _tool("git_create_branch", "Yeni Git dalı oluşturup ona geç. Onay gerekir.", {"name": {"type": "string"}}, ("name",)),
    _tool("git_checkout", "Var olan Git dalına geç. Onay gerekir.", {"name": {"type": "string"}}, ("name",)),
]


@dataclass(slots=True)
class ToolContext:
    workspace: WorkspaceService
    terminal: CommandRunner
    git: GitService
    usage: UsageStore
    session_id: str
    approve: ApprovalCallback
    activity: ActivityCallback
    terminal_output: TerminalCallback
    change_callback: ChangeCallback
    cancel_event: threading.Event


class ToolRouter:
    GIT_MUTATIONS = {"git_init", "git_add", "git_commit", "git_create_branch", "git_checkout"}
    APPROVAL_REQUIRED = {
        "write_file", "edit_file", "delete_file", "rename_path", "run_command",
        "git_init", "git_add", "git_commit", "git_create_branch", "git_checkout",
    }

    def __init__(self, context: ToolContext) -> None:
        self.context = context

    def invoke(self, name: str, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "Araç argümanları JSON nesnesi olmalı."}
        if self.context.cancel_event.is_set():
            return {"ok": False, "cancelled": True, "error": "Kullanıcı ajanı durdurdu."}
        if name not in {tool["function"]["name"] for tool in TOOL_DEFINITIONS}:
            return {"ok": False, "error": f"Bilinmeyen araç: {name}"}
        if name == "run_command":
            try:
                CommandRunner.parse(str(arguments.get("command", "")))
            except ValueError as exc:
                self.context.activity(f"Terminal komutu reddedildi: {exc}")
                return {"ok": False, "error": str(exc)}
        if name in self.APPROVAL_REQUIRED and not self.context.approve(name, arguments):
            return {"ok": False, "cancelled": True, "error": "Kullanıcı bu eylemi onaylamadı."}
        try:
            self.context.usage.authorize_agent_action(self.context.session_id)
            if name in self.GIT_MUTATIONS:
                self.context.usage.authorize_execution(self.context.session_id)
            return self._dispatch(name, arguments)
        except (WorkspaceError, UsageLimitError, ValueError, TypeError) as exc:
            self.context.activity(f"Araç başarısız: {name} — {exc}")
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("Agent aracı çalışırken beklenmeyen hata: %s", name)
            self.context.activity(f"Araç hatası: {name}")
            return {"ok": False, "error": f"Beklenmeyen araç hatası: {type(exc).__name__}: {exc}"}

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self.context.workspace
        if name == "list_files":
            entries = workspace.list_files(str(args.get("path", ".")), limit=int(args.get("limit", 1200)))
            return {"ok": True, "entries": entries, "count": len(entries)}
        if name == "read_file":
            path = str(args["path"])
            return {"ok": True, "path": workspace.relative_path(path), "content": workspace.read_file(path)}
        if name in {"write_file", "edit_file", "delete_file", "rename_path"}:
            if name == "write_file":
                change = workspace.write_file(str(args["path"]), str(args["content"]))
            elif name == "edit_file":
                change = workspace.edit_file(str(args["path"]), str(args["old_text"]), str(args["new_text"]))
            elif name == "delete_file":
                change = workspace.delete_file(str(args["path"]))
            else:
                change = workspace.rename_path(str(args["source"]), str(args["destination"]))
            self.context.change_callback(change)
            self.context.activity(f"Dosya değişti: {change.operation} — {change.path}")
            return {"ok": True, **change.as_dict()}
        if name == "run_command":
            command = str(args["command"])
            # Reject disallowed commands before consuming a quota unit.
            CommandRunner.parse(command)
            limit = self.context.usage.authorize_execution(self.context.session_id)
            requested = int(args.get("timeout", limit))
            timeout = max(1, min(requested, limit))
            self.context.activity(f"Terminal komutu çalışıyor: {command}")
            result = self.context.terminal.run(
                command,
                timeout=timeout,
                on_output=self.context.terminal_output,
                cancel_event=self.context.cancel_event,
            )
            self.context.activity(f"Terminal tamamlandı: çıkış kodu {result.exit_code}")
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_status":
            result = self.context.git.status()
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_diff":
            result = self.context.git.diff(staged=bool(args.get("staged", False)), path=args.get("path"))
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_log":
            result = self.context.git.log(int(args.get("limit", 12)))
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_branches":
            result = self.context.git.branches()
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_init":
            result = self.context.git.init(str(args.get("initial_branch", "main")))
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_add":
            paths = args.get("paths")
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise ValueError("paths metinlerden oluşan bir dizi olmalı.")
            result = self.context.git.add(paths)
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_commit":
            result = self.context.git.commit(str(args["message"]))
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_create_branch":
            result = self.context.git.create_branch(str(args["name"]))
            return {"ok": result.succeeded, **result.as_dict()}
        if name == "git_checkout":
            result = self.context.git.checkout(str(args["name"]))
            return {"ok": result.succeeded, **result.as_dict()}
        raise ValueError(f"Uygulanmamış araç: {name}")

    @staticmethod
    def encode_result(result: dict[str, Any], *, max_chars: int = 30_000) -> str:
        payload = dict(result)
        field_limit = max(500, max_chars // 5)
        for key in ("content", "stdout", "stderr", "diff"):
            value = payload.get(key)
            if isinstance(value, str) and len(value) > field_limit:
                payload[key] = value[:field_limit] + "\n[Model bağlamı için çıktı kırpıldı.]"
        entries = payload.get("entries")
        if isinstance(entries, list) and len(entries) > 50:
            payload["entries"] = entries[:50]
            payload["entries_truncated"] = True
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= max_chars:
            return encoded
        # Keep the tool protocol valid JSON even for unusually large/unexpected payloads.
        fallback = {
            "ok": bool(payload.get("ok", False)),
            "truncated": True,
            "preview": encoded[: max(100, max_chars - 500)],
        }
        encoded = json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > max_chars:
            fallback["preview"] = fallback["preview"][: max(20, max_chars - 200)]
            encoded = json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))
        return encoded
