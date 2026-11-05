"""The Gmail API client's answers to Gmail's errors, on a stand-in session: a rate limit is
waited out, and any other refusal names Google's reason code, which holds no mail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pa_home.collectors import CollectionError
from pa_home.collectors.gmail import MessageGone
from pa_home.gmail_api import GoogleGmailClient


class Answer:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def refusal(status: int, reason: str) -> Answer:
    return Answer(status, {"error": {"code": status, "errors": [{"reason": reason}]}})


class Session:
    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.calls = 0

    def get(self, url: str, **_: Any) -> Answer:
        self.calls += 1
        return self.answers.pop(0)


def client(*answers: Answer) -> tuple[GoogleGmailClient, Session, list[float]]:
    waits: list[float] = []
    gmail = GoogleGmailClient(Path("unused"), sleep=waits.append)
    session = Session(*answers)
    gmail._session = session  # pyright: ignore[reportAttributeAccessIssue]
    return gmail, session, waits


PROFILE = Answer(200, {"historyId": "7"})


@pytest.mark.parametrize(
    "limited",
    [refusal(403, "userRateLimitExceeded"), refusal(403, "rateLimitExceeded"), refusal(429, "x")],
)
def test_a_rate_limit_is_waited_out_with_a_growing_wait(limited: Answer):
    gmail, session, waits = client(limited, limited, PROFILE)

    assert gmail.history_position() == "7"
    assert session.calls == 3
    assert len(waits) == 2 and waits[1] > waits[0] > 0


def test_a_rate_limit_that_never_lifts_fails_and_says_so():
    limited = refusal(403, "userRateLimitExceeded")
    gmail, _, waits = client(*[limited] * 20)

    with pytest.raises(
        CollectionError, match=r"rate limit \(HTTP 403 userRateLimitExceeded\) didn.t lift"
    ):
        gmail.history_position()
    assert 0 < sum(waits) <= 300


def test_a_server_error_is_retried():
    gmail, session, _ = client(Answer(503, {}), PROFILE)

    assert gmail.history_position() == "7"
    assert session.calls == 2


def test_another_refusal_names_googles_reason_and_isnt_retried():
    gmail, session, waits = client(refusal(403, "insufficientPermissions"))

    with pytest.raises(
        CollectionError,
        match=r"Gmail refused access \(HTTP 403 insufficientPermissions\): run pa gmail auth",
    ):
        gmail.history_position()
    assert session.calls == 1 and waits == []


@pytest.mark.parametrize(
    "body",
    [
        ValueError("not json"),
        {"error": "a string"},
        {"error": {"errors": [{"reason": "Re: lunch with Ben?"}]}},
    ],
)
def test_a_refusal_without_a_plain_reason_code_names_only_the_status(body: Any):
    # Only a bare code is repeated, so nothing Gmail echoes back from a message reaches a log.
    gmail, _, _ = client(Answer(403, body))

    with pytest.raises(CollectionError) as failed:
        gmail.history_position()
    assert str(failed.value) == "Gmail refused access (HTTP 403): run pa gmail auth"


def test_a_message_gone_since_it_was_listed_is_message_gone():
    gmail, _, _ = client(refusal(404, "notFound"), refusal(404, "notFound"))

    with pytest.raises(MessageGone):
        gmail.fetch("18c2f0a1b2c3d4e5")
    with pytest.raises(MessageGone):
        gmail.label_ids("18c2f0a1b2c3d4e5")


def test_another_not_found_still_fails_the_collection():
    gmail, _, _ = client(refusal(404, "notFound"))

    with pytest.raises(CollectionError, match=r"Gmail answered HTTP 404 notFound"):
        gmail.label_names()
