"""Tests for cart mutation.

The decision table gets the most attention here, because it is the part that
decides whether someone believes their cart changed. Every row is asserted from
both sides: not just that the right outcome comes out, but that the wrong ones
do not.
"""

from __future__ import annotations

import json

import pytest

import cart_mutate
import cart_parse
import safety

APPLIED = cart_mutate.APPLIED
REJECTED = cart_mutate.REJECTED
UNVERIFIED = cart_mutate.UNVERIFIED
CONTRACT_STALE = cart_mutate.CONTRACT_STALE

OK = cart_mutate.Acknowledgement(True)
NO = cart_mutate.Acknowledgement(False, "rejected", "out of stock")
STALE = cart_mutate.Acknowledgement(False, CONTRACT_STALE, "rotated")


# --- the decision table ------------------------------------------------------------
#
# (acknowledgement, before, after, target) -> outcome. Written as data so the
# whole policy can be read at once; a table that has to be reconstructed from
# eight test bodies is a table nobody audits.

TABLE = {
    "accepted and the cart agrees": (OK, 1, 2, 2, APPLIED),
    "accepted and the cart disagrees": (OK, 1, 5, 2, UNVERIFIED),
    "accepted but the cart never moved": (OK, 1, 1, 2, UNVERIFIED),
    "refused and the cart never moved": (NO, 1, 1, 2, REJECTED),
    "refused but the cart moved anyway": (NO, 1, 3, 2, UNVERIFIED),
    "refused as stale, cart unmoved": (STALE, 1, 1, 2, CONTRACT_STALE),
    "refused as stale, cart moved": (STALE, 1, 4, 2, UNVERIFIED),
    "accepted but the post-read failed": (OK, 1, None, 2, UNVERIFIED),
    "refused and the post-read failed": (NO, 1, None, 2, UNVERIFIED),
    "stale and the post-read failed": (STALE, 1, None, 2, UNVERIFIED),
    "accepted, pre-read unknown, cart agrees": (OK, None, 2, 2, APPLIED),
    "refused, pre-read unknown, cart at target": (NO, None, 2, 2, UNVERIFIED),
    "removal confirmed": (OK, 2, 0, 0, APPLIED),
    "removal refused, still there": (NO, 2, 2, 0, REJECTED),
}


@pytest.mark.parametrize("case", sorted(TABLE))
def test_the_decision_table(case):
    ack, before, after, target, expected = TABLE[case]
    outcome, _ = cart_mutate.classify(ack, before, after, target)
    assert outcome == expected, case


@pytest.mark.parametrize("case", sorted(TABLE))
def test_only_a_confirmed_change_is_ever_applied(case):
    """The property behind the table, asserted independently of it.

    APPLIED requires two things and it is worth stating them apart from the
    rows: the server agreed, and the cart we then read holds the target. Either
    alone has a story where nothing happened.
    """
    ack, before, after, target, _ = TABLE[case]
    outcome, _ = cart_mutate.classify(ack, before, after, target)
    if outcome == APPLIED:
        assert ack.accepted
        assert after == target


def test_a_matching_cart_alone_is_not_success():
    """Someone editing the same cart from a phone produces exactly this."""
    outcome, message = cart_mutate.classify(NO, 1, 2, 2)
    assert outcome != APPLIED
    assert "will not send a second request" in message


def test_an_unverified_outcome_never_suggests_retrying():
    """One lost update is bad; two is what a corrective retry produces."""
    for ack, before, after in ((OK, 1, 5), (NO, 1, 3), (STALE, 1, 4), (OK, 1, None)):
        outcome, message = cart_mutate.classify(ack, before, after, 2)
        assert outcome == UNVERIFIED
        assert "again" not in message.lower()
        assert "retry" not in message.lower()


def test_every_outcome_but_success_explains_itself():
    for case, (ack, before, after, target, expected) in TABLE.items():
        _, message = cart_mutate.classify(ack, before, after, target)
        if expected != APPLIED:
            assert message.strip(), case


# --- acknowledgement ---------------------------------------------------------------


def test_a_200_carrying_errors_is_not_accepted():
    ack = cart_mutate.acknowledgement(200, {"data": None, "errors": [{"message": "nope"}]})
    assert not ack.accepted
    assert ack.detail == "nope"


