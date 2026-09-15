from desaymem.retrieval.scoring import cosine_similarity, distance_to_score


def test_cosine_identical_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_distance_to_score_matches_mem0_pgvector():
    assert distance_to_score(0.2) == 0.8
    assert distance_to_score(2.0) == 0.0
