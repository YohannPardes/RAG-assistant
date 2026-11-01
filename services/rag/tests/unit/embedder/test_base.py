import pytest

from siteops_rag.embedder.base import Embedder, EmbeddingError, Vector
from siteops_rag.embedder.fake import FakeEmbedder


class _StubEmbedder(Embedder):
    """Backend that returns a fixed (possibly wrong) answer, to test the base class's checks."""

    def __init__(self, answer: list[Vector]) -> None:
        super().__init__(dimension=2, batch_size=32)
        self.answer = answer

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        return self.answer


def test_splits_into_backend_calls_of_batch_size():
    embedder = FakeEmbedder(dimension=8, batch_size=2)

    embeddings = embedder.embed_batch(["a", "b", "c", "d", "e"])

    assert embedder.calls == [["a", "b"], ["c", "d"], ["e"]]
    assert len(embeddings.vectors) == 5
    assert embedder.embed_batch([]).vectors == []
    assert len(embedder.calls) == 3  # the empty batch made no backend call


@pytest.mark.parametrize(
    "texts",
    [[""], ["   "], ["ok", "\n"]],
    ids=["empty", "spaces", "one-blank-among-valid"],
)
def test_blank_text_is_rejected_before_any_backend_call(texts):
    embedder = FakeEmbedder()

    with pytest.raises(ValueError):
        embedder.embed_batch(texts)
    assert embedder.calls == []


@pytest.mark.parametrize(
    "answer",
    [[], [[0.0, 0.0], [0.0, 0.0]], [[0.0, 0.0, 0.0]]],
    ids=["too-few-vectors", "too-many-vectors", "wrong-dimension"],
)
def test_wrong_backend_output_raises_embedding_error(answer):
    with pytest.raises(EmbeddingError):
        _StubEmbedder(answer).embed("disk full")


@pytest.mark.parametrize("kwargs", [{"dimension": 0}, {"batch_size": 0}], ids=["dimension", "batch-size"])
def test_non_positive_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        FakeEmbedder(**kwargs)
