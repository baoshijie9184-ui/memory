"""
报告生成 — 输出官方主指标 + 效率汇总表
"""

import json
import os
from datetime import datetime
from metrics.efficiency import EvalMetrics


_OFFICIAL = {
    "locomo": "LoCoMo Token-F1 (official eval_question_answering)",
    "longmemeval": "LongMemEval Accuracy (official yes/no judge)",
    "vehiclemembench": "VehicleMemBench Tool F1 / Exact Match (score_tool_calls proxy)",
    "carmem": "CarMem Extraction In-Schema / Retrieval Hit / Maintenance Action",
}


def generate_report(metrics: EvalMetrics, dataset: str, model: str,
                    memory_system: str, output_dir: str = None) -> str:
    """生成文本报告 + JSON"""
    lines = []
    lines.append("=" * 70)
    lines.append("DesayMem-Eval 评测报告")
    lines.append(f"数据集: {dataset}  |  模型: {model}  |  记忆系统: {memory_system}")
    lines.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    lines.append("")
    lines.append("── 官方指标 ──")
    lines.append(f"协议: {_OFFICIAL.get(dataset, metrics.primary_metric)}")

    if dataset == "locomo":
        lines.append(f"Token-F1 (主指标): {metrics.mean_score:.2%}  (n={metrics.total})")
        lines.append(f"辅助二值 (F1>=0.5 / adversarial exact): {metrics.accuracy:.2%}  ({metrics.correct}/{metrics.total})")
    elif dataset == "longmemeval":
        lines.append(f"Overall Accuracy (micro): {metrics.accuracy:.2%}  ({metrics.correct}/{metrics.total})")
        lines.append(f"Task-averaged Accuracy (macro of types): {metrics.macro_accuracy():.2%}")
    elif dataset == "vehiclemembench":
        lines.append(f"Tool F1 (主指标): {metrics.mean_score:.2%}")
        lines.append(f"Exact Match (工具集合完全一致): {metrics.accuracy:.2%}  ({metrics.correct}/{metrics.total})")
        if metrics.extras_count.get("precision"):
            lines.append(f"Tool Precision / Recall: {metrics.extra_mean('precision'):.2%} / {metrics.extra_mean('recall'):.2%}")
        lines.append("说明: 完整官方指标是 VehicleWorld 终态 exact match; 此处用 new_answer 工具集合 F1 作为不拉仿真器的对接。")
    elif dataset == "carmem":
        ext = metrics.by_category.get("extraction")
        retr = metrics.by_category.get("retrieval")
        lines.append(f"Extraction In-Schema Acc: {metrics.get_category_acc('extraction'):.2%}"
                     + (f"  ({ext['correct']}/{ext['total']})" if ext else ""))
        lines.append(f"Extraction hierarchy mean (Main/Sub/Detail): {metrics.get_category_score('extraction'):.2%}")
        lines.append(f"Retrieval Hit@k proxy: {metrics.get_category_acc('retrieval'):.2%}"
                     + (f"  ({retr['correct']}/{retr['total']})" if retr else ""))
        maint_cats = [k for k in metrics.by_category if k.startswith("maintenance_")]
        if maint_cats:
            lines.append("Maintenance Action Acc (Pass/Update/Append):")
            for cat in sorted(maint_cats):
                c = metrics.by_category[cat]
                lines.append(f"    {cat:28s} {metrics.get_category_acc(cat):.2%}  ({c['correct']}/{c['total']})")
        if metrics.extras_count.get("state_ok"):
            lines.append(f"Maintenance state-after-update (辅助): {metrics.extra_mean('state_ok'):.2%}")
        if metrics.extras_count.get("attr_correct"):
            lines.append(f"Extraction attribute match (辅助): {metrics.extra_mean('attr_correct'):.2%}")
    else:
        lines.append(f"总准确率: {metrics.accuracy:.2%}  ({metrics.correct}/{metrics.total})")
        lines.append(f"均分: {metrics.mean_score:.2%}")

    if metrics.by_category:
        lines.append("")
        lines.append("  按类别:")
        for cat in sorted(metrics.by_category.keys()):
            acc = metrics.get_category_acc(cat)
            score = metrics.get_category_score(cat)
            c = metrics.by_category[cat]
            if dataset == "locomo":
                lines.append(f"    {cat:30s} F1={score:.2%}  bin={acc:.2%}  ({c['correct']}/{c['total']})")
            elif dataset == "vehiclemembench":
                lines.append(f"    {cat:30s} F1={score:.2%}  EM={acc:.2%}  ({c['correct']}/{c['total']})")
            else:
                lines.append(f"    {cat:30s} {acc:.2%}  ({c['correct']}/{c['total']})")

    lines.append("")
    lines.append("── 效率 ──")

    if metrics.by_phase:
        lines.append(f"{'阶段':<16} {'Token In':>10} {'Token Out':>10} {'Total':>10} {'Calls':>8} {'Runtime':>10}")
        lines.append("-" * 70)
        for phase in sorted(metrics.by_phase.keys()):
            s = metrics.by_phase[phase]
            lines.append(
                f"{phase:<16} {s['prompt_tokens']:>10,} {s['completion_tokens']:>10,} "
                f"{s['total_tokens']:>10,} {s['calls']:>8} {s['runtime']:>9.1f}s"
            )
        lines.append("-" * 70)
        lines.append(
            f"{'Total':<16} {metrics.total_prompt_tokens:>10,} {metrics.total_completion_tokens:>10,} "
            f"{metrics.total_tokens:>10,} {metrics.total_calls:>8} {metrics.total_runtime:>9.1f}s"
        )

    lines.append(f"{'内存峰值':<16} {metrics.peak_memory_mb:.1f} MB")

    # 记忆系统行为统计（mem0 等"评测即建库"系统的增删改分布）
    beh = (metrics.raw_info or {}).get("memory_behavior")
    if beh:
        lines.append("")
        lines.append("── 记忆行为 ──")
        events = beh.get("memory_events") or {}
        if events:
            ev_txt = "  ".join(f"{k}={v}" for k, v in sorted(events.items()))
            lines.append(f"事件分布: {ev_txt}")
        if "delete_ratio" in beh:
            lines.append(f"删除占比 (DELETE/(ADD+DELETE)): {beh['delete_ratio']:.2%}")

    lines.append("")
    lines.append("=" * 70)

    report_text = "\n".join(lines)
    print(report_text)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"{dataset}_{model.replace('/', '_')}_{memory_system}_{ts}"
        with open(os.path.join(output_dir, f"{prefix}.txt"), "w", encoding="utf-8") as f:
            f.write(report_text)
        with open(os.path.join(output_dir, f"{prefix}.json"), "w", encoding="utf-8") as f:
            json.dump(metrics.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到 {output_dir}/{prefix}.*")

    return report_text
