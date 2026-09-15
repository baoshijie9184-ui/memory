# Memory Observability — L1/L2/L3 观测接口与审计

## 概述

DesayMem 提供统一的三层记忆观测接口，用于查看当前用户在数据库中真实保存的全部记忆及其演化历史。

## L1/L2/L3 真实存储位置

| 层级 | 表 | memory_type / status | 含义 |
|------|-----|---------------------|------|
| L1 原子事实 | `memory_items` | `semantic_memory` | 从对话中抽取的具体事实，是 L2 和 L3 的事实来源 |
| L2 情景事件 | `memory_items` | `episodic_memory` | 事件摘要，metadata 含 `episode_status` (active/complete)、`source_memory_ids`、`occurred_at`、`occurred_end`、`confidence` |
| L3 用户画像 | `profile_beliefs` | status: active/superseded | 用户画像信念，含 subject/attribute/value/conditions/stability/confidence/support_count/evidence |

## 新增接口

### GET /v1/users/{user_id}/memory-layers

统一查询当前用户的 L1/L2/L3 全部记忆。

**查询参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| tenant_id | default | 租户隔离 |
| occupant_id | 可选 | 乘员位置过滤 |
| include_inactive | true | 是否包含 superseded L3 信念 |
| l1_limit | 100 (max 500) | L1 返回上限 |
| l2_limit | 100 (max 500) | L2 返回上限 |
| l3_limit | 100 (max 500) | L3 返回上限 |

**返回格式：**

```json
{
  "tenant_id": "oem_chery",
  "user_id": "user_driver",
  "stats": {
    "l1_count": 12,
    "l2_count": 3,
    "l2_active_count": 1,
    "l2_complete_count": 2,
    "l3_count": 5,
    "l3_active_count": 4,
    "l3_superseded_count": 1
  },
  "l1": [],
  "l2": [],
  "l3": [],
  "generated_at": "2025-01-01T00:00:00+00:00"
}
```

**注意：**
- 不返回 `embedding` 和 `attribute_embedding`
- 不返回系统提示词、API Key、原始 LLM 输出
- 时间统一输出 ISO8601
- 列表按 `updated_at` 倒序
- count 反映当前返回数组长度（returned_count 语义）

### GET /v1/users/{user_id}/memory-events

查询记忆审计事件（演化记录）。

**查询参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| tenant_id | default | 租户隔离 |
| layer | 可选 | L1/L2/L3 |
| event | 可选 | ADD/UPDATE/CONFIRM/COMPLETE/SUPERSEDE/COEXIST/DELETE/FORGET |
| memory_id | 可选 | 按记忆 ID 过滤 |
| limit | 100 (max 500) | 每页数量 |
| cursor | 可选 | 游标分页 |
| start_time | 可选 | 起始时间 (ISO8601) |
| end_time | 可选 | 结束时间 (ISO8601) |

**返回格式：**

```json
{
  "events": [
    {
      "id": "event-id",
      "memory_id": "memory-id",
      "layer": "L3",
      "object_type": "profile_belief",
      "event": "SUPERSEDE",
      "old_data": {"attribute": "车内温度偏好", "value": "22℃", "status": "active"},
      "new_data": {"attribute": "车内温度偏好", "value": "26℃", "status": "active", "superseded_id": "old-id"},
      "reason": "新信念替代旧信念",
      "source": "profile_distiller",
      "created_at": "2025-01-01T00:00:00+00:00"
    }
  ],
  "next_cursor": null,
  "count": 1
}
```

**安全要求：**
- tenant_id 和 user_id 强制隔离
- 非法 layer/event 返回 422
- 不返回 embedding
- 使用 created_at + id 稳定游标分页

### GET /v1/users/{user_id}/profile (扩展)

新增查询参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| include_superseded | false | 是否返回 superseded 信念 |
| occupant_id | 可选 | 乘员位置过滤 |
| limit | 100 (max 500) | 返回上限 |

保持原有调用兼容（不带新参数时行为不变）。

## 审计事件语义

### 事件类型

| 事件 | 层级 | 含义 |
|------|------|------|
| ADD | L1/L2/L3 | 新增记忆 |
| UPDATE | L1/L2/L3 | 更新已有记忆内容 |
| CONFIRM | L3 | 新证据确认已有 belief |
| COMPLETE | L2 | 旧 episode 由 active 变为 complete |
| SUPERSEDE | L3 | 新信念替代旧信念 |
| COEXIST | L3 | 条件不同的信念并存 |
| DELETE | L1/L2 | 人工删除单条记忆 |
| FORGET | 预留 | 自动遗忘（当前未启用） |

### COMPLETE vs SUPERSEDE vs DELETE vs FORGET 区别

