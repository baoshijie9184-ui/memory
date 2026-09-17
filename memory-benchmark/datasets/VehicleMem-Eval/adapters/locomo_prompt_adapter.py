"""LoCoMo adapter variant for the vendored memory-benchmarks pipeline."""

import re
from datetime import datetime, timezone

from adapters.locomo_adapter import LocomoAdapter


def _parse_locomo_date(raw: str):
    formats = ("%I:%M %p on %d %B, %Y", "%I:%M %p on %d %b, %Y")
    for date_format in formats:
        try:
            return datetime.strptime((raw or "").strip(), date_format)
        except (TypeError, ValueError):
            continue
    return None


def _sorted_sessions(conversation: dict) -> list[tuple[str, str, list[dict]]]:
    sessions = []
    for key, turns in conversation.items():
        if not re.fullmatch(r"session_\d+", key):
            continue
        sessions.append((key, conversation.get(f"{key}_date_time", ""), turns))

    def sort_key(item):
        parsed = _parse_locomo_date(item[1])
        number = int(item[0].split("_")[-1])
        return (0, parsed) if parsed else (1, datetime(2000, 1, number))

    return sorted(sessions, key=sort_key)


def _reference_date(conversation: dict) -> str:
    sessions = _sorted_sessions(conversation)
    return sessions[-1][1] if sessions and sessions[-1][1] else "2023"


class LocomoPromptAdapter(LocomoAdapter):
    """Add prompt-only metadata without changing the original adapter."""

    def get_queries(self, item: dict) -> list[dict]:
        queries = super().get_queries(item)
        reference_date = _reference_date(item.get("conversation", {}))
        for query in queries:
            query["reference_date"] = reference_date
        return queries

    def build_ingest_chunks(self, item: dict) -> list[dict]:
        """Match memory-benchmarks: one LoCoMo turn per Mem0 add call."""
        conversation = item.get("conversation", {})
        speaker_a = conversation.get("speaker_a", "A")
        chunks = []

        for _, date_text, turns in _sorted_sessions(conversation):
            parsed = _parse_locomo_date(date_text)
            timestamp = (
                int(parsed.replace(tzinfo=timezone.utc).timestamp())
                if parsed else None
            )
            for turn in turns if isinstance(turns, list) else []:
                speaker = turn.get("speaker", "")
                text = turn.get("text", "")
                image_query = turn.get("query", "")
                caption = turn.get("blip_caption", "")
                if image_query and caption:
                    photo = f"[Sharing image - query: {image_query}. The image shows: {caption}]"
                elif image_query:
                    photo = f"[Sharing image - query for: {image_query}]"
                elif caption:
                    photo = f"[Sharing image that shows: {caption}]"
                else:
                    photo = ""
                if photo:
                    text = f"{text} {photo}" if text else photo
                if not text:
                    continue
                role = "user" if speaker == speaker_a else "assistant"
                chunks.append({
                    "messages": [{"role": role, "content": f"{speaker}: {text}"}],
                    "timestamp": timestamp,
                })
        return chunks
