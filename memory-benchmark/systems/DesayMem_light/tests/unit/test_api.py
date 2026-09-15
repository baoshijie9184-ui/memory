from uuid import uuid4

from fastapi.testclient import TestClient

from desaymem_light.api.app import create_app
from desaymem_light.application.ingest_service import IngestResult
from desaymem_light.domain.models import SearchResult


class Ingest:
    message = None

    async def ingest(self, message):
        self.message = message
        return IngestResult(message_id=message.id, segmentation_job_id=uuid4())


class Retriever:
    async def search(self, request):
        return SearchResult(request_id=request.request_id, agent_context="")


class Tokens:
    def count(self, text): return 3


def app():
    return create_app(ingest_service=Ingest(), retriever=Retriever(), tokenizer=Tokens())


def app_with_ingest():
    ingest = Ingest()
    return create_app(ingest_service=ingest, retriever=Retriever(), tokenizer=Tokens()), ingest


def test_ingest_accepts_vehicle_scoped_message():
    response = TestClient(app()).post("/v1/messages", json={
        "request_id": "req-1", "sequence_no": 1, "role": "user", "content": "播放音乐",
        "scope": {"tenant_id": "t", "user_id": "u", "vehicle_id": "v",
                  "occupant_id": "driver", "session_id": "s"},
    })
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_search_contract_and_health_endpoints():
    client = TestClient(app())
    request_id = str(uuid4())
    response = client.post("/v1/memories/search", json={
        "request_id": request_id, "query": "我通勤听什么",
        "scope": {"tenant_id": "t", "user_id": "u", "vehicle_id": "v",
                  "occupant_id": "driver"},
        "tags": ["音乐"], "vehicle_only": True,
    })
    assert response.status_code == 200
    assert response.json()["request_id"] == request_id
    assert client.get("/health/live").status_code == 200


def test_invalid_payload_uses_stable_error_shape():
    response = TestClient(app()).post("/v1/messages", json={})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_ingest_preserves_explicit_role_and_occurred_at():
    api, ingest = app_with_ingest()
    response = TestClient(api).post("/v1/messages", json={
        "request_id": "req-role-time", "sequence_no": 0,
        "role": "assistant", "content": "正在为您播放音乐",
        "occurred_at": "2026-09-10T10:00:00+08:00",
        "scope": {"tenant_id": "t", "user_id": "u", "vehicle_id": "v",
                  "occupant_id": "driver", "session_id": "s"},
    })
    assert response.status_code == 202
    assert str(ingest.message.role) == "assistant"
    assert ingest.message.occurred_at.isoformat() == "2026-09-10T10:00:00+08:00"


def test_ingest_rejects_naive_occurred_at():
    response = TestClient(app()).post("/v1/messages", json={
        "request_id": "req-naive-time", "sequence_no": 0,
        "role": "user", "content": "播放音乐",
        "occurred_at": "2026-09-10T10:00:00",
        "scope": {"tenant_id": "t", "user_id": "u", "vehicle_id": "v",
                  "occupant_id": "driver", "session_id": "s"},
    })
    assert response.status_code == 422
