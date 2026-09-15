#!/usr/bin/env bash
# ============================================================
# LightMem / StructMem LoCoMo 一键流水线
#   清库 → add(构建记忆) → 镜像导出 → search(评测) → F1 计算
# 用法:
#   ./run_full_pipeline.sh                     # 全量 10 对话 (locomo10.json)
#   ./run_full_pipeline.sh --smoke             # 冒烟 1 对话 (locomo_smoke1.json)
#   ./run_full_pipeline.sh --smoke --skip-add  # 复用已有记忆库只重跑评测
#   ./run_full_pipeline.sh --smoke --no-think --skip-add  # 关思考对照评测
# 对照开关:
#   --no-think   传给 search_locomo.py --disable-thinking，通过 chat_template_kwargs
#                (enable_thinking=false) 强制 Qwen3 关闭思考直接作答。用于"思考开/关"
#                四象限对照（评测速度约 3 倍，completion token 大幅下降）。
#                只影响答案生成 LLM；judge 判分不在记忆系统成本内，不计算。
# 环境要求:
#   - vLLM 20140 (memory-llm) 已启动且带 --reasoning-parser qwen3
#   - bge-m3 模型在 /data/pengshuang/desaymem/models/bge-m3
# ============================================================
set -euo pipefail

# ---------- 可调配置 ----------
PYTHON=/data/pengshuang/memory-benchmark/envs/lightmem/bin/python
EXP_DIR=/data/pengshuang/memory-benchmark/systems/LightMem/experiments/locomo
DATA_FULL=/data/pengshuang/memory-benchmark/datasets/VehicleMem-Eval/datasets/locomo/locomo10.json
DATA_SMOKE=/data/pengshuang/memory-benchmark/data/lightmem/locomo_smoke1.json
QDRANT_PRE=/data/pengshuang/memory-benchmark/data/lightmem/qdrant_pre_update
QDRANT_POST=/data/pengshuang/memory-benchmark/data/lightmem/qdrant_post_update
LOG_DIR=/data/pengshuang/memory-benchmark/logs/experiments
RESULTS_ROOT=/data/pengshuang/memory-benchmark/results
TIKTOKEN_CACHE=/data/pengshuang/memory-benchmark/tmp/tiktoken_cache
LLM_KEY=boluoboluomi
LLM_BASE=http://127.0.0.1:20140/v1
LLM_MODEL=memory-llm

SMOKE=0; SKIP_ADD=0; NO_THINK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke) SMOKE=1; shift;;
    --skip-add) SKIP_ADD=1; shift;;
    --no-think) NO_THINK=1; shift;;
    *) echo "unknown arg: $1"; exit 1;;
  esac
done
THINK_FLAG=""
[[ $NO_THINK -eq 1 ]] && THINK_FLAG="--disable-thinking"

if [[ $SMOKE -eq 1 ]]; then
  DATA=$DATA_SMOKE; TAG=smoke
else
  DATA=$DATA_FULL; TAG=full
fi
[[ $NO_THINK -eq 1 ]] && TAG="${TAG}_nothink"

RUN_TS=$(date +%Y%m%d_%H%M%S)
RUN_ID="${RUN_TS}_${TAG}_structmem"
RESULTS_DIR=$RESULTS_ROOT/$RUN_ID
MIRROR_DIR=$RESULTS_DIR/memory_mirror
mkdir -p "$RESULTS_DIR" "$LOG_DIR"

echo "============================================================"
echo " LightMem/StructMem LoCoMo pipeline"
echo " mode=$TAG  dataset=$DATA"
echo " results -> $RESULTS_DIR"
echo "============================================================"

# ---------- Step 0: 清空测试库（防噪声） ----------
if [[ $SKIP_ADD -eq 0 ]]; then
  echo "[Step 0] 清空既有测试库..."
  # 删掉两个 qdrant 目录下的所有 conv-* 数据（保留目录本身）
  rm -rf "$QDRANT_PRE" "$QDRANT_POST"
  mkdir -p "$QDRANT_PRE" "$QDRANT_POST"
  # 删掉 add 脚本自身的运行日志（分 sample 的 conv-*.log 会按存在与否跳过处理）
  rm -rf "$EXP_DIR/logs"
  echo "  cleared: $QDRANT_PRE $QDRANT_POST $EXP_DIR/logs"
fi

export TIKTOKEN_CACHE_DIR=$TIKTOKEN_CACHE

