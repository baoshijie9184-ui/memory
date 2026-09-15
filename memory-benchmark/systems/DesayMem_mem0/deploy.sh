#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/opt/DesayMem_mem0"
BRANCH="main"
HEALTH_URL="http://127.0.0.1:8766/health"

echo "======================================"
echo " DesayMem 开始更新"
echo "======================================"

cd "$PROJECT_DIR"

if [ ! -f ".env" ]; then
    echo "错误：未找到 $PROJECT_DIR/.env"
    exit 1
fi

echo "[1/4] 拉取 GitHub 最新代码..."
git pull --ff-only origin "$BRANCH"

echo "[2/4] 重新构建并启动容器..."
docker compose up --build -d

echo "[3/4] 等待服务启动..."
for i in $(seq 1 30); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        echo "[4/4] 健康检查通过"
        curl -fsS "$HEALTH_URL"
        echo
        docker compose ps
        echo "======================================"
        echo " DesayMem 更新成功"
        echo "======================================"
        exit 0
    fi

    echo "等待服务启动：${i}/30"
    sleep 2
done

echo "错误：服务健康检查未通过"
echo "最近的 API 日志："
docker compose logs --tail=100 api
exit 1