def test_a_rotated_persisted_query_is_named_as_such():
    for error in (
        {"message": "PersistedQueryNotFound"},
        {"message": "boom", "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"}},
        "persisted query not found",
    ):
        ack = cart_mutate.acknowledgement(200, {"errors": [error]})
        assert ack.reason == CONTRACT_STALE, error


def test_the_stale_contract_message_does_not_blame_the_user():
    ack = cart_mutate.acknowledgement(200, {"errors": [{"message": "PersistedQueryNotFound"}]})
    assert "walmart-cli" in ack.detail
    assert "was not changed" in ack.detail


def test_silence_is_not_agreement():
    """No errors and no data. Never observed; deliberately not read as yes."""
    for payload in ({}, {"data": None}, {"data": {}}, {"errors": []}):
        assert not cart_mutate.acknowledgement(200, payload).accepted


def test_a_plausible_success_is_accepted():
    assert cart_mutate.acknowledgement(200, {"data": {"updateItems": {"id": "x"}}}).accepted


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503, None, "200", -1])
def test_a_bad_status_is_not_accepted(status):
    assert not cart_mutate.acknowledgement(status, {"data": {"updateItems": {}}}).accepted


@pytest.mark.parametrize("payload", [None, [], "text", 7])
def test_an_unreadable_body_is_not_accepted(payload):
    assert not cart_mutate.acknowledgement(200, payload).accepted


# --- deriving the target from a delta -----------------------------------------------


def test_a_delta_is_applied_to_what_the_cart_holds_now():
    assert cart_mutate.target_quantity(2, 1, remove=False) == 3
    assert cart_mutate.target_quantity(2, -1, remove=False) == 1


def test_removal_is_zero_whatever_the_cart_holds():
    assert cart_mutate.target_quantity(9, None, remove=True) == 0


def test_decrementing_past_empty_is_refused_not_clamped():
    with pytest.raises(cart_mutate.CartMutationRefused) as raised:
        cart_mutate.target_quantity(2, -3, remove=False)
    assert "cart remove" in str(raised.value)


def test_no_delta_and_no_removal_is_refused():
    with pytest.raises(cart_mutate.CartMutationRefused):
        cart_mutate.target_quantity(2, None, remove=False)


# --- the request body ---------------------------------------------------------------

LINE = {"item_id": "33282303", "offer_id": "62D56D88692E404098227E89A7893ECF", "name": "Chicken"}


def test_the_body_matches_the_shape_the_site_sends():
    body = cart_mutate.build_update_items_body("cart-1", LINE, 2)
    supplied = body["variables"]["input"]
    assert supplied["cartId"] == "cart-1"
    assert supplied["items"] == [
        {
            "offerId": LINE["offer_id"],
            "usItemId": LINE["item_id"],
            "quantity": 2,
            "name": "Chicken",
            "isPharmacyPrescription": False,
            "isQSRItem": False,
        }
    ]
    assert supplied["features"] == list(cart_mutate.OBSERVED_FEATURES)


def test_only_the_quantity_differs_between_two_requests():
    """Guards against a flag quietly becoming a function of the quantity."""
    one = cart_mutate.build_update_items_body("c", LINE, 1)
    zero = cart_mutate.build_update_items_body("c", LINE, 0)
    one["variables"]["input"]["items"][0]["quantity"] = 0
    assert one == zero


def test_the_body_carries_exactly_one_line():
    body = cart_mutate.build_update_items_body("c", LINE, 3)
    assert len(body["variables"]["input"]["items"]) == 1


def test_the_endpoint_passes_the_safety_guard():
    url = cart_mutate.update_items_url()
    assert safety.operation_of(url) == "updateItems"
    assert not safety.is_forbidden_operation(safety.operation_of(url))


def test_the_endpoint_is_the_operation_we_captured():
    assert cart_mutate.UPDATE_ITEMS_PATH.endswith(cart_mutate.UPDATE_ITEMS_HASH)
    assert f"/{cart_mutate.UPDATE_ITEMS}/" in cart_mutate.UPDATE_ITEMS_PATH


# --- reading the cart ---------------------------------------------------------------

