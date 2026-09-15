from desaymem.extraction.parser import (
    extract_json,
    parse_extraction_response,
    parse_messages,
    remove_code_blocks,
)


def test_parse_messages_matches_mem0_format():
    text = parse_messages(
        [
            {"role": "user", "content": "我开车的时候喜欢把空调调到22度"},
            {"role": "assistant", "content": "好的，已记下"},
        ]
    )
    assert "user: 我开车的时候喜欢把空调调到22度" in text
    assert "assistant: 好的，已记下" in text


def test_remove_code_blocks_and_think_tags():
    raw = "```json\n{\"memory\": []}\n```"
    assert remove_code_blocks(raw) == '{"memory": []}'
    assert "<think>" not in remove_code_blocks("<think>secret</think>{\"memory\":[]}")


def test_extract_json_from_chatty_text():
    raw = "Here you go\n{\"memory\": [{\"text\": \"x\"}]}\nthanks"
    assert '"memory"' in extract_json(raw)


def test_invalid_json_returns_empty_list():
    assert parse_extraction_response("not json") == []
    assert parse_extraction_response("") == []
    assert parse_extraction_response("```json\n{bad}\n```") == []


def test_parse_memory_array_and_string_facts():
    payload = '{"memory": [{"text": "User likes 22C"}, "plain fact"]}'
    items = parse_extraction_response(payload)
    assert items[0]["text"] == "User likes 22C"
    assert items[1]["text"] == "plain fact"
