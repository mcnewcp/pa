"""The model client: a prompt and an output schema in, validated structured output out.

Provider-neutral. A backend turns one request into the model's raw reply text; the client
validates that reply against the output schema.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from pa_core.errors import PaError

_ATTEMPTS = 2  # the first reply plus one retry


class InvalidModelOutputError(PaError):
    """The model kept replying with output that does not match the requested schema."""


class ModelBackendError(PaError):
    """The call to the model failed (for example the provider's command exited with an error)."""


@dataclass(frozen=True)
class ModelRequest:
    """One call to a model: the prompt and the JSON Schema its reply must satisfy."""

    prompt: str
    schema: dict[str, Any]


class ModelBackend(Protocol):
    """A provider that answers one request with the model's raw reply text (JSON)."""

    def complete(self, request: ModelRequest) -> str: ...


class ModelClient:
    """Asks a backend for structured output and validates it."""

    def __init__(self, backend: ModelBackend) -> None:
        self._backend = backend

    def generate[T: BaseModel](self, prompt: str, output: type[T]) -> T:
        """Ask for output matching `output`; an invalid reply is retried once, then raised.

        Raises InvalidModelOutputError when both replies are invalid, and lets the backend's
        ModelBackendError (the call itself failed) through without retrying.
        """
        schema = output.model_json_schema()
        request = ModelRequest(prompt=prompt, schema=schema)
        for attempt in range(1, _ATTEMPTS + 1):
            reply = self._backend.complete(request)
            try:
                return output.model_validate_json(reply)
            except ValidationError as error:
                if attempt == _ATTEMPTS:
                    raise InvalidModelOutputError(
                        f"The model's reply did not match {output.__name__} after "
                        f"{_ATTEMPTS} attempts. Last problem: {error}"
                    ) from error
                request = ModelRequest(prompt=_retry_prompt(prompt, error), schema=schema)
        raise AssertionError("unreachable")


def _retry_prompt(prompt: str, error: ValidationError) -> str:
    return (
        f"{prompt}\n\n"
        "Your previous reply did not match the required JSON Schema:\n"
        f"{error}\n"
        "Reply again with only a JSON value that matches the schema."
    )


type FakeReply = str | BaseModel | Mapping[str, Any]
"""A scripted reply: raw text (sent as is, so it can be invalid), or an object sent as JSON."""


class FakeModelBackend:
    """A backend for tests that replies with scripted responses, in order.

    Every request it receives is recorded in `requests`. Use `replying` instead when the reply
    should depend on the request.
    """

    def __init__(self, *responses: FakeReply) -> None:
        self._responses = list(responses)
        self._reply: Callable[[ModelRequest], FakeReply] = self._next_scripted
        self.requests: list[ModelRequest] = []

    @classmethod
    def replying(cls, reply: Callable[[ModelRequest], FakeReply]) -> FakeModelBackend:
        """A fake that answers every request with `reply(request)`."""
        backend = cls()
        backend._reply = reply
        return backend

    def complete(self, request: ModelRequest) -> str:
        self.requests.append(request)
        return _as_text(self._reply(request))

    def _next_scripted(self, request: ModelRequest) -> FakeReply:
        if not self._responses:
            raise ModelBackendError(
                f"FakeModelBackend has no scripted response left for request "
                f"{len(self.requests)} (prompt: {request.prompt[:80]!r})"
            )
        return self._responses.pop(0)


def _as_text(reply: FakeReply) -> str:
    if isinstance(reply, str):
        return reply
    if isinstance(reply, BaseModel):
        return reply.model_dump_json()
    return json.dumps(reply)
