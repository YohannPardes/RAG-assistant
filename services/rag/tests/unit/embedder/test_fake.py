import math

import pytest

from siteops_rag.embedder.fake import FakeEmbedder


def cosine(a: list[float], b: list[float]) -> float:
    return math.sumprod(a, b) / (math.hypot(*a) * math.hypot(*b))


def test_same_text_always_gets_the_same_vector() -> None:
    # tests compare ranked hits across runs: they must not depend on chance
    assert FakeEmbedder().embed("disk full") == FakeEmbedder().embed("disk full")


def test_vectors_have_the_configured_dimension_and_unit_length() -> None:
    vector = FakeEmbedder(dimension=16).embed("disk full on /var/log")

    assert len(vector) == 16
    assert math.hypot(*vector) == pytest.approx(1.0)


def test_texts_sharing_words_are_more_similar_than_unrelated_ones() -> None:
    # what makes the fake usable for retrieval tests: a query finds the chunk that shares its words
    embedder = FakeEmbedder()
    query = embedder.embed("disk full on var log")

    related = embedder.embed("the disk is full, clean var log")
    unrelated = embedder.embed("renew the tls certificate")

    assert cosine(query, related) > cosine(query, unrelated)


def test_model_name_is_reported() -> None:
    # stores refuse vectors from another model, so the fake must name its own
    assert FakeEmbedder().embed_batch(["x"]).model == "fake"
