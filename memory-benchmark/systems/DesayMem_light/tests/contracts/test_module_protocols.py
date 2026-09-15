from desaymem_light.contracts.modules import TopicSegmenter


class FakeTopicSegmenter:
    async def segment(self, request):
        return request


class MissingSegmentMethod:
    pass


def test_protocol_accepts_structural_plugin() -> None:
    assert isinstance(FakeTopicSegmenter(), TopicSegmenter)


def test_protocol_rejects_incomplete_plugin() -> None:
    assert not isinstance(MissingSegmentMethod(), TopicSegmenter)
