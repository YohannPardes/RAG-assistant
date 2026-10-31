"""The configured embedding model, served by a local Ollama (skipped without one)."""

import math

import pytest

from siteops_rag.config import EmbeddingSettings
from siteops_rag.embedder.openai_compat import OpenAICompatEmbedder

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def embedder(embedding_settings: EmbeddingSettings) -> OpenAICompatEmbedder:
    s = embedding_settings
    return OpenAICompatEmbedder(base_url=s.base_url, embedding_model=s.model, dimension=s.dimension, batch_size=2, timeout=s.timeout)


def cosine(a: list[float], b: list[float]) -> float:
    return math.sumprod(a, b) / (math.hypot(*a) * math.hypot(*b))


def test_vectors_have_the_configured_dimension(embedder, embedding_settings) -> None:
    # the Qdrant collection is created with this size: a mismatch would fail every upsert
    assert len(embedder.embed("disk full on /var/log")) == embedding_settings.dimension


def test_batched_vectors_match_the_texts_they_were_sent_with(embedder) -> None:
    texts = ["disk full on /var/log", "renew the TLS certificate", "restart the api pods", "promote the replica", "rotate the logs"]

    batched = embedder.embed_batch(texts).vectors  # batch_size=2: three requests

    for text, vector in zip(texts, batched, strict=True):
        assert cosine(vector, embedder.embed(text)) == pytest.approx(1.0, abs=1e-3)


def test_a_paraphrase_is_closer_than_an_unrelated_runbook_step(embedder) -> None:
    query = embedder.embed("the disk is full, how do I free space?")

    paraphrase = embedder.embed("Find what uses space on the partition with du and remove old log files.")
    unrelated = embedder.embed("Renew the TLS certificate with cert-manager before it expires.")

    assert cosine(query, paraphrase) > cosine(query, unrelated)
