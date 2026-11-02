from abc import ABC, abstractmethod

from siteops_common.contracts import SearchRequest, SearchResponse


class RagError(Exception):
    """rag could not answer: unreachable, an error status, or a reply that is not a SearchResponse.
    The triage loop records it as the window's error without knowing the transport."""


class RagClient(ABC):
    """The rag service's /search, as the agent's search_runbooks tool uses it."""

    @abstractmethod
    def search(self, request: SearchRequest) -> SearchResponse:
        """The ranked chunks for the query. Raises RagError when rag can't answer."""
