"""Provider-neutral LLM contract and OpenAI Responses implementation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from app.schemas import ConversationMessage


class LLMServiceError(RuntimeError):
    """Safe provider error that does not expose credentials or response bodies."""


class LLMProviderError(LLMServiceError):
    """The model provider call failed."""


class LLMJSONDecodeError(LLMServiceError):
    """The provider response was not valid JSON."""


@dataclass(frozen=True)
class LLMRequest:
    """Provider-neutral structured-generation request."""

    instructions: str
    messages: Sequence[ConversationMessage]
    output_schema: Mapping[str, Any]
    schema_name: str


class LLMService(Protocol):
    """Structural interface implemented by real and deterministic LLM clients."""

    def complete(self, request: LLMRequest) -> Mapping[str, Any]:
        """Return decoded structured output."""


class OpenAIResponsesService:
    """OpenAI Responses API adapter using strict JSON Schema output."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        resolved_model = model or os.getenv("OPENAI_MODEL")
        if not resolved_key:
            raise ValueError("OPENAI_API_KEY is required")
        if not resolved_model:
            raise ValueError("OPENAI_MODEL is required")
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=resolved_key)
        self._client = client
        self._model = resolved_model

    def complete(self, request: LLMRequest) -> Mapping[str, Any]:
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=request.instructions,
                input=[
                    {"role": message.role.value, "content": message.content}
                    for message in request.messages
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": request.schema_name,
                        "schema": dict(request.output_schema),
                        "strict": True,
                    }
                },
                store=False,
            )
        except Exception as exc:
            raise LLMProviderError("LLM provider call failed") from exc
        try:
            decoded = json.loads(response.output_text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise LLMJSONDecodeError("LLM structured output was not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise LLMServiceError("LLM structured output must be a JSON object")
        return decoded
