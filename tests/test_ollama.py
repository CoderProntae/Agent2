from __future__ import annotations

import json

import pytest

httpx = pytest.importorskip("httpx")

from agent2.core.ollama import OllamaClient


def test_stream_chat_parses_ndjson_content_counts_and_tool_calls() -> None:
    chunks = [
        {"model": "qwen-local", "message": {"role": "assistant", "content": "Hello "}, "done": False},
        {"model": "qwen-local", "message": {"role": "assistant", "content": "world", "tool_calls": [
            {"function": {"name": "list_files", "arguments": {"path": "."}}}
        ]}, "done": False},
        {"model": "qwen-local", "message": {"role": "assistant", "content": ""}, "done": True,
         "prompt_eval_count": 14, "eval_count": 4},
    ]

    def handler(request):
        return httpx.Response(200, content="\n".join(json.dumps(item) for item in chunks).encode(), request=request)

    session = httpx.Client(transport=httpx.MockTransport(handler))
    client = OllamaClient("http://localhost:11435", client=session, retries=0)
    events = list(client.stream_chat(model="qwen-local", messages=[{"role": "user", "content": "hi"}]))
    assert "".join(event.content for event in events) == "Hello world"
    final = events[-1]
    assert final.done
    assert final.prompt_tokens == 14
    assert final.completion_tokens == 4
    assert final.tool_calls[0]["function"]["name"] == "list_files"
    assert final.tool_calls[0]["function"]["arguments"] == {"path": "."}
    session.close()


def test_model_list_retries_transient_status() -> None:
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="warming", request=request)
        return httpx.Response(200, json={"models": [{"name": "local:latest"}]}, request=request)

    session = httpx.Client(transport=httpx.MockTransport(handler))
    client = OllamaClient("http://localhost:11435", client=session, retries=1)
    assert client.list_models()[0]["name"] == "local:latest"
    assert attempts == 2
    session.close()
