from siteops_agent.chat.base import ChatEngine, ChatRequest, ChatTurn, UpstreamError


class FakeChatEngine(ChatEngine):
    """Returns a fixed turn, or fails like a down service (the fake, design/1). Records the requests it got."""

    def __init__(self, turn: ChatTurn, error: UpstreamError | None = None) -> None:
        self.turn = turn
        self.error = error
        self.requests: list[ChatRequest] = []

    def reply(self, request: ChatRequest) -> ChatTurn:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.turn
