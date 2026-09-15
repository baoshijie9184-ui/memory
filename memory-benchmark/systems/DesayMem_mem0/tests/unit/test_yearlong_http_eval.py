"""Unit tests for yearlong HTTP eval metric helpers."""

from scripts.run_yearlong_http_test import CASES, build_store_inventory, evaluate_case


def test_store_inventory_counts_topics():
    memories = [
        {"id": "1", "content": "用户去了童梦森林亲子餐厅", "scene": "family_dining", "memory_type": "semantic_memory", "metadata": {}},
        {"id": "2", "content": "用户少油、不要香菜", "scene": "dining_preference", "memory_type": "semantic_memory", "metadata": {}},
        {"id": "3", "content": "收藏童趣岛家庭餐厅", "scene": "recommendation_feedback", "memory_type": "semantic_memory", "metadata": {}},
    ]
    inventory = build_store_inventory(memories)
    assert inventory["total"] == 3
    by_id = {topic["id"]: topic["count"] for topic in inventory["topics"]}
    assert by_id["tongmeng"] == 1
    assert by_id["diet_pref"] == 1
    assert by_id["tongqudao"] == 1


def test_evaluate_case_contrasts_written_expected_actual():
    case = next(item for item in CASES if item["id"] == "fuzzy_01")
    store = [
        {"id": "a", "content": "多次前往童梦森林亲子餐厅", "scene": "family_dining", "memory_type": "semantic_memory", "metadata": {}},
        {"id": "b", "content": "收藏童趣岛一次", "scene": "recommendation_feedback", "memory_type": "semantic_memory", "metadata": {}},
        {"id": "c", "content": "去了麦田亲子餐厅", "scene": "family_dining", "memory_type": "semantic_memory", "metadata": {}},
    ]
    hits = [
        {"id": "c", "content": "去了麦田亲子餐厅", "score": 0.9, "memory_type": "semantic_memory", "scene": "family_dining", "metadata": {}},
        {"id": "a", "content": "多次前往童梦森林亲子餐厅", "score": 0.8, "memory_type": "semantic_memory", "scene": "family_dining", "metadata": {}},
    ]
    result = evaluate_case(case, hits, {}, store)
    metrics = result["metrics"]
    assert metrics["must_facts_hit"] == 1
    assert metrics["must_fact_recall"] == 1.0
    assert metrics["first_relevant_rank"] == 2
    assert metrics["mrr"] == 0.5
    assert metrics["top1_expected"] is False
    assert metrics["precision_at_k"] == 0.5
    assert result["expected"]["must"][0]["written_count"] == 1
    assert result["expected"]["must"][0]["first_rank"] == 2
    assert result["actual_hits"][0]["labels"] == ["NOISE"]
    assert "MUST" in result["actual_hits"][1]["labels"]
