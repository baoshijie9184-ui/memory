#!/usr/bin/env bash
# 记忆系统全量评测脚本（修正版）。原 run_full.sh 保留不动，便于对照和回退。
#
# 用法示例：
#   MEMORY_SYSTEM=mem0 DATASET=locomo MODEL_CONFIG=memory-llm \
#     bash scripts/run_full_fixed.sh
#
# 支持的记忆系统：none / mem0 / structmem / structmem-nosummary
# 支持的数据集：locomo / carmem / longmemeval / vehiclemembench / desaymem
#
# 整体流程：
#   1. 读取 config/systems/{系统}.env，选择该系统的 Python 环境；
#   2. 按 --full 的真实数据配置生成样本和 collection 计划；
#   3. StructMem 系统先逐样本建库并检查库完整性，Mem0/none 跳过此阶段；
#   4. 执行全量评测，把日志和报告直接写入本次唯一结果目录。
#
# 运行环境：Linux Bash；需要 flock（util-linux）以及相应的 Python 环境。
set -Eeuo pipefail

# -------------------- 用户可通过环境变量覆盖的配置 --------------------
MEMORY_SYSTEM="${MEMORY_SYSTEM:-structmem}"
DATASET="${DATASET:-locomo}"
MODEL_CONFIG="${MODEL_CONFIG:-memory-llm}"
BENCH_ROOT="${BENCH_ROOT:-/data/pengshuang/memory-benchmark}"
EVAL_ROOT="$BENCH_ROOT/datasets/VehicleMem-Eval"
GPU_ID="${GPU_ID:-}"

# 尽早校验输入，避免运行数小时后才发现名称或目录写错。
die() { echo "ERROR: $*" >&2; exit 1; }
case "$MEMORY_SYSTEM" in none|mem0|structmem|structmem-nosummary) ;; *) die "Unsupported system: $MEMORY_SYSTEM";; esac
case "$DATASET" in locomo|carmem|longmemeval|vehiclemembench|desaymem) ;; *) die "Unsupported dataset: $DATASET (run datasets individually)";; esac
[[ "$MODEL_CONFIG" =~ ^[a-zA-Z0-9_.-]+$ ]] || die "Invalid model configuration name"
[[ -f "$EVAL_ROOT/run.py" ]] || die "Missing $EVAL_ROOT/run.py"

# -------------------- 加载系统专属环境 --------------------
# 必须先 source 系统配置，再填充 Python 默认值；否则系统专属环境可能被静默绕过。
SYSTEM_ENV="$EVAL_ROOT/config/systems/$MEMORY_SYSTEM.env"
# 两种 StructMem 模式共用同一套 LightMem 环境配置。
if [[ ! -f "$SYSTEM_ENV" && "$MEMORY_SYSTEM" == structmem-nosummary ]]; then
  SYSTEM_ENV="$EVAL_ROOT/config/systems/structmem.env"
fi
if [[ -f "$SYSTEM_ENV" ]]; then
  # set -a 让 .env 中赋值的变量自动 export 给后续 Python 子进程。
  set -a
  source "$SYSTEM_ENV"
  set +a
  echo "System config: $SYSTEM_ENV"
elif [[ "$MEMORY_SYSTEM" != none ]]; then
  die "Missing system configuration: $SYSTEM_ENV"
fi
PY_EVAL="${PY_EVAL:-$BENCH_ROOT/envs/eval/bin/python}"
PY_BUILD="${PY_BUILD:-$BENCH_ROOT/envs/lightmem/bin/python}"
[[ -x "$PY_EVAL" ]] || die "Evaluation Python is missing: $PY_EVAL"
if [[ "$MEMORY_SYSTEM" == mem0 && "$PY_EVAL" == "$BENCH_ROOT/envs/eval/bin/python" ]]; then
  die "mem0 must use its dedicated environment; set PY_EVAL=$BENCH_ROOT/envs/mem0/bin/python in $SYSTEM_ENV"
fi
export TIKTOKEN_CACHE_DIR="${TIKTOKEN_CACHE_DIR:-$BENCH_ROOT/tmp/tiktoken_cache}"
export PYTHONPATH="$EVAL_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# -------------------- 锁和结果目录 --------------------
# 建库程序和 bridge 会访问共享的本地 Qdrant 目录，禁止两个全量任务并行写入。
command -v flock >/dev/null || die "Install flock (util-linux) first"
mkdir -p "$BENCH_ROOT/results/raw" "$BENCH_ROOT/tmp"
exec 9>"$BENCH_ROOT/tmp/run_full_fixed.lock"
flock -n 9 || die "Another run_full_fixed.sh is using the shared stores"
# mktemp 的随机后缀保证同一秒启动的任务也不会覆盖彼此结果。
RESULT_DIR=$(mktemp -d "$BENCH_ROOT/results/raw/$(date +%Y%m%d_%H%M%S)_${DATASET}_${MEMORY_SYSTEM}_${MODEL_CONFIG}_XXXXXX")
# 从这里开始，终端输出同时写入 run.log；eval/build 的详细输出另存专用日志。
exec > >(tee -a "$RESULT_DIR/run.log") 2>&1
# 任意未处理错误都会显示退出码、出错行和本次结果目录。
trap 'rc=$?; echo "FAILED: exit=$rc line=$LINENO; results: $RESULT_DIR"; exit "$rc"' ERR
echo "Run: $DATASET / $MEMORY_SYSTEM / $MODEL_CONFIG"
echo "Python: $PY_EVAL"
echo "Results: $RESULT_DIR"
cd "$EVAL_ROOT"