# ---------- Step 1: add（构建记忆） ----------
if [[ $SKIP_ADD -eq 0 ]]; then
  echo "[Step 1] add_memory 构建记忆 (可能数小时)..."
  LOCOMO_DATA_PATH=$DATA nohup $PYTHON "$EXP_DIR/add_locomo.py" \
    --extraction_mode event --enable_summary \
    --summary_time_window 3600 --summary_top_k_seeds 15 \
    --workers 1 \
    > "$LOG_DIR/lightmem_${TAG}_add_${RUN_TS}.log" 2>&1 &
  ADD_PID=$!
  echo "  add pid=$ADD_PID, log=$LOG_DIR/lightmem_${TAG}_add_${RUN_TS}.log"
  wait $ADD_PID
  echo "[Step 1] add 完成"
fi

# ---------- Step 1.5: 修复 summary 库缺失（上游 bug） ----------
# add_locomo.py Phase 2.5 把摘要写进 pre_update/{conv}_summary，但从未同步到
# post_update —— search 读 post_update → 永远 0 摘要（实测冒烟两次都空转）。
# 布局注意: lightmem 写出的真数据在 {lib}/conv-xx_summary/collection/conv-xx_summary/
# 四层深处；search 的 fallback 读取点是 {lib}/conv-xx_summary/collection/conv-xx_summary/。
# 正确同步 = 把深层真数据放到读取点（只 sqlite 数据文件 + meta.json）。
if [[ $SKIP_ADD -eq 0 ]]; then
  echo "[Step 1.5] 同步 summary 库 pre_update -> post_update (修复上游摘要不同步 bug)..."
  synced=0
  for d in "$QDRANT_PRE"/*_summary; do
    [[ -d "$d" ]] || continue
    name=$(basename "$d")
    src_deep="$d/$name/collection/$name"      # lightmem 真数据 (四层深处)
    dst="$QDRANT_POST/$name/collection/$name" # search fallback 读取点
    if [[ ! -f "$src_deep/storage.sqlite" ]]; then
      echo "  WARN: $src_deep/storage.sqlite 不存在，跳过 $name"
      continue
    fi
    mkdir -p "$dst"
    cp "$src_deep/storage.sqlite" "$dst/storage.sqlite"
    # meta.json: 双层都放，保证 Qdrant API 模式也能识别
    [[ -f "$d/$name/meta.json" ]] && cp "$d/$name/meta.json" "$QDRANT_POST/$name/meta.json"
    synced=$((synced+1))
    echo "  synced $name"
  done
  # 清掉 post_update 根级 collection/ 空库（collection_entry_count 误建，遮蔽读取）
  rm -rf "$QDRANT_POST/collection"
  echo "  synced $synced summary collections"
fi

# ---------- Step 2: 镜像导出（人可读的数据库内容快照） ----------
echo "[Step 2] 导出记忆镜像 JSON..."
$PYTHON "$EXP_DIR/dump_memory_mirror.py" \
  --qdrant-dir "$QDRANT_POST" \
  --mirror-dir "$MIRROR_DIR" | tee "$RESULTS_DIR/mirror_dump.log"
echo "[Step 2] 镜像 -> $MIRROR_DIR (每条记忆的完整 payload 可直接查看)"

# ---------- Step 3: search 评测 ----------
echo "[Step 3] search 评测 (LLM 生成 + judge)..."
$PYTHON "$EXP_DIR/search_locomo.py" \
  --dataset "$DATA" \
  --qdrant-dir "$QDRANT_POST" \
  --output-dir "$RESULTS_DIR" \
  --embedder huggingface --embedding-model-path /data/pengshuang/desaymem/models/bge-m3 \
  --retrieval-mode combined --total-limit 60 \
  --enable-summary --summary-limit 5 \
  $THINK_FLAG \
  --llm-api-key $LLM_KEY --llm-base-url $LLM_BASE --llm-model $LLM_MODEL \
  --judge-api-key $LLM_KEY --judge-base-url $LLM_BASE --judge-model $LLM_MODEL \
  2>&1 | tee "$LOG_DIR/lightmem_${TAG}_search_${RUN_TS}.log"
echo "[Step 3] search 完成"

# ---------- Step 4: token F1 ----------
echo "[Step 4] 计算 token F1..."
$PYTHON "$EXP_DIR/eval_f1.py" \
  --results-dir "$RESULTS_DIR" \
  --output "$RESULTS_DIR/f1_summary.json" | tee "$RESULTS_DIR/f1_report.txt"

echo "============================================================"
echo " 完成: $RESULTS_DIR"
echo "   summary.json      — J-score / token 统计"
echo "   f1_summary.json   — token F1 分类别结果"
echo "   sample_*.json     — 每题 prediction/reference/token_usage"
echo "   memory_mirror/    — 记忆库内容快照(条目级)"
echo "============================================================"
