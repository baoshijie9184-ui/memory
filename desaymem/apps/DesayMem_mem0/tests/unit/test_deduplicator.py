from desaymem.extraction.deduplicator import drop_duplicate_texts, memory_content_hash


def test_hash_matches_mem0_md5():
    text = "用户开车时喜欢把空调调到22度"
    assert memory_content_hash(text) == __import__("hashlib").md5(text.encode()).hexdigest()


def test_drop_existing_and_in_batch_duplicates():
    items = [
        {"text": "用户开车时喜欢把空调调到22度"},
        {"text": "用户开车时喜欢把空调调到22度"},
        {"text": "用户喜欢导航播报简洁模式"},
    ]
    existing = {memory_content_hash("用户开车时喜欢把空调调到22度")}
    kept, skipped = drop_duplicate_texts(items, existing)
    assert len(kept) == 1
    assert kept[0]["text"] == "用户喜欢导航播报简洁模式"
    assert skipped == 2
    assert "content_hash" in kept[0]
