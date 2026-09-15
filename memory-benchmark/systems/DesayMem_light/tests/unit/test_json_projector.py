import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from desaymem_light.adapters.json_mirror.file_projector import AtomicJsonTableProjector
from desaymem_light.contracts.mirror import MirrorEvent


def event(outbox_id, operation, value):
    return MirrorEvent(
        outbox_id=outbox_id, database="postgres", table_name="memory_items",
        primary_key={"id": "m1"}, operation=operation, row_data={"id": "m1", "value": value},
        row_version=outbox_id,
    )


@pytest.mark.asyncio
async def test_json_projection_matches_insert_update_delete():
    root = Path(".test-artifacts") / str(uuid4())
    try:
        projector = AtomicJsonTableProjector(root=root)
        await projector.apply(event(1, "INSERT", "a"))
        await projector.apply(event(2, "UPDATE", "b"))
        path = root / "postgres" / "memory_items.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        assert next(iter(document["rows"].values()))["data"]["value"] == "b"
        await projector.apply(event(3, "DELETE", "b"))
        assert json.loads(path.read_text(encoding="utf-8"))["rows"] == {}
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_json_projection_ignores_old_version():
    root = Path(".test-artifacts") / str(uuid4())
    try:
        projector = AtomicJsonTableProjector(root=root)
        await projector.apply(event(2, "INSERT", "new"))
        result = await projector.apply(event(1, "UPDATE", "old"))
        assert result.applied is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_json_projection_updates_existing_deployed_format():
    root = Path(".test-artifacts") / str(uuid4())
    path = root / "postgres" / "memory_items.json"
    try:
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "rows": {
                '{"id":"m1"}': {"version": 2, "data": {"id": "m1", "value": "old"}}
            },
            "table_version": 2,
        }), encoding="utf-8")
        projector = AtomicJsonTableProjector(root=root)
        await projector.apply(event(3, "UPDATE", "new"))
        document = json.loads(path.read_text(encoding="utf-8"))
        row = next(iter(document["rows"].values()))
        assert row["version"] == 3
        assert row["data"]["value"] == "new"
        assert document["table_version"] == 3
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_json_projection_replaces_embedding_with_placeholder():
    """Mirror files exist for inspection only; raw vectors carry no value
    there but dominate file size (~92% of memory_items.json). Vectors arrive
    from the outbox trigger as pgvector text, and as lists elsewhere."""
    root = Path(".test-artifacts") / str(uuid4())
    path = root / "postgres" / "memory_items.json"
    try:
        projector = AtomicJsonTableProjector(root=root)
        as_text = MirrorEvent(
            outbox_id=1, database="postgres", table_name="memory_items",
            primary_key={"id": "m1"}, operation="INSERT",
            row_data={"id": "m1", "embedding": "[" + ",".join(["0.01"] * 1024) + "]",
                      "content": "偏好周杰伦"},
            row_version=1,
        )
        as_list = MirrorEvent(
            outbox_id=2, database="postgres", table_name="memory_items",
            primary_key={"id": "m2"}, operation="INSERT",
            row_data={"id": "m2", "embedding": [0.5, 0.5], "content": "短向量不处理"},
            row_version=2,
        )
        await projector.apply(as_text)
        await projector.apply(as_list)
        document = json.loads(path.read_text(encoding="utf-8"))
        rows = document["rows"]
        assert rows['{"id":"m1"}']["data"]["embedding"] == "<vector:1024d>"
        assert rows['{"id":"m2"}']["data"]["embedding"] == [0.5, 0.5]
        # truncation must not mutate the event payload itself
        assert "0.01" in as_text.row_data["embedding"]
    finally:
        shutil.rmtree(root, ignore_errors=True)