# -------------------- 生成全量执行计划 --------------------
# 直接复用评测引擎的 _apply_full_mode 和 adapter，避免手写 50/100/500 等数量后失配。
# plan.json 记录真实样本数以及预期 collection 名称，后续建库和验库都以它为准。
"$PY_EVAL" - "$DATASET" "$MODEL_CONFIG" "$RESULT_DIR/plan.json" <<'PY'
import json, sys
from pathlib import Path
import yaml
from evalcore.eval_engine import _load_adapter, _apply_full_mode
from evalcore.model_registry import load_model_config
dataset, model, output = sys.argv[1:]
load_model_config(model)
# 使用与 run.py 完全相同的全量数据配置和加载逻辑。
cfg = yaml.safe_load(Path('config/datasets.yaml').read_text(encoding='utf-8'))
adapter = _load_adapter(dataset, _apply_full_mode(cfg['datasets'][dataset]))
data = adapter.load_data()
if not data:
    raise RuntimeError('Dataset is empty')
plan = {'count': len(data), 'collections': []}
# desaymem 一个样本内含多个身份 scope，每个 scope 独立建 collection。
if dataset == 'desaymem':
    plan['collections'] = [f'desaymem_{uid}' for item in data for uid in item['scopes']]
elif dataset != 'locomo':
    plan['collections'] = [f'{dataset}_user_{i}' for i in range(len(data))]
Path(output).write_text(json.dumps(plan), encoding='utf-8')
print(f'Full dataset: {len(data)} samples')
PY

# -------------------- StructMem/LightMem 离线建库 --------------------
# Mem0 会在评测阶段逐轮 add，none 没有记忆库，因此只有 structmem* 进入本段。
if [[ "$MEMORY_SYSTEM" == structmem* ]]; then
  # LoCoMo 使用已有全量库；其余数据集使用新数据集专用库根目录。
  if [[ "$DATASET" == locomo ]]; then
    LIB_ROOT="$BENCH_ROOT/data/lightmem/qdrant_post_update"
    LIB_PREFIX=""
  else
    LIB_ROOT="$BENCH_ROOT/data/lightmem/qdrant_new_datasets/qdrant_post_update"
    LIB_PREFIX="${DATASET}_"
  fi
  # 如果 structmem.env 定义了路径函数，以系统配置为准。
  if declare -F DS_LIB_ROOT >/dev/null; then LIB_ROOT=$(DS_LIB_ROOT "$DATASET"); fi
  if declare -F DS_COLLECTION_PREFIX >/dev/null; then LIB_PREFIX=$(DS_COLLECTION_PREFIX "$DATASET"); fi

  if [[ "$DATASET" != locomo ]]; then
    # 当前 build_lightmem_lib.py 内部仍有服务器绝对路径，因此先明确限制工作区位置。
    [[ -x "$PY_BUILD" ]] || die "Build Python is missing: $PY_BUILD"
    [[ "$BENCH_ROOT" == /data/pengshuang/memory-benchmark ]] || die "Current builder contains fixed paths; this build requires /data/pengshuang/memory-benchmark"
    [[ "$LIB_ROOT" == "$BENCH_ROOT/data/lightmem/qdrant_new_datasets/qdrant_post_update" && "$LIB_PREFIX" == "${DATASET}_" ]] || die "Build output path/prefix cannot be overridden by the current builder"
    # desaymem 建库函数目前没有 summary 阶段，不能冒充完整 structmem 给出分数。
    [[ "$DATASET" != desaymem || "$MEMORY_SYSTEM" != structmem ]] || die "The desaymem builder has no summary implementation; use structmem-nosummary or implement summary building first"
    BUILD_SCRIPT="${BUILD_SCRIPT:-$BENCH_ROOT/systems/LightMem/experiments/locomo/build_lightmem_lib.py}"
    [[ -f "$BUILD_SCRIPT" ]] || die "Missing build script: $BUILD_SCRIPT"
    if [[ -z "$GPU_ID" ]]; then
      # 未手动指定时，选择当前剩余显存最多的 GPU。
      GPU_ID=$(nvidia-smi --query-gpu=index,memory.free --format=csv,noheader,nounits | sort -t, -k2 -nr | awk 'NR==1 {gsub(/ /,"",$1); split($1,a,","); print a[1]}')
    fi
    [[ -n "$GPU_ID" ]] || die "No GPU found; set GPU_ID explicitly"
    echo "Building with GPU $GPU_ID; log: $RESULT_DIR/build_lib.log"
    # 只有本脚本成功写出的 marker 才表示样本已完整建库。
    # 遇到来源不明或可能只建了一半的旧目录时停止，不静默复用，也不自动删除用户数据。
    CUDA_VISIBLE_DEVICES="$GPU_ID" "$PY_BUILD" -u - "$BUILD_SCRIPT" "$DATASET" "$MEMORY_SYSTEM" "$RESULT_DIR/plan.json" "$LIB_ROOT" <<'PY' >"$RESULT_DIR/build_lib.log" 2>&1
