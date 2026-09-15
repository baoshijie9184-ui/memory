"""JSON helpers for L2/L3 LLM outputs."""

from __future__ import annotations

from typing import Any

from desaymem.extraction.parser import parse_json_payload


def as_object(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {}


def parse_object(response: str | None) -> dict[str, Any]:
    return as_object(parse_json_payload(response))


def remap_ids(raw_ids: Any, mapping: dict[str, str]) -> list[str]:
    if not isinstance(raw_ids, list):
        return []
    out: list[str] = []
    for item in raw_ids:
        key = str(item)
        out.append(mapping.get(key, key))
    return out
