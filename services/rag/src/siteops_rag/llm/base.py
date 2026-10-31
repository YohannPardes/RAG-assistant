from abc import ABC, abstractmethod
from typing import Any


class LLMError(Exception):
    """The LLM backend failed (unreachable, error response, reply that is not a JSON object)."""


class ChatModel(ABC):
    """Answers one system + user prompt with a JSON object."""

    def __init__(self, model: str) -> None:
        self.model = model  # the model's own name, reported in AskResponse

    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """One completion, parsed as a JSON object. Raises LLMError on any backend failure."""
