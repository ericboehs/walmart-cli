"""Block detection tests. Synthetic payloads only, shaped from the capture.

The live 412 body is not copied here even in scrubbed form. What matters is the
shape: which keys prove a block, and which responses must never be mistaken for
one.
"""

from __future__ import annotations

import pytest

import challenge


def block_payload(**overrides):
    """Shaped like the observed PerimeterX block body, with invented values."""
    payload = {
        "appId": "PXsynthetic",
        "blockScript": "/px/xhr/captcha.js?uuid=00000000-0000-0000-0000-000000000000",
        "firstPartyEnabled": True,
        "hostUrl": "www.walmart.com",
        "jsClientSrc": "/px/xhr/client.js",
        "redirectUrl": "/blocked?uuid=00000000-0000-0000-0000-000000000000",
        "uuid": "[REDACTED]",
        "vid": "[REDACTED]",
    }
    payload.update(overrides)
    return payload


# --- Recognizing a block -----------------------------------------------------


def test_the_observed_block_payload_is_recognized():
    assert challenge.detect(block_payload()) == "perimeterx"


def test_two_block_only_keys_are_enough():
    # Walmart may drop fields; the diagnosis should not depend on all of them.
    assert challenge.detect({"blockScript": "/x.js", "jsClientSrc": "/y.js"}) is not None


def test_a_redirect_to_the_blocked_page_is_enough_on_its_own():
    assert challenge.detect({"redirectUrl": "https://identity.walmart.com/blocked"}) is not None


def test_key_spelling_does_not_matter():
    assert challenge.detect({"block_script": "/x.js", "js_client_src": "/y.js"}) is not None


# --- Not a block -------------------------------------------------------------
#
# The expensive mistake is the other one: calling a working response a block
# strands the user on a command that would have succeeded.


def test_an_ordinary_search_response_is_not_a_block():
    assert challenge.detect({"data": {"search": {"searchResult": {"itemStacks": []}}}}) is None


def test_a_precondition_failure_without_a_block_body_is_not_a_block():
    # 412 is also Walmart's ordinary precondition status: the first capture saw
    # one on setFulfillmentIntent. The number is not the diagnosis.
    assert challenge.detect({"errors": [{"message": "precondition failed"}]}) is None


def test_a_single_generic_key_is_not_enough():
    assert challenge.detect({"firstPartyEnabled": True}) is None


def test_a_product_that_merely_mentions_a_captcha_is_not_a_block():
    payload = {"data": {"search": {"items": [{"name": "Captcha Puzzle Book"}]}}}

    assert challenge.detect(payload) is None


def test_non_dict_payloads_are_not_blocks():
    for payload in (None, [], "blocked", 412, b"blocked"):
        assert challenge.detect(payload) is None


# --- Behaviour ---------------------------------------------------------------


def test_a_block_stops_the_command():
    with pytest.raises(challenge.Blocked) as raised:
        challenge.raise_if_blocked(block_payload(), status=412)

    assert raised.value.code == "challenge_detected"


def test_the_message_tells_the_human_what_to_do():
    with pytest.raises(challenge.Blocked) as raised:
        challenge.raise_if_blocked(block_payload(), status=412)

    message = str(raised.value)
    assert "walmart login" in message
    assert "412" in message
    assert "will not retry" in message


def test_nothing_is_raised_for_a_healthy_response():
    challenge.raise_if_blocked({"data": {"search": {}}}, status=200)


def test_the_error_code_is_one_the_cli_already_understands():
    # Walmart::Worker::LOGIN_CODES lists challenge_detected, so the CLI reports
    # a challenge instead of a generic failure.
    assert challenge.CHALLENGE_CODE == "challenge_detected"


def test_the_module_never_suggests_retrying():
    # Retrying a block is how a temporary block becomes a longer one. This is a
    # rule about behaviour, so it is asserted rather than left to review.
    text = challenge.ADVICE.lower()

    assert "retry" in text
    assert "not retry" in text


def test_a_block_destination_is_recognized_from_the_url_alone():
    assert challenge.detect_url("https://identity.walmart.com/blocked?ref=1") == "unknown"
    assert challenge.detect_url("https://captcha.px-cloud.net/x") == "perimeterx"


def test_an_ordinary_url_is_not_a_block():
    assert challenge.detect_url("https://www.walmart.com/search?q=blocked+drain+cleaner") is None
    assert challenge.detect_url(None) is None
