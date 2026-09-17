"""
DesayMem-Eval 统一入口

用法:
  # 评测 DesayMem 在 Locomo 上 (deepseek 模型)
  python run.py --dataset locomo --memory-system desaymem --model-config deepseek

  # baseline (不用记忆系统)
  python run.py --dataset locomo --memory-system none --model-config deepseek

  # 冒烟测试 (只跑1个样本)
  python run.py --dataset locomo --memory-system desaymem --model-config deepseek --sample-limit 1

  # 官方全集 (去掉 yaml 冒烟限制)
  python run.py --dataset locomo --memory-system desaymem --model-config deepseek --full

  # 评测所有数据集
  python run.py --dataset all --memory-system desaymem --model-config deepseek
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evalcore import eval_engine as _eval_engine
from evalcore.memory_bridges_fixed import (
    MEMORY_SYSTEMS,
    build_memory_system as _build_fixed_memory_system,
)

# Keep the normal runner untouched: only this process uses the isolated bridge.
_eval_engine._build_from_registry = _build_fixed_memory_system
run_evaluation = _eval_engine.run_evaluation

DATASETS = ["locomo", "longmemeval", "carmem", "vehiclemembench", "desaymem"]


def main():
    parser = argparse.ArgumentParser(description="DesayMem-Eval 统一记忆评测")
    parser.add_argument("--dataset", type=str, default="locomo",
                        choices=DATASETS + ["all"],
                        help="评测数据集 (默认 locomo)")
    parser.add_argument("--memory-system", type=str, default="desaymem",
                        choices=MEMORY_SYSTEMS,
                        help="记忆系统 (注册表可选: none/mem0/...; vehiclemem=desaymem同义)")
    parser.add_argument("--model-config", type=str, default="deepseek",
                        help="模型配置名 (对应 config/models.yaml 中的 key)")
    parser.add_argument("--sample-limit", type=int, default=0,
                        help="限制样本数 (0=全部, 冒烟测试用1)")
    parser.add_argument("--build-limit", type=int, default=0,
                        help="冒烟提速: 每样本只 ingest 前 N 条对话轮次 (0=全部, 仅影响 memory_build)")
    parser.add_argument("--full", action="store_true",
                        help="忽略 yaml 冒烟限制, 按官方全集评测 (qa_limit/user_limit=0, file_range=1-50)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="结果输出目录 (默认 results/)")
    args = parser.parse_args()

    datasets = [args.dataset] if args.dataset != "all" else DATASETS

    for ds in datasets:
        print(f"\n{'#'*60}")
        print(f"# 评测数据集: {ds}")
        print(f"{'#'*60}")
        run_evaluation(
            dataset=ds,
            memory_system=args.memory_system,
            model_config_name=args.model_config,
            sample_limit=args.sample_limit,
            output_dir=args.output_dir,
            full=args.full,
        build_limit=args.build_limit,
        )


if __name__ == "__main__":
    main()
