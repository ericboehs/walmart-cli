"""Protocol tests: stdout stays machine-readable and secrets stay out of it."""

from __future__ import annotations

import io
import json

import pytest

import protocol


def read_events(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_each_event_is_one_json_line():
    out = io.StringIO()
    protocol.log("hello", stream=out)
    protocol.progress("working", current=1, total=3, stream=out)
    protocol.result({"ok": True}, stream=out)

    events = read_events(out)
    assert [event["type"] for event in events] == ["log", "progress", "result"]
    assert events[1]["current"] == 1
    assert events[2]["data"] == {"ok": True}


def test_result_payloads_are_redacted_too():
    # A parser that accidentally lifts a secret out of a page must not be able
    # to launder it into the CLI's public JSON just because the run succeeded.
    out = io.StringIO()
    protocol.result({"account": {"email": "eric@example.com"}, "signed_in": True}, stream=out)

    text = out.getvalue()
    assert "eric@example.com" not in text
    assert read_events(out)[0]["data"]["signed_in"] is True


def test_result_keeps_the_data_a_caller_actually_needs():
    out = io.StringIO()
    protocol.result({"items": [{"id": "10450114", "price": 3.12}]}, stream=out)
    data = read_events(out)[0]["data"]

    assert data["items"][0]["id"] == "10450114"
    assert data["items"][0]["price"] == 3.12


def test_error_events_carry_a_code():
    out = io.StringIO()
    protocol.error("login_required", "session expired", stream=out)

    event = read_events(out)[0]
    assert event["type"] == "error"
    assert event["code"] == "login_required"
    assert "unverified" not in event


def test_unverified_errors_are_flagged_for_exit_code_three():
    out = io.StringIO()
    protocol.error("internal", "died mid-add", unverified=True, stream=out)

    assert read_events(out)[0]["unverified"] is True


def test_error_details_are_redacted():
    out = io.StringIO()
    protocol.error(
        "internal",
        "boom",
        details={"request": {"password": "hunter2", "email": "eric@example.com", "url": "/cart"}},
        stream=out,
    )

    text = out.getvalue()
    assert "hunter2" not in text
    assert "eric@example.com" not in text
    assert "/cart" in text


def test_redact_walks_nested_structures():
    payload = {"a": [{"token": "abc"}, {"safe": 1}], "cookie": "x"}
    cleaned = protocol.redact(payload)

    assert cleaned["a"][0]["token"] == "[REDACTED]"
    assert cleaned["a"][1]["safe"] == 1
    assert cleaned["cookie"] == "[REDACTED]"


def test_read_request_parses_one_object():
    request = protocol.read_request(io.StringIO('{"action":"probe","params":{}}\n'))

    assert request["action"] == "probe"


def test_read_request_accepts_the_declared_protocol_version():
    raw = f'{{"protocol":{protocol.PROTOCOL_VERSION},"action":"probe"}}\n'
    assert protocol.read_request(io.StringIO(raw))["action"] == "probe"


# A mismatch means the Ruby and Python halves came from different checkouts.
# Saying so beats failing later in a way that looks like Walmart changed.
def test_read_request_rejects_an_unknown_protocol_version():
    with pytest.raises(ValueError, match="out of sync"):
        protocol.read_request(io.StringIO('{"protocol":99,"action":"probe"}\n'))


@pytest.mark.parametrize("raw", ["", "   \n", "[1,2]", '{"params":{}}'])
def test_malformed_requests_raise(raw):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        protocol.read_request(io.StringIO(raw))
