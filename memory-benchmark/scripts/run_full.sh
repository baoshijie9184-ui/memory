#!/bin/bash
# ============================================================================
# 记忆评测全量一键脚本 — 建库(structmem系) → 评测 → 结果归档到统一 results 仓库
#
# 用法: 改下面 3 个变量后 ./run_full.sh
#   系统名:   none / mem0 / structmem / structmem-nosummary
#   数据集名: locomo / vehiclemembench / longmemeval / carmem / desaymem
#   模型名:   models.yaml 里的 key（当前主力 memory-llm）
#
# 逻辑:
#   - mem0 / none: 不需要离线库，直接评测
#   - structmem / structmem-nosummary: 先建库（断点续跑 --skip-existing），再评测
#     · locomo 用已有 LoCoMo 全量库（自动跳过建库）
#     · 其他数据集逐样本建 collection
#   - 结果统一落 benchmark results 仓库（目录规划 §4.7，每次唯一目录不覆盖历史）:
#       results/raw/{时间戳}_{数据集}_{系统}_{模型}/
#         ├── run.log       全程汇总
#         ├── build_lib.log 建库日志（structmem 系）
#         ├── eval.log      评测日志
#         └── 评测明细 json/txt 副本
#   - 评测原始输出同时留在 datasets/VehicleMem-Eval/results/
#
# 模型/环境变量配置:
#   - 评测端 LLM/嵌入: config/models.yaml（--model-config），不需要环境变量
#   - 建库端/系统特有: config/systems/{系统名}.env（自动 source，改那个文件）
#     · structmem.env 控制 build_lightmem_lib.py 的 LLM（LIGHTMEM_API_BASE/KEY/MODEL）
#   - 无 .env 的系统（none/mem0）自动跳过
# ============================================================================

# ---------------- 只改这 3 个 ----------------
MEMORY_SYSTEM="structmem"        # none / mem0 / structmem / structmem-nosummary
DATASET="locomo"                # locomo / vehiclemembench / longmemeval / carmem / desaymem
MODEL_CONFIG="memory-llm"       # config/models.yaml 的 key
# --------------------------------------------

# ---------------- 可选覆盖（一般不动） ----------------
GPU_ID=""   # 手动指定建库 GPU（留空=自动选显存最大的卡）。仅建库用 GPU，评测不用（走 LLM/embedding API）
# ------------------------------------------------------

# 固定路径
BENCH_ROOT="/data/pengshuang/memory-benchmark"
EVAL_ROOT="$BENCH_ROOT/datasets/VehicleMem-Eval"
BUILD_SCRIPT="$BENCH_ROOT/systems/LightMem/experiments/locomo/build_lightmem_lib.py" #评测仓库
LIB_ROOT="$BENCH_ROOT/data/lightmem/qdrant_new_datasets/qdrant_post_update"

# ---- python 环境加载（$BENCH_ROOT/envs/，不能混用）----
# 评测用 envs/eval；structmem 建库用 envs/lightmem（要 lightmem/qdrant_client/tiktoken + 本地 GPU 模型）。
# 两个 PY 变量可被 config/systems/{系统名}.env 覆盖——新算法若需要专属环境，在它的 .env 里写:
#   PY_EVAL=$BENCH_ROOT/envs/{新环境}/bin/python      （评测进程）
#   PY_BUILD=$BENCH_ROOT/envs/{新环境}/bin/python      （建库进程，仅需要建库的系统）
PY_EVAL="${PY_EVAL:-$BENCH_ROOT/envs/eval/bin/python}"
PY_LIGHTMEM="${PY_BUILD:-$BENCH_ROOT/envs/lightmem/bin/python}"
echo "评测环境: $PY_EVAL | 建库环境: $PY_LIGHTMEM"

# tiktoken 离线缓存（不加会联网下载超时）
export TIKTOKEN_CACHE_DIR="$BENCH_ROOT/tmp/tiktoken_cache"

# 各数据集全量建库样本数（与 datasets.yaml --full 一致）
declare -A FULL_SIZE=(
  [carmem]=100
  [vehiclemembench]=50
  [longmemeval]=500
  [desaymem]=1          # 特殊：脚本内部自建 10 个 scope collection，循环 1 次即可
  [locomo]=0            # 用已有 LoCoMo 全量库，不建
)

declare -A PREFIX_MAP=(
  [carmem]=carmem_
  [vehiclemembench]=vehiclemembench_
  [longmemeval]=longmemeval_
  [desaymem]=desaymem_
)

TS=$(date +%Y%m%d_%H%M%S)
RESULT_DIR="$BENCH_ROOT/results/raw/${TS}_${DATASET}_${MEMORY_SYSTEM}_${MODEL_CONFIG}"
mkdir -p "$RESULT_DIR"
LOG="$RESULT_DIR/run.log"

