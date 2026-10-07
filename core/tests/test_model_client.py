import pytest
from pydantic import BaseModel

from pa_core.errors import PaError
from pa_core.model_client import (
    FakeModelBackend,
    InvalidModelOutputError,
    ModelBackendError,
    ModelClient,
    ModelRequest,
)


class Verdict(BaseModel):
    correct: bool
    reasoning: str


def test_a_valid_response_comes_back_as_a_validated_object():
    backend = FakeModelBackend('{"correct": true, "reasoning": "Matches the expected answer."}')
    client = ModelClient(backend)

    verdict = client.generate("Is the answer right?", Verdict)

    assert verdict == Verdict(correct=True, reasoning="Matches the expected answer.")


def test_one_invalid_response_is_retried_and_the_valid_retry_is_returned():
    backend = FakeModelBackend(
        '{"correct": "maybe"}',
        '{"correct": false, "reasoning": "Names the wrong day."}',
    )
    client = ModelClient(backend)

    verdict = client.generate("Is the answer right?", Verdict)

    assert verdict == Verdict(correct=False, reasoning="Names the wrong day.")
    first, retry = backend.requests
    assert retry.prompt.startswith(first.prompt)
    assert retry.schema == first.schema


def test_two_invalid_responses_raise_a_clear_error():
    backend = FakeModelBackend("Sure! The answer looks right to me.", '{"correct": true}')
    client = ModelClient(backend)

    with pytest.raises(InvalidModelOutputError) as raised:
        client.generate("Is the answer right?", Verdict)

    assert isinstance(raised.value, PaError)
    message = str(raised.value)
    assert "Verdict" in message
    assert "2 attempts" in message
    assert "reasoning" in message  # the field the last reply was missing
    assert len(backend.requests) == 2


def test_the_fake_can_be_scripted_with_objects_instead_of_json_text():
    backend = FakeModelBackend(
        Verdict(correct=True, reasoning="Right day."), {"correct": False, "reasoning": "No."}
    )
    client = ModelClient(backend)

    assert client.generate("First?", Verdict) == Verdict(correct=True, reasoning="Right day.")
    assert client.generate("Second?", Verdict) == Verdict(correct=False, reasoning="No.")


def test_the_fake_can_reply_from_the_request_so_replies_follow_the_prompt():
    def reply(request: ModelRequest) -> dict[str, object]:
        return {"correct": "Saturday" in request.prompt, "reasoning": "Checked for Saturday."}

    client = ModelClient(FakeModelBackend.replying(reply))

    assert client.generate("Answer: Saturday", Verdict).correct is True
    assert client.generate("Answer: Monday", Verdict).correct is False


def test_a_fake_that_runs_out_of_scripted_responses_says_so():
    client = ModelClient(FakeModelBackend())

    with pytest.raises(ModelBackendError, match="no scripted response"):
        client.generate("Is the answer right?", Verdict)