CART = {
    "lines": [
        {"item_id": "33282303", "quantity": 2, "name": "Chicken"},
        {"item_id": "999", "quantity": 1, "name": "Marketplace"},
    ]
}


def test_a_missing_item_reads_as_zero_not_unknown():
    assert cart_mutate.quantity_of(CART, "nope") == 0


def test_an_unreadable_cart_reads_as_unknown_not_zero():
    """The distinction the whole read path is built on, restated here."""
    assert cart_mutate.quantity_of(None, "33282303") is None


def test_a_present_item_reports_its_quantity():
    assert cart_mutate.quantity_of(CART, "33282303") == 2


def test_item_ids_are_compared_as_strings():
    assert cart_mutate.find_line(CART, 33282303)["quantity"] == 2
    assert cart_mutate.find_line(CART, " 33282303 ")["quantity"] == 2


# --- the cart id never leaves this module ------------------------------------------
#
# The invariant narrowed when updateItems turned out to require a cart id:
# from "we never hold it" to "it never crosses the worker boundary and never
# survives the action". These canaries run the real action end to end and hunt
# the value in every direction it could escape -- the result document, the
# NDJSON events, and stderr. A narrowed invariant with no test is a deleted one.

CART_ID = "cart-id-canary-0000-not-a-real-cart"
OPTIONS = {"profile_dir": "/tmp/profile", "profile_lock": "/tmp/profile.lock"}
ITEM_ID = "33282303"
MARKETPLACE_LINE = {
    "item_id": ITEM_ID,
    "quantity": 1,
    "seller_type": "EXTERNAL",
    "seller_name": "Someone Else",
    "name": "Sold by someone else",
    "offer_id": "X",
}


def _live_payload(quantity):
    return {
        "data": {
            "cart": {
                "id": CART_ID,
                "lineItems": [
                    {
                        "id": "line-1",
                        "quantity": quantity,
                        "product": {
                            "usItemId": ITEM_ID,
                            "offerId": "62D56D88692E404098227E89A7893ECF",
                            "name": "Chicken",
                            "sellerType": "INTERNAL",
                            "sellerName": "Walmart.com",
                        },
                        "priceInfo": {"linePrice": {"value": 6.78}},
                    }
                ],
                "priceDetails": {"subTotal": {"value": 6.78}, "grandTotal": {"value": 6.78}},
            }
        }
    }


class MutatingSession:
    """A cart that actually changes when the mutation is sent."""

    def __init__(self, before=1, after=None, status=200, response=None):
        self.quantity = before
        self.after = before + 1 if after is None else after
        self.status = status
        self.response = response or {"data": {"updateItems": {"id": "x"}}}
        self.page = self
        self.handlers = []
        self.sent = []
        self.url = "https://www.walmart.com/cart"

    def on_response(self, handler):
        self.handlers.append(handler)

    def goto(self, url, **_kwargs):
        self.url = url
        return type("R", (), {"status": 200, "url": url})()

    def wait_for_timeout(self, _ms):
        payload = _live_payload(self.quantity if not self.sent else self.after)
        for handler in list(self.handlers):
            handler(type("Resp", (), {"url": f"{_GETCART}x", "json": lambda _s: payload})())

    def evaluate(self, _script, argument):
        self.sent.append(argument)
        return {"status": self.status, "payload": self.response}


_GETCART = "https://www.walmart.com/orchestra/home/graphql/getCart/"


@pytest.fixture
def mutation_run(monkeypatch):
    """Run the real action against a fake session, capturing every output."""

    def run(session, params):
        monkeypatch.setattr("browser.open_session", lambda _o: _nullcontext(session))
        events = []
        monkeypatch.setattr("protocol.progress", lambda text, **k: events.append(text))
        request = {"params": params, "options": OPTIONS}
        return cart_mutate.action_cart_change(request), events

    return run


class _nullcontext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_exc):
        return False


def test_the_cart_id_is_sent_to_walmart_but_never_returned(mutation_run):
    session = MutatingSession(before=1, after=2)
    result, events = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})

    # It really was used: the request could not work otherwise.
    assert session.sent[0]["body"]["variables"]["input"]["cartId"] == CART_ID
    # And it is in none of the three ways out.
    assert CART_ID not in json.dumps(result)
    assert CART_ID not in json.dumps(events)


