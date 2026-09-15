from types import SimpleNamespace

import pytest

from desaymem_light.adapters.embedding.bge_m3 import BgeM3OpenAICompatible
from desaymem_light.adapters.llm.json_utils import parse_json_object
from desaymem_light.adapters.llm.qwen_openai import QwenOpenAICompatible
from desaymem_light.adapters.llm.qwen_tokenizer import QwenTokenCounter
from desaymem_light.contracts.providers import ChatRequest
from desaymem_light.domain.errors import ContractError


class FakeChatCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.parameters = None

    async def create(self, **parameters):
        self.parameters = parameters
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        )


class FakeChatClient:
    def __init__(self, content: str) -> None:
        self.completions = FakeChatCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls = []

    async def create(self, **parameters):
        self.calls.append(parameters)
        data = [
            SimpleNamespace(index=index, embedding=[float(index)] * 1024)
            for index, _ in enumerate(parameters["input"])
        ]
        return SimpleNamespace(data=list(reversed(data)))


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings()


class FakeTokenizer:
    def __init__(self) -> None:
        self.last_add_special_tokens = None

    def encode(self, text, *, add_special_tokens):
        self.last_add_special_tokens = add_special_tokens
        return text.split()


def test_json_parser_handles_fence_and_qwen_reasoning_prefix() -> None:
    assert parse_json_object('```json\n{"memory": []}\n```') == {"memory": []}
    assert parse_json_object('analysis finished\n{"memory": ["a"]}') == {"memory": ["a"]}
    with pytest.raises(ContractError):
        parse_json_object("not-json")


@pytest.mark.asyncio
async def test_qwen_adapter_requests_one_json_response_and_records_usage() -> None:
    client = FakeChatClient('{"memory": [{"text": "likes music"}]}')
    adapter = QwenOpenAICompatible(
        model="Qwen3-32B", base_url="http://qwen/v1", client=client,
    )
    result = await adapter.generate_json(
        ChatRequest(
            messages=[{"role": "user", "content": "hello"}],
            response_schema={"type": "object"},
            max_output_tokens=200,
        )
    )

    assert result.data["memory"][0]["text"] == "likes music"
    assert result.usage.input_tokens == 12
    assert client.completions.parameters["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_bge_adapter_batches_orders_and_validates_dimensions() -> None:
    client = FakeEmbeddingClient()
    adapter = BgeM3OpenAICompatible(
        model="BGE-M3", base_url="http://bge/v1", client=client,
        options={"batch_size": 2, "send_dimensions": False},
    )
    result = await adapter.embed(["a", "b", "c"], "add")

    assert result.request_count == 2
    assert len(result.vectors) == 3
    assert len(result.vectors[0]) == 1024
    assert "dimensions" not in client.embeddings.calls[0]


def test_qwen_token_counter_accepts_injected_tokenizer() -> None:
    tokenizer = FakeTokenizer()
    counter = QwenTokenCounter(tokenizer_path="", tokenizer=tokenizer)

    assert counter.count("one two three") == 3
    assert tokenizer.last_add_special_tokens is False
