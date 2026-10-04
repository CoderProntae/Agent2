"""Resilient local Ollama HTTP client with NDJSON/SSE streaming support."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """User-facing local model service error."""


@dataclass(slots=True)
class OllamaEvent:
    content: str = ""
    done: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str = ""


class OllamaClient:
    """HTTP client for Ollama's /api/tags and /api/chat endpoints."""

    def __init__(
        self,
        base_url: str = "http://localhost:11435",
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 180.0,
        retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = self._validate_base_url(base_url)
        self.retries = max(0, min(int(retries), 5))
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(connect=connect_timeout, read=read_timeout, write=30.0, pool=10.0),
            headers={"Connection": "keep-alive", "Accept": "application/x-ndjson, text/event-stream, application/json"},
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0),
        )

    @staticmethod
    def _validate_base_url(base_url: str) -> str:
        parsed = urlsplit(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Ollama adresi http(s)://ana-makine:bağlantı-noktası biçiminde olmalı.")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("Ollama temel adresinde yol/sorgu/fragment bulunamaz.")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Ollama bağlantı noktası geçersiz.") from exc
        if port == 0:
            raise ValueError("Ollama bağlantı noktası 1-65535 arasında olmalı.")
        return base_url.strip().rstrip("/")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def list_models(self, *, before_request: Callable[[], None] | None = None) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/tags"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                if before_request:
                    before_request()
                response = self._client.get(url)
                if response.status_code in {408, 425, 429} or response.status_code >= 500:
                    if attempt < self.retries:
                        time.sleep(min(0.4 * (2**attempt), 2.0))
                        continue
                response.raise_for_status()
                payload = response.json()
                models = payload.get("models", []) if isinstance(payload, dict) else []
                return [model for model in models if isinstance(model, dict)]
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:1000]
                raise OllamaError(f"Ollama model listesi alınamadı (HTTP {exc.response.status_code}): {detail}") from exc
            except (httpx.RequestError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(0.4 * (2**attempt), 2.0))
                    continue
                break
        logger.warning("Ollama model listesi alınamadı", exc_info=last_error)
        raise OllamaError(f"Ollama'ya bağlanılamadı ({self.base_url}). Sunucunun çalıştığını ve portun doğru olduğunu kontrol edin.") from last_error

    def stream_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> Iterator[OllamaEvent]:
        if not model.strip():
            raise OllamaError("Model adı boş olamaz.")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": max(0.0, min(float(temperature), 2.0))},
        }
        if tools:
            payload["tools"] = tools
        url = f"{self.base_url}/api/chat"
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            response_started = False
            content_seen = False
            pieces: dict[int, dict[str, Any]] = {}
            prompt_tokens: int | None = None
            completion_tokens: int | None = None
            selected_model = model
            try:
                if before_request:
                    before_request()
                with self._client.stream("POST", url, json=payload) as response:
                    if response.status_code in {408, 425, 429} or response.status_code >= 500:
                        detail = response.read().decode("utf-8", "replace")[:1000]
                        if attempt < self.retries:
                            time.sleep(min(0.4 * (2**attempt), 2.0))
                            continue
                        raise OllamaError(f"Ollama HTTP {response.status_code}: {detail}")
                    if response.status_code >= 400:
                        detail = response.read().decode("utf-8", "replace")[:1000]
                        raise OllamaError(f"Ollama isteği reddetti (HTTP {response.status_code}): {detail}")

                    buffer: list[str] = []
                    for line in response.iter_lines():
                        if cancel_event is not None and cancel_event.is_set():
                            raise OllamaError("Üretim kullanıcı tarafından durduruldu.")
                        if not line:
                            continue
                        line = line.strip()
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if not line or line == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            # Some proxies split a JSON object across transport chunks.
                            buffer.append(line)
                            combined = "".join(buffer)
                            try:
                                chunk = json.loads(combined)
                                buffer.clear()
                            except json.JSONDecodeError:
                                continue
                        if not isinstance(chunk, dict):
                            continue
                        if chunk.get("error"):
                            raise OllamaError(f"Ollama: {str(chunk['error'])[:1000]}")
                        response_started = True
                        selected_model = str(chunk.get("model") or selected_model)
                        message = chunk.get("message") or {}
                        if not isinstance(message, dict):
                            message = {}
                        content = message.get("content", "")
                        if isinstance(content, str) and content:
                            content_seen = True
                            yield OllamaEvent(content=content, model=selected_model)
                        self._merge_tool_calls(pieces, message.get("tool_calls"))
                        if isinstance(chunk.get("prompt_eval_count"), int):
                            prompt_tokens = int(chunk["prompt_eval_count"])
                        if isinstance(chunk.get("eval_count"), int):
                            completion_tokens = int(chunk["eval_count"])
                        if chunk.get("done") is True:
                            break

                    calls = self._finish_tool_calls(pieces)
                    yield OllamaEvent(
                        done=True,
                        tool_calls=calls,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        model=selected_model,
                    )
                    return
            except OllamaError:
                raise
            except (httpx.RequestError, httpx.TimeoutException, OSError) as exc:
                last_error = exc
                # Retry only before emitting a partial answer. Retrying after visible
                # content would duplicate text and potentially repeat tool requests.
                if response_started or content_seen or attempt >= self.retries:
                    logger.warning("Ollama akışı yarıda kesildi", exc_info=exc)
                    raise OllamaError(f"Ollama akışı kesildi: {exc}") from exc
                time.sleep(min(0.4 * (2**attempt), 2.0))
        logger.warning("Ollama bağlantısı başarısız", exc_info=last_error)
        raise OllamaError(f"Ollama'ya bağlanılamadı ({self.base_url}). Sunucuyu ve 11435 portunu kontrol edin.") from last_error

    @staticmethod
    def _merge_tool_calls(target: dict[int, dict[str, Any]], incoming: Any) -> None:
        if not isinstance(incoming, list):
            return
        for position, item in enumerate(incoming):
            if not isinstance(item, dict):
                continue
            index = item.get("index", position)
            try:
                index = int(index)
            except (TypeError, ValueError):
                index = position
            current = target.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": {}}})
            if item.get("id"):
                current["id"] = str(item["id"])
            function = item.get("function") or {}
            if not isinstance(function, dict):
                continue
            destination = current["function"]
            if function.get("name"):
                destination["name"] += str(function["name"])
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                if not isinstance(destination.get("arguments"), dict):
                    destination["arguments"] = {}
                destination["arguments"].update(arguments)
            elif isinstance(arguments, str) and arguments:
                if not isinstance(destination.get("arguments"), str):
                    destination["arguments"] = ""
                destination["arguments"] += arguments

    @staticmethod
    def _finish_tool_calls(pieces: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for index in sorted(pieces):
            call = pieces[index]
            function = call.get("function", {})
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments else {}
                except json.JSONDecodeError:
                    arguments = {"_raw_arguments": arguments}
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append({
                "id": call.get("id") or f"call-{index}",
                "type": "function",
                "function": {"name": function.get("name", ""), "arguments": arguments},
            })
        return calls


def rough_token_count(text: str) -> int:
    """Conservative, dependency-free estimate used only when Ollama omits counts."""
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)
