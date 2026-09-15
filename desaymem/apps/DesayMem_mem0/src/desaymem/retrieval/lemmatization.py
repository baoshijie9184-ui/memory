"""BM25 tokenization migrated from mem0.utils.lemmatization.

Mem0 uses spaCy lemmas when available and otherwise returns the raw text.
DesayMem keeps spaCy optional and always emits space-separated tokens so
PostgreSQL `simple` tsvector / in-memory keyword search can match CJK.
"""

from __future__ import annotations

import re

_ASCII_WORD = re.compile(r"[A-Za-z0-9_]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_NLP = None
_NLP_TRIED = False


def lemmatize_for_bm25(text: str) -> str:
    if not text or not str(text).strip():
        return ""
    source = str(text)
    fallback = _ascii_cjk_tokens(source)
    spacy_tokens = _spacy_lemmas(source)
    if not spacy_tokens:
        return " ".join(fallback) if fallback else source.lower()
    merged: list[str] = []
    seen: set[str] = set()
    for token in [*spacy_tokens, *fallback]:
        if token in seen:
            continue
        seen.add(token)
        merged.append(token)
    return " ".join(merged)


def _ascii_cjk_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _ASCII_WORD.finditer(text.lower()):
        tokens.append(match.group(0))
    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        tokens.extend(list(run))
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _spacy_lemmas(text: str) -> list[str] | None:
    nlp = _load_spacy()
    if nlp is None:
        return None
    tokens: list[str] = []
    for token in nlp(text.lower()):
        if token.is_punct or token.is_stop:
            continue
        lemma = token.lemma_
        if lemma.isalnum():
            tokens.append(lemma)
        if token.text.endswith("ing") and token.text != lemma and token.text.isalnum():
            tokens.append(token.text)
    return tokens


def _load_spacy():
    global _NLP, _NLP_TRIED
    if _NLP_TRIED:
        return _NLP
    _NLP_TRIED = True
    try:
        import spacy
    except Exception:
        return None
    for name in ("en_core_web_sm", "zh_core_web_sm"):
        try:
            _NLP = spacy.load(name)
            return _NLP
        except Exception:
            continue
    return None
