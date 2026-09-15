# DesayMem 云端部署指南（共享服务器，8766 端口）

> 适用于已有一台 2C4G 云服务器（正在运行 company-agent@8765），新增 DesayMem@8766 共存部署。

## 架构

```
公网用户
   ↓
80 / 443
   ↓
Nginx（已有）
   ├── /agent     → 127.0.0.1:8765  (company-agent，已有)
   └── /memory    → 127.0.0.1:8766  (DesayMem API)
                    127.0.0.1:5432  (PostgreSQL，仅容器内部)
```

## 资源评估

| 服务 | 内存占用 | CPU |
|------|----------|-----|
| company-agent（已有） | ~1-2 GB | 低（调外部 API） |
| DesayMem API | ~200 MB | 低 |
| PostgreSQL + pgvector | ~300-500 MB | 低 |
| **总计** | **~2-2.7 GB / 4 GB** | 够用 |

如果内存紧张，PostgreSQL 可加 `shared_buffers=128MB` 限制。

---

## 部署步骤

### 1. SSH 登录服务器

```bash
ssh root@你的服务器IP
```

### 2. 拉取代码

```bash
cd /opt
git clone https://github.com/psile/DesayMem_mem0.git
cd DesayMem_mem0
```

### 3. 修改端口为 8766

编辑 `docker-compose.yml`，把 api 服务的端口改掉，postgres 端口不暴露：

```yaml
  api:
    # ... 其他不变 ...
    ports:
      - "127.0.0.1:8766:8000"    # 改这一行：只绑定本地 8766

  postgres:
    # ... 其他不变 ...
    ports:
      - "127.0.0.1:5432:5432"    # 只绑定本地，不对外
```

### 4. 配置 .env

```bash
cp .env.example .env
nano .env
```

```env
APP_PORT=8000

POSTGRES_DSN=postgresql://desaymem:change_me@postgres:5432/desaymem

# DashScope 密钥
LLM_MODEL=qwen-plus
LLM_API_KEY=sk-你的DashScope密钥
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_API_KEY=sk-你的DashScope密钥
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_DIMS=1024

HISTORY_DB_PATH=/app/data/history.db
```

### 5. 启动 DesayMem

```bash
docker compose up --build -d
```

验证：

```bash
# 容器状态
docker compose ps

# 健康检查
curl http://127.0.0.1:8766/health
# 应返回 {"status":"ok"}

# 快速写入测试
curl -X POST http://127.0.0.1:8766/v1/memories \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user",
    "tenant_id": "test",
    "messages": [{"role": "user", "content": "测试写入"}]
  }'

# 检索测试
curl -X POST http://127.0.0.1:8766/v1/memories/search \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user",
    "tenant_id": "test",
    "query": "测试"
  }'
```

### 6. 配置 Nginx

在已有 Nginx 配置中增加 DesayMem 入口。

**方案 A：按路径分流（无域名，推荐测试用）**

在已有的 Nginx server block 中增加：

```nginx
# DesayMem 记忆服务
location /memory/ {
    proxy_pass http://127.0.0.1:8766/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 60s;
}
```

重载 Nginx：

```bash
nginx -t && nginx -s reload
```

验证：

```bash
curl http://127.0.0.1/memory/health
# 或从本地电脑：
curl http://你的服务器IP/memory/health
```

API 调用示例：

```bash
curl -X POST http://你的服务器IP/memory/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u1","tenant_id":"test","messages":[{"role":"user","content":"我喜欢空调22度"}]}'
```

**方案 B：按子域名分流（有域名时推荐）**

```nginx
server {
    listen 80;
    server_name memory.你的域名.com;

    location / {
        proxy_pass http://127.0.0.1:8766;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

### 7. 本地验证连通

回到本地电脑：

```bash
# 健康检查
curl http://你的服务器IP/memory/health

# 跑车机模拟器（路径模式需改 base_url）
python scripts/cockpit_simulator.py --http --base-url http://你的服务器IP/memory
```

---

## 运维命令

```bash
# 查看日志
docker compose logs -f api

# 重启服务
docker compose restart api

# 查看资源占用
docker stats

# 停止
docker compose down

# 更新代码
git pull && docker compose up --build -d
```

---

## 常见问题

### Q: Swagger 文档页面打不开？

路径代理模式下 `/memory/docs` 页面会尝试加载 `/openapi.json`（绝对路径），导致 404。

解决办法：用 SSH 隧道直接访问容器端口，绕过 Nginx 路径问题：

```bash
# 本地电脑执行
ssh -L 8766:127.0.0.1:8766 root@你的服务器IP
# 然后浏览器打开 http://localhost:8766/docs
```

### Q: 内存不够怎么办？

```bash
# 查看
free -h

# 限制 PostgreSQL 内存，在 docker-compose.yml 的 postgres 加：
environment:
  POSTGRES_SHARED_BUFFERS: 128MB
```

### Q: 数据持久化？

PostgreSQL 数据在 Docker volume `postgres_data` 中，`docker compose down` 不会删除。只有 `docker compose down -v` 才会删数据卷。
