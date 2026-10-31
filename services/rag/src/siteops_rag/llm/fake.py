from typing import Any

from siteops_rag.llm.base import ChatModel


class FakeChatModel(ChatModel):
    """Offline chat model for tests: always gives the same reply, and records what it was asked."""

    def __init__(self, reply: dict[str, Any] | None = None, model: str = "fake") -> None:
        super().__init__(model=model)
        self.reply = reply if reply is not None else {"answer": "I don't know.", "citations": []}
        self.calls: list[tuple[str, str]] = []  # (system, user) of each call, for assertions

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.reply