- **COMPLETE**: L2 情景事件从 active 变为 complete，表示事件自然结束。记忆仍在数据库中。
- **SUPERSEDE**: L3 旧 belief 被 new belief 替代，旧 belief status 变为 superseded。旧 belief 仍在数据库中。
- **CONFIRM**: L3 新证据再次确认已有 belief，support_count+1，belief 内容不变。
- **COEXIST**: L3 同属性不同条件的信念并存（如夏天 22 度 / 冬天 26 度），两个 belief 都 active。
- **DELETE**: 人工删除单条 L1/L2 记忆。记忆从数据库移除，审计保留安全摘要。
- **FORGET**: 自动遗忘机制。**当前未实现，不产生 FORGET 事件。**

### 演化触发时机

每次 `POST /v1/memories` 写入新对话后，系统自动触发三层演化：

| 层级 | 可能产生的事件 | 触发代码 |
|------|---------------|----------|
| L1 | ADD | `_persist_texts` — 从对话中抽取原子事实 |
| L2 | UPDATE (续写) + ADD (新 episode) + COMPLETE (旧 episode) | `_upsert_episode` — 判断 continues/new |
| L3 | ADD (CREATE) + CONFIRM + SUPERSEDE + COEXIST | `distiller._apply_one` — 蒸馏画像信念 |

审计采用 safe_append 模式：审计写入失败只记录 warning，不阻断记忆写入。

### 前端观测面板

cockpit-frontend 右侧面板实时展示三层记忆状态，5 个标签页：

- **总览**：L1/L2/L3 计数、今日演化事件数、隔离测试
- **L1 事实**：content、source、scene、occupant、删除按钮
- **L2 情景**：摘要、episode_status、occurred_at、来源 L1 链接
- **L3 画像**：attribute、value、stability、status、evidence
- **演化记录**：审计事件倒序列表，支持层级/事件过滤

> 前后端完整交互文档见 [cockpit-frontend/docs/frontend-backend-interaction.md](https://github.com/psile/cockpit-frontend/blob/main/docs/frontend-backend-interaction.md)

## 自动遗忘状态

**当前未启用自动遗忘机制。**

- L2 COMPLETE 不等于 FORGET
- L3 SUPERSEDE 不等于 FORGET
- 人工 DELETE 不等于 FORGET
- 不会因为记忆长时间未被检索而删除
- API 支持 FORGET 事件类型，为未来预留

## 审计表

### PostgreSQL: memory_audit_events

迁移文件：`migrations/006_memory_observability.sql`

```sql
CREATE TABLE IF NOT EXISTS memory_audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    memory_id TEXT,
    layer TEXT NOT NULL,          -- L1/L2/L3
    object_type TEXT NOT NULL,
    event TEXT NOT NULL,          -- ADD/UPDATE/CONFIRM/COMPLETE/SUPERSEDE/COEXIST/DELETE/FORGET
    old_data JSONB,
    new_data JSONB,
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### SQLite history.db

继续保留，兼容 mem0 已有逻辑。不删除。

## 数据隔离

- 所有查询强制 `tenant_id` + `user_id` 隔离
- `occupant_id` 存在时进一步过滤
- 不允许跨用户查询
- 审计事件同样按 `tenant_id` + `user_id` 隔离

## delete_all 隐私语义

`DELETE /v1/users/{user_id}/memories?confirm=true` 代表**彻底清除**用户所有数据：

- 删除 L1 memory_items
- 删除 L2 episodic_memory
- 删除 L3 profile_beliefs
- 删除 session_messages
- 删除 memory_entities
- **同时清除该用户的审计事件**

**不得为调试保留用户已要求彻底删除的内容。**

单条 `DELETE /v1/users/{user_id}/memories/{memory_id}` 是普通开发删除，保留安全的审计信息（不含 embedding）。

## 数据库迁移

```bash
# 应用所有迁移（包括 006）
desaymem-migrate apply

# 检查 schema 是否就绪
desaymem-migrate check
```

迁移 006 支持重复执行（所有对象使用 `IF NOT EXISTS`）。

## 审计失败处理

- 审计写入失败时记录 warning 日志，不抛出异常
- 不影响已成功的 L1/L2/L3 写入
- 不会因为审计失败导致重复写入记忆
- 不允许静默吞掉所有异常

## 部署兼容

- PostgreSQL: 127.0.0.1:20143
- Qwen vLLM: 127.0.0.1:20140/v1
- BGE-M3: 127.0.0.1:20141/v1
- DesayMem API: 0.0.0.0:20144（当前实例，含观测接口）
- DesayMem API: 0.0.0.0:20142（旧实例，不含观测接口）
- EMBEDDING_DIMS=1024
- SQLite history.db 继续保留
- 不修改现有端口和 embedding 维度

### occupant_id 注意事项

`memory-layers` 和 `profile` 接口的 `occupant_id` 参数必须与存储时一致：

- 前端登录"乘员位置"选 `driver` → 存储和查询都用 `driver`
- 不传 `occupant_id` → 后端不过滤乘员，返回该用户全部记忆
- **不要硬编码默认值**（曾因路由层 `occupant_id or "primary"` 导致查询为空）

前端已在 memory-layers URL 中传递 `occupant_id={state.occupant}`，确保与存储一致。