# ---- per-system 环境覆盖（不同算法配置不同，各归 config/systems/{系统名}.env）----
# 注意: 放在 PY_EVAL/PY_LIGHTMEM 默认值之后 → .env 里写的 PY_EVAL/PY_BUILD 生效（source 覆盖同名变量）
SYSTEM_ENV="$EVAL_ROOT/config/systems/${MEMORY_SYSTEM}.env"
if [[ -f "$SYSTEM_ENV" ]]; then
  set -a; source "$SYSTEM_ENV"; set +a
  echo "已加载系统配置: $SYSTEM_ENV" | tee -a "$LOG"
  echo "评测环境: $PY_EVAL | 建库环境: $PY_LIGHTMEM" | tee -a "$LOG"
fi

echo "=== 全量评测: $DATASET × $MEMORY_SYSTEM × $MODEL_CONFIG ===" | tee -a "$LOG"
echo "=== 结果目录: $RESULT_DIR ===" | tee -a "$LOG"

# ---------- 阶段 1: 建库（仅 structmem 系需要） ----------
NEED_LIB=0
[[ "$MEMORY_SYSTEM" == "structmem" || "$MEMORY_SYSTEM" == "structmem-nosummary" ]] && NEED_LIB=1

if [[ $NEED_LIB -eq 1 && "${FULL_SIZE[$DATASET]}" -gt 0 ]]; then
  echo "[1/2] 建离线库（断点续跑，已建的自动跳过）..." | tee -a "$LOG"
  if [[ -n "$GPU_ID" ]]; then
    GPU_FREE="$GPU_ID"
  else
    GPU_FREE=$(nvidia-smi --query-gpu=index,memory.free --format=csv,noheader,nounits 2>/dev/null | sort -t, -k2 -rn | head -1 | cut -d, -f1)
  fi
  echo "  使用 GPU $GPU_FREE（仅建库加载本地压缩/嵌入模型用）" | tee -a "$LOG"
  CUDA_VISIBLE_DEVICES=$GPU_FREE "$PY_LIGHTMEM" -B -u "$BUILD_SCRIPT" \
    --dataset "$DATASET" --turn-limit 0 --extraction-mode event \
    --skip-existing >> "$RESULT_DIR/build_lib.log" 2>&1
  if [[ $? -ne 0 ]]; then
    echo "  建库失败，看 $RESULT_DIR/build_lib.log；已建部分下次重跑自动续" | tee -a "$LOG"
  fi
else
  if [[ $NEED_LIB -eq 1 ]]; then
    echo "[1/2] locomo: 使用已有 LoCoMo 全量库，跳过建库" | tee -a "$LOG"
  else
    echo "[1/2] $MEMORY_SYSTEM 不需要离线库，跳过建库" | tee -a "$LOG"
  fi
fi

# ---------- 阶段 2: 评测（--full 全量） ----------
echo "[2/2] 评测 (--full)..." | tee -a "$LOG"
cd "$EVAL_ROOT"

RUN_ENV=()
if [[ $NEED_LIB -eq 1 ]]; then
  if [[ "$DATASET" == "locomo" ]]; then
    RUN_ENV=(env LIGHTMEM_LIB_ROOT="$BENCH_ROOT/data/lightmem/qdrant_post_update")
  else
    RUN_ENV=(env LIGHTMEM_LIB_ROOT="$LIB_ROOT" LIGHTMEM_COLLECTION_PREFIX="${PREFIX_MAP[$DATASET]}")
  fi
fi

"${RUN_ENV[@]}" "$PY_EVAL" -B -u run.py \
  --dataset "$DATASET" \
  --memory-system "$MEMORY_SYSTEM" \
  --model-config "$MODEL_CONFIG" \
  --full >> "$RESULT_DIR/eval.log" 2>&1

EVAL_EXIT=$?

# 把评测明细 json/txt 副本拷进本次 run 目录（找该组合最新文件）
LATEST=$(ls -t "$EVAL_ROOT/results/${DATASET}_${MODEL_CONFIG}_${MEMORY_SYSTEM}"_*.json 2>/dev/null | head -1)
if [[ -n "$LATEST" ]]; then
  cp "$LATEST" "$RESULT_DIR/" 2>/dev/null
  [[ -f "${LATEST%.json}.txt" ]] && cp "${LATEST%.json}.txt" "$RESULT_DIR/"
fi

echo "" | tee -a "$LOG"
echo "=== 完成 (eval exit=$EVAL_EXIT) ===" | tee -a "$LOG"
echo "=== 本次结果: $RESULT_DIR ===" | tee -a "$LOG"
echo "  run.log / build_lib.log / eval.log / 评测明细 json+txt" | tee -a "$LOG"
grep -aE "完成 \|" "$RESULT_DIR/eval.log" | tail -3 | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo ">>> 记得把结果追加到 docs/记忆系统×数据集结果矩阵.md §2 全量结果表" | tee -a "$LOG"
