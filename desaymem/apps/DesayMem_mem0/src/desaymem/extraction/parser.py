"""JSON / message parsers migrated from mem0.memory.utils.

Copyright 2023 Taranjeet Singh / Mem0
Licensed under the Apache License, Version 2.0
Migrated from mem0/memory/utils.py (OSS v2.0.18, commit 4fa48390).

Modifications:
- Dropped vision, telemetry, Cypher sanitization, and unused fact-retrieval helpers
- Namespaced under desaymem.extraction
"""

from __future__ import annotations

import json
import re
from typing import Any

from desaymem.core.logging import get_logger

logger = get_logger(__name__)


def parse_messages(messages: list[dict[str, Any]]) -> str:
    """Flatten chat messages into `role: content` text, matching Mem0 OSS."""
    response = ""
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if content is None:
            continue
        if role == "system":
            response += f"system: {content}\n"
        elif role == "user":
            response += f"user: {content}\n"
        elif role == "assistant":
            response += f"assistant: {content}\n"
    return response


def remove_code_blocks(content: str | list | None) -> str:
    """Strip markdown fences and <think> tags from an LLM response."""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", ""))
        content = "".join(parts)
    if not isinstance(content, str):
        return ""
    pattern = r"^```[a-zA-Z0-9]*\n([\s\S]*?)\n```$"
    match = re.match(pattern, content.strip())
    match_res = match.group(1).strip() if match else content.strip()
    return re.sub(r"<think>.*?</think>", "", match_res, flags=re.DOTALL).strip()


def extract_json(text: str) -> str:
    """Extract a JSON object or array from a possibly chatty LLM response."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        obj_start = text.find("{")
        arr_start = text.find("[")
        starts = [idx for idx in (obj_start, arr_start) if idx != -1]
        if not starts:
            return text
        start_idx = min(starts)
        closer = "}" if text[start_idx] == "{" else "]"
        end_idx = text.rfind(closer)
        if end_idx != -1 and end_idx > start_idx:
            json_str = text[start_idx : end_idx + 1]
        else:
            json_str = text
    return json_str


def parse_json_payload(response: str | None) -> Any:
    """Parse an LLM JSON object or array. Invalid input yields None."""
    if not response:
        return None
    cleaned = remove_code_blocks(response)
    if not cleaned:
        return None
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        try:
            return json.loads(extract_json(cleaned), strict=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("LLM returned invalid JSON payload")
            return None
    except Exception:
        logger.warning("LLM JSON payload parse failed")
        return None


def parse_extraction_response(response: str | None) -> list[dict[str, Any]]:
    """Parse ADD-only extraction JSON. Invalid JSON yields an empty list.

    Upstream behavior: Mem0 logs the parse error and continues with [].
    """
    if not response:
        return []
    cleaned = remove_code_blocks(response)
    if not cleaned:
        return []
    payload: Any = None
    try:
        payload = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        try:
            payload = json.loads(extract_json(cleaned), strict=False)
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON; treating extraction as empty")
            return []
        except Exception:
            logger.warning("LLM extraction parse failed; treating extraction as empty")
            return []
    except Exception:
        logger.warning("LLM extraction parse failed; treating extraction as empty")
        return []

    if not isinstance(payload, dict):
        logger.warning("LLM JSON was not an object; treating extraction as empty")
        return []

    raw_memories = payload.get("memory", [])
    if raw_memories is None:
        return []
    if not isinstance(raw_memories, list):
        logger.warning("LLM JSON 'memory' field was not a list")
        return []
    return normalize_extracted_memories(raw_memories)


def normalize_extracted_memories(raw_memories: list[Any]) -> list[dict[str, Any]]:
    """Normalize LLM items to `{text, attributed_to, linked_memory_ids}`."""
    normalized: list[dict[str, Any]] = []
    for item in raw_memories:
        text = None
        attributed_to = None
        linked: list[str] = []
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("text") or item.get("fact") or item.get("memory")
            attributed_to = item.get("attributed_to")
            raw_links = item.get("linked_memory_ids") or []
            if isinstance(raw_links, list):
                linked = [str(x) for x in raw_links if x]
        else:
            continue
        if not text or not str(text).strip():
            continue
        normalized.append(
            {
                "text": str(text).strip(),
                "attributed_to": attributed_to,
                "linked_memory_ids": linked,
            }
        )
    return normalized
