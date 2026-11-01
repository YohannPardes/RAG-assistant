import pytest

from siteops_agent.rag_client.base import RagError
from siteops_agent.rag_client.fake import FakeRagClient
from siteops_common.contracts import SearchHit, SearchRequest


def hit(chunk_id: str) -> SearchHit:
    return SearchHit(chunk_id=chunk_id, source=f"{chunk_id}.md", heading_path=[], page=None, text="…", score=0.5)


def test_it_returns_its_hits_cut_to_top_k_and_records_the_requests() -> None:
    rag = FakeRagClient([hit("a"), hit("b"), hit("c")])

    assert [h.chunk_id for h in rag.search(SearchRequest(query="q1", top_k=2)).hits] == ["a", "b"]
    assert [h.chunk_id for h in rag.search(SearchRequest(query="q2")).hits] == ["a", "b", "c"]  # no top_k: all of them
    assert [r.query for r in rag.requests] == ["q1", "q2"]


def test_it_can_fail_like_an_unreachable_rag() -> None:
    rag = FakeRagClient(error=RagError("down"))

    with pytest.raises(RagError, match="down"):
        rag.search(SearchRequest(query="q"))
    assert len(rag.requests) == 1
