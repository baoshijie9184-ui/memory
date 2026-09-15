"""
Offline token-F1 evaluator for LightMem LoCoMo search results.

Reads sample_*.json produced by search_locomo.py (which contain
prediction / reference / category / metrics per question) and computes
token-level F1 per category, alongside the LLM-judge accuracy already
recorded, so the two metrics can be cross-checked.

Usage:
    python eval_f1.py --results-dir <search_output_dir> [--output <json path>]
"""
import argparse
import glob
import json
import os
import re
import statistics
from collections import defaultdict

# ---- official-style tokenization (same as MemoryOS eval / snap-research eval) ----
def simple_tokenize(text):
    if text is None:
        return set()
    return set(re.findall(r"\b\w+\b", str(text).lower()))

def clean_prediction(prediction):
    """
    Strip reasoning-preamble pollution from raw model output.

    The local vLLM service (memory-llm / Qwen3-family) emits
    '```think ...```' style reasoning before the final answer when no
    reasoning parser is enabled server-side. Extract the actual answer:
    1. drop everything up to and including the closing '```' of a think block
    2. otherwise fall back to the last non-empty line (answer prompts ask for
       a short 5-6 word answer on its own line)
    """
    text = str(prediction or "").strip()
    if "```" in text:
        parts = text.split("```")
        # keep the last segment after the final code fence
        tail = parts[-1].strip()
        if tail:
            return tail
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) > 1:
        return lines[-1]
    return text

def calculate_f1(prediction, reference):
    pred_tokens = simple_tokenize(prediction)
    ref_tokens = simple_tokenize(reference)
    common = pred_tokens & ref_tokens
    precision = len(common) / len(pred_tokens) if pred_tokens else 0.0
    recall = len(common) / len(ref_tokens) if ref_tokens else 0.0
    if precision + recall > 0:
        return 2 * precision * recall / (precision + recall)
    return 0.0

def main():
    parser = argparse.ArgumentParser(description="Token-F1 evaluation for search_locomo results")
    parser.add_argument("--results-dir", type=str, required=True,
                        help="Directory containing sample_*.json from search_locomo.py")
    parser.add_argument("--output", type=str, default=None,
                        help="Optional path to write F1 summary JSON")
    args = parser.parse_args()

    files = sorted(glob.glob(os.path.join(args.results_dir, "sample_*.json")))
    if not files:
        raise SystemExit(f"No sample_*.json found in {args.results_dir}")

    cat_f1 = defaultdict(list)
    cat_judge = defaultdict(list)
    total_f1, total_judge = [], []
    n_q = 0

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            sample = json.load(f)
        for r in sample.get("results", []):
            # adversarial (cat5) has no reference answer -> not F1-scoreable
            if r.get("category") == 5 or not r.get("reference"):
                continue
            n_q += 1
            f1 = calculate_f1(clean_prediction(r.get("prediction")), r.get("reference"))
            judge = float(r.get("metrics", {}).get("judge_correct", 0) or 0)
            cat = int(r["category"])
            cat_f1[cat].append(f1)
            cat_judge[cat].append(judge)
            total_f1.append(f1)
            total_judge.append(judge)

    print(f"Evaluated {n_q} questions from {len(files)} samples (cat5 excluded)")
    print(f"{'cat':<6}{'n':<6}{'F1':<10}{'J-score':<10}")
    print("-" * 32)
    for cat in sorted(cat_f1):
        print(f"{cat:<6}{len(cat_f1[cat]):<6}"
              f"{statistics.mean(cat_f1[cat]):<10.4f}"
              f"{statistics.mean(cat_judge[cat]):<10.4f}")
    print("-" * 32)
    print(f"{'all':<6}{len(total_f1):<6}"
          f"{statistics.mean(total_f1):<10.4f}"
          f"{statistics.mean(total_judge):<10.4f}")

    if args.output:
        summary = {
            "total_questions": n_q,
            "overall": {
                "f1": {"mean": statistics.mean(total_f1), "std": statistics.pstdev(total_f1)},
                "judge_correct": {"mean": statistics.mean(total_judge)},
            },
            "per_category": {
                str(cat): {
                    "count": len(cat_f1[cat]),
                    "f1": {"mean": statistics.mean(cat_f1[cat]), "std": statistics.pstdev(cat_f1[cat])},
                    "judge_correct": {"mean": statistics.mean(cat_judge[cat])},
                }
                for cat in sorted(cat_f1)
            },
            "metric_definition": "token-set F1: 2*P*R/(P+R), P/R over lowercase word sets (regex \\b\\w+\\b)",
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\nSaved: {args.output}")

if __name__ == "__main__":
    main()