def test_the_cart_id_is_absent_from_a_failed_run(mutation_run, capsys):
    session = MutatingSession(before=1, after=1, status=500)
    result, events = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    captured = capsys.readouterr()
    assert result["outcome"] != APPLIED
    for text in (json.dumps(result), json.dumps(events), captured.err, captured.out):
        assert CART_ID not in text


def test_the_cart_id_is_absent_when_the_response_is_unreadable(mutation_run, capsys):
    session = MutatingSession(before=1, after=2, response="not json")
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert CART_ID not in json.dumps(result) + capsys.readouterr().err


def test_the_result_document_carries_no_unexpected_keys(mutation_run):
    session = MutatingSession(before=1, after=2)
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert set(result) == {
        "item_id",
        "outcome",
        "quantity_before",
        "quantity_after",
        "quantity_intended",
        "changed",
        "message",
    }


# --- the action, end to end ---------------------------------------------------------


def test_a_confirmed_increment_reports_applied(mutation_run):
    session = MutatingSession(before=1, after=2)
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert result["outcome"] == APPLIED
    assert (result["quantity_before"], result["quantity_after"]) == (1, 2)
    assert result["changed"] is True


def test_the_quantity_sent_is_derived_from_the_read_not_the_request(mutation_run):
    """The delta is relative to what the cart holds now, not to anything cached."""
    session = MutatingSession(before=4, after=5)
    mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert session.sent[0]["body"]["variables"]["input"]["items"][0]["quantity"] == 5


def test_removal_sends_zero(mutation_run):
    session = MutatingSession(before=2, after=0)
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "remove": True})
    assert session.sent[0]["body"]["variables"]["input"]["items"][0]["quantity"] == 0
    assert result["outcome"] == APPLIED


def test_a_cart_that_did_not_move_is_never_reported_as_applied(mutation_run):
    session = MutatingSession(before=1, after=1)
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert result["outcome"] == UNVERIFIED
    assert result["changed"] is False


def test_a_marketplace_line_is_refused_before_anything_is_sent(mutation_run, monkeypatch):
    session = MutatingSession(before=1)
    monkeypatch.setattr(
        "cart_parse.parse_cart",
        lambda _p: {"lines": [dict(MARKETPLACE_LINE)], "totals": {}},
    )
    with pytest.raises(cart_mutate.CartMutationRefused) as raised:
        mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert "marketplace" in str(raised.value)
    assert session.sent == []


def test_an_item_not_in_the_cart_is_refused_before_anything_is_sent(mutation_run):
    session = MutatingSession(before=1)
    with pytest.raises(cart_mutate.CartMutationRefused):
        mutation_run(session, {"item_id": "not-here", "delta": 1})
    assert session.sent == []


def test_a_delta_of_zero_is_refused_as_a_usage_error(mutation_run):
    """Not a no-op success: asking for no change is asking for nothing."""
    session = MutatingSession(before=2, after=2)
    with pytest.raises(ValueError):
        mutation_run(session, {"item_id": ITEM_ID, "delta": 0})
    assert session.sent == []


def test_removing_a_line_already_at_zero_sends_nothing(mutation_run, monkeypatch):
    """The one path where the target equals the current quantity."""
    session = MutatingSession(before=0, after=0)
    empty = dict(MARKETPLACE_LINE, quantity=0, seller_type="INTERNAL", seller_name="Walmart.com")
    monkeypatch.setattr("cart_parse.parse_cart", lambda _p: {"lines": [empty], "totals": {}})
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "remove": True})
    assert session.sent == []
    assert result["changed"] is False


def test_the_cart_is_read_again_after_the_change(mutation_run):
    session = MutatingSession(before=1, after=2)
    _, events = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert events == ["reading the cart", "updating the cart", "checking the cart"]


def test_an_unreadable_post_state_is_unverified_not_failed(mutation_run, monkeypatch):
    session = MutatingSession(before=1, after=2)
    calls = {"n": 0}
    real = cart_parse.parse_cart

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] > 1:
            raise cart_parse.CartUnreadable("blocked")
        return real(payload)

    monkeypatch.setattr("cart_parse.parse_cart", flaky)
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert result["outcome"] == UNVERIFIED
    assert "could not be read afterwards" in result["message"]
