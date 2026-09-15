from desaymem.core.memory import DesayMemory
from tests.conftest import LIVE_TENANT


async def test_live_extractor_returns_climate_fact(memory: DesayMemory):
    extracted = await memory.extractor.extract(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        existing_memories=[],
        last_k_messages=[{"role": "user", "content": "上一轮说了座椅加热"}],
    )
    assert extracted
    blob = " ".join(item.get("text") or "" for item in extracted)
    assert "22" in blob or "空调" in blob
