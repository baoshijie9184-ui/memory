# DesayMem Light

面向云端测试的轻量化车载记忆系统。V1优先保证方案简单、来源可追溯、LLM调用可控、数据可审计，并通过稳定接口为后续算法替换保留空间。

> 当前状态：本地单元测试通过；云端部署前仍须在目标服务器完成Migration、Preflight和Smoke Test。

## 架构

```text
车端消息
  -> SessionBuffer
  -> LightMem风格Topic切分（Embedding，无LLM）
  -> Mem0 ADD-only L1 Fact（每个Topic最多一次LLM）
  -> 确定性Event（无LLM）
  -> StructMem风格Cross-Event（默认累计10个Event后一次LLM）
  -> TiMEM风格L3 Profile（每批一次LLM）
  -> Tag、时间、身份过滤与BGE-M3向量检索（默认无LLM）
```

全链路保留 `tenant_id / user_id / vehicle_id / occupant_id / session_id`。

## 主要特性

- Qwen3-32B OpenAI-compatible接口；
- BGE-M3 Embedding，V1固定1024维；
- PostgreSQL与pgvector作为业务事实源；
- SQLite保存兼容历史和小型消息缓存；
- PostgreSQL Outbox将业务表逐行同步为JSON；
- JSON增删改与数据库对应，采用原子文件替换；
- 多值画像默认共存，例如用户可同时喜欢多个歌手；
- Fact、Event、Cross-Event、Profile保存来源关系与审计记录；
- API、算法、模型、数据库、镜像和Worker均通过接口解耦。

## 参考来源

- Mem0：ADD-only Fact抽取与检索流程；
- LightMem：SessionBuffer、Topic切分和Token触发；
- StructMem：Event与Cross-Event表达；
- Memos：Tag组织和时间过滤；
- TiMEM：周期画像更新与画像摘要；
- Graphiti：有效时间与失效时间建模；
- `DesayMem_mem0`：现有云端配置、PostgreSQL、pgvector及时间冲突处理经验。

具体版本与借鉴点见[provenance.yaml](provenance.yaml)，第一版限制见[已知限制](docs/KNOWN_LIMITATIONS_V1.md)。

## 目录结构

```text
src/desaymem_light/
  api/                 HTTP接口
  application/         Session、L1、L2、L3编排
  modules/             可插拔算法实现
  contracts/           稳定插件和仓储接口
  adapters/            Qwen、BGE、PostgreSQL、SQLite、JSON
  workers/             任务与镜像Worker
  bootstrap/           配置、注册表和生产装配
migrations/
  postgres/            001—011
  sqlite/              001—004
deploy/                 Linux安装、systemd与验收脚本
docs/                   架构、表结构及部署设计
```

## 本地开发

要求Python 3.10—3.13。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[tokenizer,dev]'
pytest -q
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
pip install -e ".[tokenizer,dev]"
pytest -q
```

Tokenizer必须预先放在本地路径，运行时不会从互联网自动下载。

## 配置

复制示例文件，但不要提交真实密码：

```bash
cp config/cloud-test.example.yaml config/cloud-test.yaml
cp .env.server.example /etc/desaymem-light/desaymem-light.env
chmod 600 /etc/desaymem-light/desaymem-light.env
```

必须配置Qwen、BGE、Tokenizer、PostgreSQL，以及独立的SQLite和JSON路径。HTTP端口不能与现有`DesayMem_mem0`冲突。

## 远程Linux服务器部署

开发电脑不需要Docker。代码上传服务器后执行：

```bash
sudo bash /opt/desaymem-light/deploy/server_install.sh
sudo ENV_FILE=/etc/desaymem-light/desaymem-light.env \
  bash /opt/desaymem-light/deploy/server_validate.sh
```

校验通过后启动：

```bash
sudo systemctl enable --now desaymem-light-mirror.service
sudo systemctl enable --now desaymem-light-worker.service
sudo systemctl enable --now desaymem-light-api.service
python3 /opt/desaymem-light/deploy/smoke_test.py \
  --base-url http://127.0.0.1:8000 --wait-seconds 180
