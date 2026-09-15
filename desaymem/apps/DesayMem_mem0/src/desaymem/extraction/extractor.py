"""ADD-only memory extractor.

Call chain migrated from mem0.memory.main.AsyncMemory._add_to_vector_store
Phases 0-2 (OSS v2.0.18, commit 4fa48390):

  parse messages
  -> retrieve existing memories for LLM context
  -> single LLM call with ADDITIVE_EXTRACTION_PROMPT
  -> JSON parse

Not migrated: telemetry, notices, graph memory.
Last-k messages are loaded from the session message store (Mem0 SQLite
`get_last_messages`); entity linking is done after insert.
"""

from __future__ import annotations

from typing import Any, Protocol

from desaymem.core.exceptions import LLMError
from desaymem.core.logging import get_logger
from desaymem.extraction.parser import parse_extraction_response, parse_messages
from desaymem.extraction.prompts import (
    ADDITIVE_EXTRACTION_PROMPT,
    generate_additive_extraction_prompt,
)

logger = get_logger(__name__)


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[dict],
        response_format: dict | None = None,
    ) -> str: ...


class MemoryExtractor:
    def __init__(
        self,
        llm: LLMClient,
        custom_instructions: str = "",
        use_input_language: bool = True,
    ) -> None:
        self.llm = llm
        self.custom_instructions = custom_instructions
        self.use_input_language = use_input_language

    async def extract(
        self,
        messages: list[dict[str, Any]],
        existing_memories: list[dict[str, str]] | None = None,
        last_k_messages: list[dict[str, Any]] | None = None,
        custom_instructions: str | None = None,
        summary: str | None = None,
    ) -> list[dict[str, Any]]:
        parsed = parse_messages(messages)
        existing = existing_memories or []
        # Anti-hallucination: send integer ids to the LLM, keep a UUID map.
        uuid_mapping: dict[str, str] = {}
        indexed_existing: list[dict[str, str]] = []
        for idx, mem in enumerate(existing):
            fake_id = str(idx)
            real_id = str(mem.get("id", fake_id))
            uuid_mapping[fake_id] = real_id
            indexed_existing.append({"id": fake_id, "text": mem.get("text", "")})

        user_prompt = generate_additive_extraction_prompt(
            summary=summary,
            existing_memories=indexed_existing,
            new_messages=parsed,
            last_k_messages=last_k_messages or [],
            custom_instructions=custom_instructions or self.custom_instructions or None,
            use_input_language=self.use_input_language,
        )
        try:
            response = await self.llm.complete(
                messages=[
                    {"role": "system", "content": ADDITIVE_EXTRACTION_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except LLMError:
            raise
        except Exception as exc:
            logger.error("LLM extraction failed")
            raise LLMError(f"LLM extraction failed: {exc}") from exc

        extracted = parse_extraction_response(response)
        for item in extracted:
            remapped: list[str] = []
            for linked in item.get("linked_memory_ids") or []:
                remapped.append(uuid_mapping.get(str(linked), str(linked)))
            item["linked_memory_ids"] = remapped
        logger.info("Extracted %s candidate memories", len(extracted))
        return extracted
