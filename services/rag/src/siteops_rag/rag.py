"""Retrieve, then answer from the retrieved chunks with citations, or "I don't know". No reranker yet (rag/8)."""

import time
from typing import Any

from siteops_common.contracts import AskResponse, Citation, SearchHit, SearchResponse
from siteops_rag.embedder.base import Embedder
from siteops_rag.llm.base import ChatModel
from siteops_rag.vector_store.base import ScoredChunk, VectorStore

I_DONT_KNOW = "I don't know."

SYSTEM_PROMPT = f"""You answer site operations questions using only the runbook excerpts you are given.
Each excerpt starts with its number in brackets, e.g. [1].
Reply with a JSON object: {{"answer": "...", "citations": [{{"excerpt": 1, "quote": "..."}}]}}
- Every citation's "quote" is copied word for word from that excerpt and supports the answer.
- If the excerpts contradict something the question assumes, the answer corrects it, citing the excerpt that contradicts it.
- If the excerpts do not contain the answer, reply {{"answer": "{I_DONT_KNOW}", "citations": []}}."""


class RAG:
    def __init__(self, embedder: Embedder, vector_store: VectorStore, chat_model: ChatModel) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.chat_model = chat_model

    def search(self, query: str, top_k: int) -> list[ScoredChunk]:
        return self.vector_store.search(self.embedder.embed(query), top_k)

    def search_response(self, query: str, top_k: int) -> SearchResponse:
        start = time.perf_counter()
        scored = self.search(query, top_k)
        return SearchResponse(
            hits=[self._to_hit(s) for s in scored],
            embedding_model=self.embedder.embedding_model,
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    def ask(self, question: str, top_k: int) -> AskResponse:
        start = time.perf_counter()
        scored = self.search(question, top_k)

        answer, citations = I_DONT_KNOW, []
        if scored:  # nothing retrieved: no need to ask the LLM
            reply = self.chat_model.complete_json(SYSTEM_PROMPT, self._user_prompt(question, scored))
            citations = self._checked_citations(reply, scored)
            # rag/9: an answer counts only if at least one citation checks out (rag/10)
            if citations:
                answer = str(reply.get("answer", ""))

        return AskResponse(
            answered=bool(citations),
            answer=answer,
            citations=citations,
            hits=[self._to_hit(s) for s in scored],
            model=self.chat_model.model,
            embedding_model=self.embedder.embedding_model,
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    @staticmethod
    def _user_prompt(question: str, scored: list[ScoredChunk]) -> str:
        excerpts = "\n\n".join(f"[{i}] {s.chunk.text}" for i, s in enumerate(scored, start=1))
        return f"Runbook excerpts:\n\n{excerpts}\n\nQuestion: {question}"

    @staticmethod
    def _checked_citations(reply: dict[str, Any], scored: list[ScoredChunk]) -> list[Citation]:
        """Keep the citations that point to a retrieved excerpt and quote it word for word (rag/10)."""
        items = reply.get("citations")
        if not isinstance(items, list):
            return []

        citations = []
        for item in items:
            if not isinstance(item, dict):
                continue
            number, quote = item.get("excerpt"), item.get("quote")
            if not isinstance(number, int) or not 1 <= number <= len(scored) or not isinstance(quote, str) or not quote.strip():
                continue
            chunk = scored[number - 1].chunk
            if RAG._squash(quote) not in RAG._squash(chunk.text):  # whitespace may differ, words may not
                continue
            citations.append(
                Citation(chunk_id=chunk.chunk_id, source=chunk.source, heading_path=list(chunk.heading_path), page=chunk.page, quote=quote)
            )
        return citations

    @staticmethod
    def _squash(text: str) -> str:
        return " ".join(text.split())

    @staticmethod
    def _to_hit(scored: ScoredChunk) -> SearchHit:
        chunk = scored.chunk
        return SearchHit(
            chunk_id=chunk.chunk_id,
            source=chunk.source,
            heading_path=list(chunk.heading_path),
            page=chunk.page,
            text=chunk.text,
            score=scored.score,
        )
