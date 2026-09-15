# VehicleMem-Eval

Unified evaluation framework for **in-vehicle memory systems**, benchmarking accuracy and efficiency across four datasets: **LoCoMo**, **LongMemEval**, **CarMem**, and **VehicleMemBench**.

## Why

Existing memory benchmarks evaluate conversational QA accuracy in isolation. Vehicle memory systems face an additional constraint: they must be **accurate** *and* **efficient** enough to run on edge hardware with tight token, latency, and memory budgets. VehicleMem-Eval bridges this gap by running all four benchmarks under a single harness that simultaneously measures:

- **Accuracy** — per-dataset official scoring protocols (Token-F1, yes/no judge, tool-call F1, hierarchical extraction F1)
- **Efficiency** — token consumption, LLM call count, per-phase runtime, peak memory

This lets you compare "good and cheap" versus "accurate but expensive" memory strategies fairly.

## Architecture

```
VehicleMem-Eval/
├── run.py                          # Unified CLI entry point
├── config/
│   ├── models.yaml                 # Model configs (API keys, endpoints, embedding dims)
│   └── datasets.yaml               # Dataset paths + smoke-test sample limits
├── datasets/                       # Pre-loaded benchmark data (LFS-tracked)
│   ├── locomo/locomo10.json        # 10 conversations, ~1986 QA pairs
│   ├── longmemeval/                # Official S-cleaned, 500 questions
│   │   ├── longmemeval_s_cleaned.json   (265 MB, LFS)
│   │   └── longmemeval_single.json      (single-sample smoke test)
│   ├── carmem/dataset.jsonl        # 100 users × 10 preferences
│   └── vehiclemembench/            # 50 history + QA files
│       ├── history/history_{1..50}.txt
│       └── qa_data/qa_{1..50}.json
├── evalcore/
│   ├── eval_engine.py              # Orchestration: memory_build → retrieval → qa → judge
│   ├── llm_client.py               # OpenAI-compatible client with token/call counting
│   └── model_registry.py           # Load model config from YAML
├── adapters/                       # Per-dataset adapters (BaseAdapter interface)
│   ├── base.py
│   ├── locomo_adapter.py
│   ├── longmemeval_adapter.py
│   ├── carmem_adapter.py
│   └── vehiclemembench_adapter.py
├── metrics/
│   ├── efficiency.py               # EvalMetrics: accuracy + efficiency aggregation
│   ├── official.py                 # Official scoring per dataset (F1, judge, tool-F1, CarMem)
│   └── reporter.py                 # TXT + JSON report generation
├── results/                        # Evaluation output (git-ignored, regenerable)
└── requirements.txt
```

## Quick Start

### Prerequisites

