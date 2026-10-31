"""The configured LLM, served by a local Ollama (skipped without one). Its text is not deterministic:
these tests check the shape of what it returns, which rag.py relies on."""

import pytest

from siteops_rag.config import LLMSettings
from siteops_rag.llm.base import LLMError
from siteops_rag.llm.openai_compat import OpenAICompatChatModel
from siteops_rag.models import Chunk
from siteops_rag.rag import RAG, SYSTEM_PROMPT
from siteops_rag.vector_store.base import ScoredChunk

pytestmark = pytest.mark.integration

EXCERPT = ScoredChunk(
    chunk=Chunk("c1", "runbook_disk_full.pdf", "Disk full > Fix\n\nRun du -sh /var/log/* | sort -h to find the largest files.", ("Disk full",), 2),
    score=0.9,
)


def make(settings: LLMSettings, **overrides: object) -> OpenAICompatChatModel:
    s = settings.model_copy(update=overrides)
    return OpenAICompatChatModel(
        base_url=s.base_url, model=s.model, timeout=s.timeout, temperature=s.temperature, reasoning_effort=s.reasoning_effort
    )


def test_reply_to_the_rag_prompt_has_the_shape_rag_expects(llm_settings) -> None:
    reply = make(llm_settings).complete_json(SYSTEM_PROMPT, RAG._user_prompt("How do I find the largest log files?", [EXCERPT]))

    assert isinstance(reply.get("answer"), str)
    assert isinstance(reply.get("citations"), list)
    for citation in reply["citations"]:
        assert isinstance(citation.get("excerpt"), int)
        assert isinstance(citation.get("quote"), str)


def test_unknown_model_is_an_llm_error(llm_settings) -> None:
    # e.g. a typo in config.yaml, or the model not pulled: the API turns this into a 502
    with pytest.raises(LLMError):
        make(llm_settings, model="no-such-model:1b").complete_json("rules", "question")