import hashlib, json, subprocess, sys
from pathlib import Path
script, dataset, system, plan_file, root = sys.argv[1:]
plan = json.loads(Path(plan_file).read_text())
root = Path(root)
summary = system == 'structmem'
# marker 同时绑定建库脚本内容和是否启用 summary；代码或模式变化后必须重新建库。
signature = hashlib.sha256(Path(script).read_bytes()).hexdigest() + f':summary={summary}'
for index in range(plan['count']):
    names = plan['collections'] if dataset == 'desaymem' else [plan['collections'][index]]
    marker = root / f'.full-fixed-{dataset}-{index}-{system}.json'
    if marker.exists() and json.loads(marker.read_text()) == signature:
        print(f'Skipping completed sample {index}', flush=True)
        continue
    if any((root / name).exists() or (root.parent / 'qdrant_pre_update' / name).exists() for name in names):
        raise RuntimeError(f'Unverified existing sample {index}: {names}. Back up/move its pre/post libraries before rebuilding; no data was deleted.')
    # 普通数据集必须显式逐个传 sample-idx；原脚本漏掉循环，只会建立 user_0。
    args = [sys.executable, '-B', '-u', script, '--dataset', dataset,
            '--sample-idx', str(index), '--turn-limit', '0', '--extraction-mode', 'event']
    if summary:
        args.append('--enable-summary')
    subprocess.run(args, check=True)
    if summary:
        # 建库器把摘要生成在 pre_update；整个样本成功后才复制到评测读取的 post_update。
        import shutil
        for name in names:
            source = root.parent / 'qdrant_pre_update' / f'{name}_summary'
            target = root / f'{name}_summary'
            if source.exists():
                shutil.copytree(source, target, dirs_exist_ok=True)
    marker.write_text(json.dumps(signature))
PY
  fi

  # -------------------- 离线库完整性检查 --------------------
  # bridge 对缺失用户存在回退到首个 collection 的逻辑；不先验库会造成跨用户错误召回。
  # 这里要求每个预期 SQLite 存在且至少有一条 point，摘要模式还要检查 summary 库。
  "$PY_EVAL" - "$RESULT_DIR/plan.json" "$LIB_ROOT" "$DATASET" "$MEMORY_SYSTEM" <<'PY'
import json, sqlite3, sys
from pathlib import Path
plan_file, root, dataset, system = sys.argv[1:]
plan = json.loads(Path(plan_file).read_text())
root = Path(root)
names = plan['collections']
if dataset == 'locomo':
    # LoCoMo collection 名是 conv-*，不能从 user_0 规则推导，只检查数量和内容。
    names = sorted(p.name for p in root.glob('conv-*') if p.is_dir() and not p.name.endswith('_summary'))
    if len(names) != plan['count']:
        raise RuntimeError('LoCoMo library count differs from dataset; build/verify the LoCoMo libraries first')
if system == 'structmem':
    names = names + [f'{name}_summary' for name in names]
for name in names:
    db = root / name / 'collection' / name / 'storage.sqlite'
    # mode=ro 防止“检查”动作意外创建一个新的空 SQLite 文件。
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        count = conn.execute('select count(*) from points').fetchone()[0]
    if count == 0:
        raise RuntimeError(f'Empty collection: {name}; refusing misleading full evaluation')
print(f'Validated {len(names)} collections')
PY
  export LIGHTMEM_LIB_ROOT="$LIB_ROOT"
  export LIGHTMEM_COLLECTION_PREFIX="$LIB_PREFIX"
fi

# -------------------- 正式评测和报告校验 --------------------
# --output-dir 让本轮报告直接写进 RESULT_DIR，不再从公共 results 中猜“最新文件”。
echo "Evaluation started; log: $RESULT_DIR/eval.log"
if "$PY_EVAL" -B -u run.py --dataset "$DATASET" --memory-system "$MEMORY_SYSTEM" \
    --model-config "$MODEL_CONFIG" --full --output-dir "$RESULT_DIR" >"$RESULT_DIR/eval.log" 2>&1; then
  shopt -s nullglob
  # Reporter 使用 models.yaml 内解析后的 model 名，可能与 MODEL_CONFIG 的 key 不同。
  # 即使 Python 返回 0，也必须确认实际生成了本系统的 JSON 报告。
  reports=("$RESULT_DIR/${DATASET}_"*"_${MEMORY_SYSTEM}_"*.json)
  ((${#reports[@]} > 0)) || die "Evaluation returned zero but produced no report"
  echo "SUCCESS: $RESULT_DIR"
else
  rc=$?
  echo "Evaluation failed (exit=$rc). See $RESULT_DIR/eval.log"
  exit "$rc"
fi
