import json
from typing import Any

import httpx

from siteops_rag.llm.base import ChatModel, LLMError


class OpenAICompatChatModel(ChatModel):
    """Calls an OpenAI-compatible `/chat/completions` endpoint (vLLM, Ollama...) in JSON mode."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float,
        max_retries: int,
        temperature: float,
        reasoning_effort: str | None = None,
        transport: httpx.BaseTransport | None = None,
        api_key: str = "EMPTY",
    ):
        super().__init__(model=model)
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            # retries= only covers connection failures (server down, refused), not 5xx responses
            transport=transport or httpx.HTTPTransport(retries=max_retries),
        )

    def close(self) -> None:
        self._client.close()

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},  # the server constrains the reply to valid JSON
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.reasoning_effort is not None:  # omitted otherwise: servers without thinking may reject the field
            body["reasoning_effort"] = self.reasoning_effort
        try:
            response = self._client.post("/chat/completions", json=body)
            response.raise_for_status()
            reply = json.loads(response.json()["choices"][0]["message"]["content"])
        except httpx.HTTPError as exc:  # connection, timeout, 4xx/5xx
            raise LLMError(f"chat request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:  # not the expected shape, or not JSON
            raise LLMError(f"malformed chat response: {exc}") from exc
        if not isinstance(reply, dict):
            raise LLMError(f"expected a JSON object, got {type(reply).__name__}")
        return reply
