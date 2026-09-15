"""Entity extraction without a hard spaCy dependency.

Mem0 OSS (`mem0.utils.entity_extraction`) uses spaCy NER plus quoted text,
technical identifiers, and topic phrases. DesayMem keeps the same return
shape ``(entity_type, entity_text)`` and the same types (PROPER, QUOTED,
TOPIC, IDENTIFIER), with a regex/heuristic extractor so cockpit Chinese
and English both work in the default install.

If spaCy is installed, NER labels are merged on top of the heuristics.
"""

from __future__ import annotations

import re
from typing import Iterable

# Entity types emitted by Mem0's extractor.
PROPER = "PROPER"
QUOTED = "QUOTED"
TOPIC = "TOPIC"
IDENTIFIER = "IDENTIFIER"

_GENERIC_SINGLE = {
    "user",
    "assistant",
    "agent",
    "customer",
    "client",
    "person",
    "people",
    "memory",
    "message",
    "conversation",
    "chat",
    "session",
    "system",
    "top",
}

_GENERIC_CJK = {
    "用户",
    "助手",
    "系统",
    "空调",
    "温度",
    "喜欢",
    "习惯",
    "开车",
    "时候",
    "导航",
    "音乐",
    "座椅",
    "车辆",
    "乘员",
    "会话",
}

_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+\b")
_PROPER_RE = re.compile(r"\b(?:[A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+){0,4}|[A-Z]{2,6})\b")
_CJK_AFTER_HINT_RE = re.compile(
    r"(?:名叫|叫做|去|到|在|开往|导航到)\s*([\u4e00-\u9fff]{2,12})"
)


def normalize_entity_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def _clean_text(text: str) -> str:
    text = re.sub(r"^\*+\s*|\s*\*+$", "", (text or "").strip())
    text = re.sub(r"\s*:+$", "", text)
    return " ".join(text.split())


def _add(out: list[tuple[str, str]], entity_type: str, text: str) -> None:
    cleaned = _clean_text(text)
    if not cleaned or len(cleaned) <= 1:
        return
    if cleaned.lower() in _GENERIC_SINGLE or cleaned in _GENERIC_CJK:
        return
    out.append((entity_type, cleaned))


def _quoted(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    patterns = (
        r'"([^"]{2,80})"',
        r"'([^']{2,80})'",
        r"「([^」]{2,80})」",
        r"『([^』]{2,80})』",
        r"《([^》]{2,80})》",
        r"“([^”]{2,80})”",
        r"‘([^’]{2,80})’",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            _add(found, QUOTED, match.group(1))
    return found


def _identifiers(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in _IDENTIFIER_RE.finditer(text):
        _add(found, IDENTIFIER, match.group(0))
    return found


def _proper_english(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in _PROPER_RE.finditer(text):
        token = match.group(0)
        if token.lower() in _GENERIC_SINGLE:
            continue
        if token.isupper() and len(token) < 2:
            continue
        _add(found, PROPER, token)
    return found


def _cjk_named(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in _CJK_AFTER_HINT_RE.finditer(text):
        _add(found, PROPER, match.group(1))
    return found


def _spacy_entities(text: str) -> list[tuple[str, str]]:
    try:
        import spacy
    except Exception:
        return []
    nlp = None
    for name in ("zh_core_web_sm", "en_core_web_sm"):
        try:
            nlp = spacy.load(name)
            break
        except Exception:
            continue
    if nlp is None:
        return []
    accepted = {
        "PERSON",
        "ORG",
        "GPE",
        "LOC",
        "FAC",
        "PRODUCT",
        "WORK_OF_ART",
        "EVENT",
        "NORP",
        "LAW",
        "LANGUAGE",
    }
    found: list[tuple[str, str]] = []
    doc = nlp(text)
    for ent in doc.ents:
        if ent.label_ in accepted:
            _add(found, PROPER, ent.text)
    return found


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Return deduplicated ``(entity_type, entity_text)`` tuples."""
    if not text or not str(text).strip():
        return []
    source = str(text)
    candidates: list[tuple[str, str]] = []
    candidates.extend(_quoted(source))
    candidates.extend(_identifiers(source))
    candidates.extend(_proper_english(source))
    candidates.extend(_cjk_named(source))
    candidates.extend(_spacy_entities(source))
    return _dedupe(candidates)


def extract_entities_batch(texts: Iterable[str], batch_size: int = 32) -> list[list[tuple[str, str]]]:
    del batch_size
    return [extract_entities(text) for text in texts]


def _dedupe(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for entity_type, text in candidates:
        key = normalize_entity_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((entity_type, text))
    return out