```

详细步骤见[远程服务器部署说明](docs/REMOTE_SERVER_DEPLOYMENT.md)。如由云端Codex检查，请将[云端Codex验收任务](CLOUD_CODEX_VERIFICATION.md)作为执行入口。

## API

### 写入消息

`POST /v1/messages`

```json
{
  "request_id": "request-001",
  "scope": {
    "tenant_id": "tenant-1",
    "user_id": "user-1",
    "vehicle_id": "vehicle-1",
    "occupant_id": "driver",
    "session_id": "session-1"
  },
  "sequence_no": 1,
  "role": "user",
  "content": "上班路上播放周杰伦",
  "metadata": {"tags": ["音乐", "通勤"]}
}
```

返回`202 Accepted`，后续处理由PostgreSQL任务队列异步完成。

### 检索记忆

`POST /v1/memories/search`

```json
{
  "request_id": "8ec62983-e21e-4372-a62f-f7751407bd0d",
  "query": "我通勤时喜欢听什么？",
  "scope": {
    "tenant_id": "tenant-1",
    "user_id": "user-1",
    "vehicle_id": "vehicle-1",
    "occupant_id": "driver"
  },
  "tags": ["音乐"],
  "top_k": 10,
  "vehicle_only": true
}
```

返回Fact、Event、Cross-Event、当前画像和可直接传给对话模型的`agent_context`。检索默认一次Embedding、零LLM。

### 车控结构化记忆（P1/P2 薄实现）

- `POST /v1/operations`：写入 command 的 `requested`/`result` 或 `observed_action`（幂等、乱序关联、未知工具可存历史但标 `unverified`）。
- `POST /v1/trips`：行程开始/完成 upsert。
- `POST /v1/vehicle-memory/search`：`purpose=answer` 返回历史操作/观察；`purpose=suggestion` 仅返回 active Preference 与 Skill 提示。

Skill 蒸馏默认只产生 `candidate`，**不自动 published**；`suggestion` 不会把 candidate 标为可执行物理命令，只返回 steps/slots/evidence/missing_context。

健康接口：`GET /health/live`、`GET /health/ready`。

## 进程命令

```text
desaymem-api
desaymem-worker
desaymem-mirror-worker
desaymem-migrate
desaymem-preflight
```

Migration必须作为独立初始化步骤运行；API和Worker不会自动修改数据库结构。

## 数据与成本

PostgreSQL是唯一业务事实源。JSON通过Outbox实现可恢复的最终一致，不是跨数据库与文件系统的强事务。第一版严格镜像数据库行，包括Embedding，因此需要监控JSON磁盘容量。

主要调用预算：

- Topic：Embedding，无LLM；
- Fact：每个Topic最多一次LLM；
- Event：零LLM；
- Cross-Event：默认每10个Event最多一次LLM；
- Profile：每批最多一次LLM；
- Retrieval：一次Embedding，默认零LLM。

## 文档

- [整体算法架构图](docs/ALGORITHM_ARCHITECTURE_DIAGRAM.md)
- [总体架构](docs/ARCHITECTURE_V1.md)
- [数据模型与JSON镜像](docs/DATA_AND_JSON_MIRROR_V1.md)
- [九步检索](docs/SCHEMA_V1_STEP6_RETRIEVAL.md)
- [云端部署设计](docs/ARCHITECTURE_V1_STEP9_DEPLOYMENT.md)
- [远程服务器部署](docs/REMOTE_SERVER_DEPLOYMENT.md)
- [云端Codex验收](CLOUD_CODEX_VERIFICATION.md)

## 验证状态

- 本地单元测试：通过；
- Python源码与验收脚本编译：通过；
- 云端PostgreSQL迁移：待目标服务器执行；
- Qwen/BGE连通性：待`desaymem-preflight`验证；
- 端到端链路：待云端`smoke_test.py`验证。

只有Migration、Preflight和Smoke Test全部通过，才应进入云端测试流量。