- Python 3.10+
- [Git LFS](https://git-lfs.com/) installed (datasets are LFS-tracked)
- An OpenAI-compatible LLM API key (DeepSeek, OpenAI, Qwen, local vLLM, etc.)
- An embedding API key (required when evaluating with DesayMem memory system)

### Install

```bash
git clone https://github.com/psile/VehicleMem-Eval.git
cd VehicleMem-Eval
git lfs pull                          # download large dataset files
pip install -r requirements.txt       # openai>=1.0.0, pyyaml, numpy
```

### Configure API Keys

Edit `config/models.yaml`:

```yaml
models:
  deepseek:
    api_base: "https://api.deepseek.com/v1"
    api_key: "sk-your-key-here"       # or set env var LLM_API_KEY
    model: "deepseek-chat"
    judge_model: "deepseek-chat"
    embedding_base: "https://ark.cn-beijing.volces.com/api/v3"
    embedding_model: "doubao-embedding-vision-251215"
    embedding_key: "your-embedding-key"
    embedding_dim: 2048
```

You can add any number of OpenAI-compatible model entries. Each needs:
- `api_base` / `api_key` / `model` — for answer generation and (LongMemEval) yes/no judging
- `embedding_base` / `embedding_model` / `embedding_key` / `embedding_dim` — for DesayMem memory vectorization

### Smoke Test

```bash
# 1 sample, checks the full pipeline
python run.py --dataset locomo --memory-system desaymem --model-config deepseek --sample-limit 1
```

### Full Evaluation

```bash
# Single dataset, official full split
python run.py --dataset locomo --memory-system desaymem --model-config deepseek --full

# Baseline (no memory system; full history in prompt)
python run.py --dataset locomo --memory-system none --model-config deepseek

# All datasets
python run.py --dataset all --memory-system desaymem --model-config deepseek --full

# Switch model
python run.py --dataset carmem --memory-system desaymem --model-config gpt-4o-mini
```

### CLI Reference

| Flag | Default | Description |
|---|---|---|
| `--dataset` | `locomo` | `locomo` / `longmemeval` / `carmem` / `vehiclemembench` / `all` |
| `--memory-system` | `desaymem` | `desaymem` (alias `vehiclemem`) / `none` (baseline) |
| `--model-config` | `deepseek` | Key in `config/models.yaml` |
| `--sample-limit` | `0` | Limit sample count; `0` = all; `1` for smoke |
| `--full` | off | Ignore smoke limits in `datasets.yaml`, run official full split |
| `--output-dir` | `results/` | Report output directory |

## Datasets

### LoCoMo

10 long conversations with ~1986 QA pairs across 5 categories: multi-hop, temporal, open-domain, single-hop, adversarial.

**Official metric**: Token-level F1 (Porter-stemmed, normalized). Multi-hop uses mean-of-max F1 over comma-separated sub-answers. Adversarial scores 1.0 if the model abstains.

### LongMemEval

500 questions over long multi-session conversations. 6 question types: single-session-user, single-session-assistant, multi-session, multi-session-user, multi-session-assistant, abstention.

**Official metric**: GPT-style yes/no judge per question type. Reports micro accuracy and macro (6-type average).

### CarMem

100 users × 10 preferences. Evaluates three stages:
- **Extraction** — In-schema F1: predicted Main/Sub/Detail/Attribute vs gold
- **Retrieval** — hit@k: gold preference appears in retrieved context
- **Maintenance** — Pass/Update/Append classification + state validation (equal → Pass; negate → Update; different → Append for MP / Update for SP)

### VehicleMemBench

50 multi-user vehicle history files with vehicle-control tool-call QA. 5 reasoning types: preference_conflict, conditional_constraint, coreference_resolution, error_correction, state_shift.

**Official metric**: Tool-call precision/recall/F1 via `(name, args)` exact match. Exact match = predicted tool set equals gold tool set.

## Metrics

### Accuracy (per dataset, official protocols)

| Dataset | Metric | Scoring |
|---|---|---|
| LoCoMo | Token-F1 | Porter stem + normalize; cat1 mean-max; cat5 abstain |
| LongMemEval | Yes/no judge | Official English prompt; abstention template for `_abs` |
| CarMem | Extraction F1 / Retrieval hit / Maintenance action | Hierarchical match; context hit; Pass/Update/Append |
| VehicleMemBench | Tool-call P/R/F1 | `(name, json.dumps(args))` set match |

### Efficiency

| Metric | Phase | Meaning |
|---|---|---|
| Token In | all | Input tokens — reflects memory retrieval context size |
| Token Out | all | Output tokens — generation length |
| Total Tokens | all | Input + output — direct API cost |
| LLM Calls | all | Request count — latency and rate-limit impact |
| Runtime (s) | per-phase | Wall-clock time per stage |
| Peak Memory (MB) | global | `tracemalloc` peak — edge deployment footprint |

**Phases**: `memory_build` (write conversations to memory) → `retrieval` (vector search + ranking) → `qa` (generate answer) → `judge` (LongMemEval only; others use rule-based scoring).

### Output

Each run produces two files in `results/` (or `--output-dir`):

- `{dataset}_{model}_{memory-system}_{timestamp}.txt` — Human-readable report
- `{dataset}_{model}_{memory-system}_{timestamp}.json` — Structured JSON for programmatic analysis

Example:
```
── Accuracy ──
Overall: 53.50%  (107/200)
  adversarial          66.67%  (40/60)
  multi_hop            22.22%  (10/45)
  ...

── Efficiency ──
Phase             Token In  Token Out    Total   Calls  Runtime
memory_build      124,000     31,000  155,000     800   450.0s
retrieval          32,000          0   32,000     800   120.0s
qa                180,000     41,000  221,000     200   300.0s
judge              65,000      2,200   67,200     200   115.0s
────────────────────────────────────────────────────────────────
Total             401,000     74,200  475,200   2,000   985.0s
Peak Memory                                                  284.6 MB
```

## Extend

### Add a Dataset

1. Create `adapters/myds_adapter.py` inheriting `BaseAdapter`
2. Implement `load_data()`, `build_history()`, `get_queries()`, `evaluate_answer()`
3. Register in `evalcore/eval_engine.py` `ADAPTERS` dict
4. Add path config in `config/datasets.yaml`

### Add a Model

Add a new entry to `config/models.yaml` under `models:`. Any OpenAI-compatible endpoint works (DeepSeek, OpenAI, Qwen, local vLLM, Ollama).

## Troubleshooting

| Problem | Fix |
|---|---|
| `402 Payment Required` | LLM API key has no balance — top up or switch key |
| Embedding 400 `InvalidParameter` | Multimodal embeddings (e.g. `doubao-embedding-vision-*`) must use `/embeddings/multimodal`; standard models use `/embeddings`. Ensure `embedding_model` matches `embedding_base` |
| Dimension mismatch | `embedding_dim` must match model output (e.g. `text-embedding-3-small`=1536, `text-embedding-v3`=1024) |
| Judge unstable | LoCoMo / VehicleMemBench use rule-based scoring (no LLM judge). LongMemEval uses official yes/no — try a stronger `judge_model` |
| LFS files missing | Run `git lfs pull` after cloning |

## License

Research evaluation tooling. Dataset licenses follow their respective sources:
- LoCoMo: [snap-research/locomo](https://github.com/snap-research/locomo)
- LongMemEval: [xiaowu0162/LongMemEval](https://github.com/xiaowu0162/LongMemEval)
- CarMem: [COLING 2025 / arxiv 2501.09645](https://arxiv.org/abs/2501.09645)
- VehicleMemBench: per its release terms
