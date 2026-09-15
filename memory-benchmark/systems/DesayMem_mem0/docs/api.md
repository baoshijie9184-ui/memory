# HTTP API

Base URL: `http://localhost:8000`

Interactive docs: `http://localhost:8000/docs`

## GET /health

Returns process and database status. HTTP 503 when PostgreSQL is down.

## POST /v1/memories

Extract and store memories from a conversation.

```json
{
  "tenant_id": "default",
  "user_id": "user_001",
  "vehicle_id": "vehicle_001",
  "occupant_id": "primary",
  "session_id": "session_001",
  "scene": "driving",
  "messages": [
    {"role": "user", "content": "我开车的时候喜欢把空调调到22度"}
  ],
  "infer": true,
  "memory_type": null
}
```

Optional add fields:

- `infer` (default `true`): when `false`, skip LLM extraction and store raw messages.
- `memory_type`: only `procedural_memory` is accepted besides omitting the field.
  That path writes a procedure summary instead of ADD-only facts.

`GET /v1/users/{user_id}/memories` also accepts `memory_type`.

Response:

```json
{
  "memories": [
    {
      "id": "uuid",
      "content": "用户开车时喜欢把空调调到22度",
      "tenant_id": "default",
      "user_id": "user_001",
      "event": "ADD"
    }
  ],
  "extracted": 1,
  "skipped_duplicates": 0,
  "beliefs_applied": 1,
  "episode": {
    "id": "uuid",
    "content": "用户说明开车时空调偏好",
    "memory_type": "episodic_memory"
  }
}
```

`episode` 仅在 L2 成功写入或续写时出现。`beliefs_applied` 是本次蒸馏实际落地的信念条数。

如需指定事实发生时间，在 `metadata.occurred_at` 传入带日期和时区的 ISO-8601 绝对时间。未传时系统使用接收时间；传入“上周”等相对文本或非法时间会返回校验错误。系统另写入 `observed_at` 表示服务收到并持久化该事实的时间。

## POST /v1/memories/search

```json
{
  "tenant_id": "default",
  "user_id": "user_001",
  "query": "用户习惯的空调温度是多少",
  "top_k": 5,
  "filters": {"vehicle_id": "vehicle_001"}
}
```

`tenant_id` and `user_id` are mandatory isolation keys. Search over-fetches
with the nine-step hybrid (semantic + BM25 + entity boost), then a semantic
reranker selects among candidates. The response always includes `profile`
(current L3 beliefs). Vector similarity alone is never enough.

```json
{
  "memories": [{ "id": "uuid", "content": "...", "memory_type": "semantic_memory" }],
  "query": "用户习惯的空调温度是多少",
  "top_k": 5,
  "profile": {
    "narrative": "User cabin temperature while driving: 22C",
    "beliefs": []
  }
}
```

## GET /v1/users/{user_id}/profile

Query: `tenant_id`. Returns current active beliefs and the narrative snapshot.
Does not perform vector search.

## GET /v1/users/{user_id}/memories

Query: `tenant_id`, optional `vehicle_id` / `occupant_id` / `session_id` /
`scene` / `source` / `memory_type`, `limit`.

## GET /v1/users/{user_id}/memories/{memory_id}/history

Returns SQLite history events for that memory (`ADD` / `DELETE`). History
is kept after delete, matching Mem0 `Memory.history(memory_id)`.

## DELETE /v1/users/{user_id}/memories/{memory_id}

Deletes only when the row belongs to that tenant+user. Cross-user deletes
return 404.

## DELETE /v1/users/{user_id}/memories?confirm=true

Clears that tenant+user, including L1/L2 rows, entities, last-k messages, and L3 beliefs. Refuses without `confirm=true`.

History lookup first verifies that the requested memory belongs to the path `user_id` and query `tenant_id`; a cross-user or cross-tenant lookup returns 404.

## Errors

| Class | HTTP |
| --- | --- |
| ValidationError | 400 |
| MemoryNotFoundError | 404 |
| DatabaseError | 503 |
| LLMError / EmbeddingError | 502 |
