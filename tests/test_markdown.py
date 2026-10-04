from __future__ import annotations

from agent2.ui.markdown import fenced_code_block


def test_fenced_code_block_keeps_json_entities_literal() -> None:
    payload = '{"ok":true,"error":"\'mkdir\' izinli değil","path":"a/b.txt"}'

    rendered = fenced_code_block(payload, "json")

    assert payload in rendered
    assert "&quot;" not in rendered
    assert "&#x27;" not in rendered
    assert rendered.startswith("```json\n")


def test_fenced_code_block_uses_a_longer_fence_than_the_body() -> None:
    payload = "text\n````\nmore"

    rendered = fenced_code_block(payload, "json")

    assert rendered.startswith("`````json\n")
    assert payload in rendered
    assert rendered.endswith("`````\n\n")
