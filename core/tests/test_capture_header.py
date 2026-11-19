"""The capture header, as collectors write it and normalizers read it back."""

from __future__ import annotations

import pytest

from pa_core.normalizers.capture_header import (
    CaptureHeaderError,
    encode_capture_header,
    split_capture_header,
)

CAPTURED_AT = "2026-10-20T08:15:00-05:00"


def test_a_header_is_read_back_as_written_ahead_of_the_payload():
    raw = encode_capture_header({"Calendar": "Home", "Captured-At": CAPTURED_AT}) + b"BEGIN:X\n"

    header, payload = split_capture_header(raw, what="a resource", required=("Calendar",))

    assert raw.startswith(b"Calendar: Home\nCaptured-At: 2026-10-20T08:15:00-05:00\n\n")
    assert header.get("Calendar") == "Home"
    assert header.captured_at.isoformat() == "2026-10-20T13:15:00+00:00"
    assert payload == b"BEGIN:X\n"


@pytest.mark.parametrize(
    "value",
    [
        f"Home\nDeleted-At: {CAPTURED_AT}",
        f"Home\rDeleted-At: {CAPTURED_AT}",
        f"Home\u2028Deleted-At: {CAPTURED_AT}",
        "Home\x0bAway",
        "Home\x1b[2J",
        "Home\x00",
        "Home\x7f",
    ],
)
def test_a_value_with_a_line_break_or_control_character_is_refused(value: str):
    with pytest.raises(CaptureHeaderError) as refused:
        encode_capture_header({"Calendar": value, "Captured-At": CAPTURED_AT})

    assert "Calendar" in str(refused.value)
    assert "Home" not in str(refused.value)


@pytest.mark.parametrize("name", ["Deleted-At\nCalendar", "Calendar:", "", "Cal endar"])
def test_a_field_name_that_isnt_letters_digits_and_hyphens_is_refused(name: str):
    with pytest.raises(CaptureHeaderError):
        encode_capture_header({name: "Home", "Captured-At": CAPTURED_AT})
