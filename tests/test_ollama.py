from __future__ import annotations

import json

import pytest

httpx = pytest.importorskip("httpx")

from agent2.core.ollama import OllamaClient, OllamaError  # noqa: E402


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


def test_qwen35_disables_reasoning_in_chat_payload() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=json.dumps({"message": {"role": "assistant", "content": "Merhaba"}, "done": True}).encode(),
            request=request,
        )

    session = httpx.Client(transport=httpx.MockTransport(handler))
    client = OllamaClient("http://localhost:11435", client=session, retries=0)
    events = list(client.stream_chat(
        model="qwen3.5-9b-abliterated", messages=[{"role": "user", "content": "Merhaba"}], tools=[]
    ))
    assert payloads[0]["think"] is False
    assert "Merhaba" in "".join(event.content for event in events)
    session.close()


def test_qwen_xml_tool_parser_error_is_retried_once() -> None:
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            body = {"error": "XML syntax error on line 4: element <function> closed by </parameter>"}
        else:
            body = {"message": {"role": "assistant", "content": "Dosya oluşturuldu."}, "done": True}
        return httpx.Response(200, content=(json.dumps(body) + "\n").encode(), request=request)

    session = httpx.Client(transport=httpx.MockTransport(handler))
    client = OllamaClient("http://localhost:11435", client=session, retries=1)
    events = list(client.stream_chat(
        model="qwen3.5-9b-abliterated", messages=[{"role": "user", "content": "Dosya oluştur"}], tools=[{"type": "function"}]
    ))
    assert attempts == 2
    assert "Dosya oluşturuldu." in "".join(event.content for event in events)
    session.close()


def test_persistent_qwen_xml_tool_parser_error_has_actionable_guidance() -> None:
    def handler(request):
        body = {"error": "XML syntax error on line 4: element <function> closed by </parameter>"}
        return httpx.Response(200, content=(json.dumps(body) + "\n").encode(), request=request)

    session = httpx.Client(transport=httpx.MockTransport(handler))
    client = OllamaClient("http://localhost:11435", client=session, retries=0)
    with pytest.raises(OllamaError, match="Ollama'yı güncelleyip yeniden deneyin"):
        list(client.stream_chat(
            model="qwen3.5-9b-abliterated", messages=[{"role": "user", "content": "Dosya oluştur"}], tools=[{"type": "function"}]
        ))
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
