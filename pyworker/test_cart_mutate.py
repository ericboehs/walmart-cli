"""Tests for cart mutation.

The decision table gets the most attention here, because it is the part that
decides whether someone believes their cart changed. Every row is asserted from
both sides: not just that the right outcome comes out, but that the wrong ones
do not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cart_mutate
import cart_parse
import safety

APPLIED = cart_mutate.APPLIED
REJECTED = cart_mutate.REJECTED
UNVERIFIED = cart_mutate.UNVERIFIED
CONTRACT_STALE = cart_mutate.CONTRACT_STALE

# A success shape nobody has observed. Tests need one to exercise everything
# downstream of "Walmart said yes", but it is a hypothesis and is labelled as
# one: it is injected here, never defined in the module, and the module ships
# with ACCEPTED_RESPONSE_SHAPE = None so that no real run can reach APPLIED.
HYPOTHETICAL_SHAPE = {"updateItems": {}}


@pytest.fixture
def assuming_a_success_contract(monkeypatch):
    """Pretend, for one test, that every missing contract has been observed.

    Both gates, because a test that only lifted one would pass for the wrong
    reason the day the other is lifted for real.
    """
    monkeypatch.setattr(cart_mutate, "ACCEPTED_RESPONSE_SHAPE", HYPOTHETICAL_SHAPE)
    monkeypatch.setattr(cart_mutate, "PER_LINE_DELIVERY_PROVEN", True)
    return HYPOTHETICAL_SHAPE


CART_ID = "cart-id-canary-0000-not-a-real-cart"

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


# A GraphQL error can echo the variables that caused it, and those variables
# carry the cart id. Repeating the server's words would be a side door around
# every canary watching the front one.
def test_the_servers_own_error_text_is_never_repeated():
    leaky = {"errors": [{"message": f"invalid input cartId={CART_ID} for updateItems"}]}
    ack = cart_mutate.acknowledgement(200, leaky)
    assert CART_ID not in (ack.detail or "")
    assert ack.detail == cart_mutate.GENERIC_REJECTION


def test_a_stale_contract_is_still_detected_in_text_it_does_not_repeat():
    leaky = {"errors": [{"message": f"PersistedQueryNotFound cartId={CART_ID}"}]}
    ack = cart_mutate.acknowledgement(200, leaky)
    assert ack.reason == CONTRACT_STALE
    assert CART_ID not in (ack.detail or "")


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


# --- the success contract is unproven, and that fails closed ----------------------
#
# The first version of this accepted any non-empty `data` object, so
# {"data": {"anything": false}} was a success. That is a guess that fails open.
# Until a real response is captured there is no honest definition of accepted,
# and the module says so by shipping ACCEPTED_RESPONSE_SHAPE = None.


def test_the_success_contract_is_not_yet_known():
    """If this ever fails, a shape was filled in -- check it came from a capture."""
    assert cart_mutate.ACCEPTED_RESPONSE_SHAPE is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": None},
        {"data": {}},
        {"errors": []},
        {"data": {"anything": False}},
        {"data": {"updateItems": {"id": "x"}}},
        {"data": {"somethingElse": {"id": "x"}}},
    ],
)
def test_nothing_is_accepted_while_the_contract_is_unproven(payload):
    ack = cart_mutate.acknowledgement(200, payload)
    assert not ack.accepted
    assert ack.reason == cart_mutate.CONTRACT_UNPROVEN


def test_silence_is_not_agreement(assuming_a_success_contract):
    """Even given a contract, absence of the shape is not agreement."""
    for payload in ({}, {"data": None}, {"data": {}}, {"errors": []}):
        assert not cart_mutate.acknowledgement(200, payload).accepted


def test_an_unrelated_data_object_is_not_agreement(assuming_a_success_contract):
    assert not cart_mutate.acknowledgement(200, {"data": {"anything": False}}).accepted


def test_the_known_shape_is_accepted(assuming_a_success_contract):
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

# The options Ruby actually sends, read from the shared contract rather than
# imagined here. The first version of these tests used a bare store id string
# while Worker sends the whole store object; both suites passed and the guard
# would have refused every correctly configured request in production.
# test/contracts/README.md has the rule.
CONTRACT_PATH = Path(__file__).resolve().parent.parent / "test" / "contracts" / "worker_options.json"
OPTIONS = {k: v for k, v in json.loads(CONTRACT_PATH.read_text()).items() if not k.startswith("_")}
PINNED_STORE = OPTIONS["pinned_store"]
STORE = PINNED_STORE["store_id"]
ITEM_ID = "33282303"
DELIVERABLE = {"intent": "DELIVERY", "store_id": STORE}
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
                "fulfillment": {"intent": "DELIVERY", "storeId": STORE},
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

    def run(session, params, options=None):
        monkeypatch.setattr("browser.open_session", lambda _o: _nullcontext(session))
        events = []
        monkeypatch.setattr("protocol.progress", lambda text, **k: events.append(text))
        action = "cart.remove" if params.get("remove") else "cart.add"
        request = {"action": action, "params": params, "options": options or OPTIONS}
        return cart_mutate.action_cart_change(request), events

    return run


class _nullcontext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_exc):
        return False


def test_the_cart_id_is_sent_to_walmart_but_never_returned(mutation_run, assuming_a_success_contract):
    session = MutatingSession(before=1, after=2)
    result, events = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})

    # It really was used: the request could not work otherwise.
    assert session.sent[0]["body"]["variables"]["input"]["cartId"] == CART_ID
    # And it is in none of the three ways out.
    assert CART_ID not in json.dumps(result)
    assert CART_ID not in json.dumps(events)


def test_the_cart_id_is_absent_from_a_failed_run(mutation_run, capsys, assuming_a_success_contract):
    session = MutatingSession(before=1, after=1, status=500)
    result, events = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    captured = capsys.readouterr()
    assert result["outcome"] != APPLIED
    for text in (json.dumps(result), json.dumps(events), captured.err, captured.out):
        assert CART_ID not in text


def test_the_cart_id_is_absent_when_the_response_is_unreadable(
    mutation_run, capsys, assuming_a_success_contract
):
    session = MutatingSession(before=1, after=2, response="not json")
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert CART_ID not in json.dumps(result) + capsys.readouterr().err


def test_the_result_document_carries_no_unexpected_keys(mutation_run, assuming_a_success_contract):
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


def test_a_confirmed_increment_reports_applied(mutation_run, assuming_a_success_contract):
    session = MutatingSession(before=1, after=2)
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert result["outcome"] == APPLIED
    assert (result["quantity_before"], result["quantity_after"]) == (1, 2)
    assert result["changed"] is True


def test_the_quantity_sent_is_derived_from_the_read_not_the_request(
    mutation_run, assuming_a_success_contract
):
    """The delta is relative to what the cart holds now, not to anything cached."""
    session = MutatingSession(before=4, after=5)
    mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert session.sent[0]["body"]["variables"]["input"]["items"][0]["quantity"] == 5


def test_removal_sends_zero(mutation_run, assuming_a_success_contract):
    session = MutatingSession(before=2, after=0)
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "remove": True})
    assert session.sent[0]["body"]["variables"]["input"]["items"][0]["quantity"] == 0
    assert result["outcome"] == APPLIED


def test_a_cart_that_did_not_move_is_never_reported_as_applied(mutation_run, assuming_a_success_contract):
    session = MutatingSession(before=1, after=1)
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert result["outcome"] == UNVERIFIED
    assert result["changed"] is False


def test_a_marketplace_line_is_refused_before_anything_is_sent(
    mutation_run, monkeypatch, assuming_a_success_contract
):
    session = MutatingSession(before=1)
    monkeypatch.setattr(
        "cart_parse.parse_cart",
        lambda _p: {"lines": [dict(MARKETPLACE_LINE)], "totals": {}, "fulfillment": DELIVERABLE},
    )
    with pytest.raises(cart_mutate.CartMutationRefused) as raised:
        mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert "marketplace" in str(raised.value)
    assert session.sent == []


def test_an_item_not_in_the_cart_is_refused_before_anything_is_sent(
    mutation_run, assuming_a_success_contract
):
    session = MutatingSession(before=1)
    with pytest.raises(cart_mutate.CartMutationRefused):
        mutation_run(session, {"item_id": "not-here", "delta": 1})
    assert session.sent == []


def test_a_delta_of_zero_is_refused_as_a_usage_error(mutation_run, assuming_a_success_contract):
    """Not a no-op success: asking for no change is asking for nothing."""
    session = MutatingSession(before=2, after=2)
    with pytest.raises(ValueError):
        mutation_run(session, {"item_id": ITEM_ID, "delta": 0})
    assert session.sent == []


def test_removing_a_line_already_at_zero_sends_nothing(
    mutation_run, monkeypatch, assuming_a_success_contract
):
    """The one path where the target equals the current quantity."""
    session = MutatingSession(before=0, after=0)
    empty = dict(MARKETPLACE_LINE, quantity=0, seller_type="INTERNAL", seller_name="Walmart.com")
    monkeypatch.setattr(
        "cart_parse.parse_cart",
        lambda _p: {"lines": [empty], "totals": {}, "fulfillment": DELIVERABLE},
    )
    result, _ = mutation_run(session, {"item_id": ITEM_ID, "remove": True})
    assert session.sent == []
    assert result["changed"] is False


def test_the_cart_is_read_again_after_the_change(mutation_run, assuming_a_success_contract):
    session = MutatingSession(before=1, after=2)
    _, events = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert events == ["reading the cart", "updating the cart", "checking the cart"]


def test_an_unreadable_post_state_is_unverified_not_failed(
    mutation_run, monkeypatch, assuming_a_success_contract
):
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


# --- the readiness gate --------------------------------------------------------------


def test_a_change_refuses_before_opening_a_browser_while_unproven(monkeypatch):
    """Not merely fails: never starts. The gate is checked before the session."""
    opened = []
    monkeypatch.setattr("browser.open_session", lambda _o: opened.append(1))
    with pytest.raises(cart_mutate.CartMutationRefused) as raised:
        cart_mutate.action_cart_change(
            {"action": "cart.add", "params": {"item_id": ITEM_ID, "delta": 1}, "options": OPTIONS}
        )
    assert "not enabled yet" in str(raised.value)
    assert opened == []


def test_each_unmet_contract_is_named_separately(monkeypatch):
    """Two unknowns, two gates. Evidence for one is not evidence for the other."""
    assert len(cart_mutate.unmet_contracts()) == 2

    monkeypatch.setattr(cart_mutate, "ACCEPTED_RESPONSE_SHAPE", HYPOTHETICAL_SHAPE)
    remaining = cart_mutate.unmet_contracts()
    assert len(remaining) == 1
    assert "particular line can be delivered" in remaining[0]


def test_capturing_a_response_does_not_authorise_undelivered_lines(monkeypatch):
    """The failure mode the second gate exists to prevent."""
    monkeypatch.setattr(cart_mutate, "ACCEPTED_RESPONSE_SHAPE", HYPOTHETICAL_SHAPE)
    with pytest.raises(cart_mutate.CartMutationRefused):
        cart_mutate.assert_ready()


def test_per_line_delivery_is_still_unproven():
    """If this fails, someone proved it -- check what they observed."""
    assert cart_mutate.PER_LINE_DELIVERY_PROVEN is False


# --- store and delivery ---------------------------------------------------------------
#
# A change made against a pickup cart, or another store's cart, is a change to
# something nobody asked about. None of it can be inferred from silence, so a
# missing value refuses exactly as a wrong one does.

DELIVERY_CART = {"intent": "DELIVERY", "store_id": STORE}

BAD_CARTS = {
    "no store pinned": (DELIVERY_CART, None, "no store is pinned"),
    "cart names no store": ({"intent": "DELIVERY", "store_id": ""}, PINNED_STORE, "which store"),
    "a different store": (
        {"intent": "DELIVERY", "store_id": "1234"},
        PINNED_STORE,
        "belongs to store",
    ),
    "no intent at all": ({"intent": "", "store_id": STORE}, PINNED_STORE, "delivery or pickup"),
    "a pickup cart": (
        {"intent": "PICKUP", "store_id": STORE},
        PINNED_STORE,
        "only changes delivery",
    ),
    "shipping": (
        {"intent": "SHIPPING", "store_id": STORE},
        PINNED_STORE,
        "only changes delivery",
    ),
    "no fulfillment block": ({}, PINNED_STORE, "which store"),
    # The shapes a malformed config could produce. None may be accepted, and
    # none may be stringified into something that accidentally compares equal.
    "pinned store is a bare id": (DELIVERY_CART, STORE, "not a store object"),
    "pinned store is a list": (DELIVERY_CART, [STORE], "not a store object"),
    "pinned store has no id": (DELIVERY_CART, {"name": "Owasso"}, "no store_id"),
    "pinned store id is blank": (DELIVERY_CART, {"store_id": "  "}, "no store_id"),
    "pinned store id is null": (DELIVERY_CART, {"store_id": None}, "no store_id"),
}


@pytest.mark.parametrize("case", sorted(BAD_CARTS))
def test_a_cart_we_cannot_vouch_for_is_refused(case):
    fulfillment, pinned, expected = BAD_CARTS[case]
    with pytest.raises(cart_mutate.CartMutationRefused) as raised:
        cart_mutate.assert_deliverable_from_the_pinned_store({"fulfillment": fulfillment}, pinned)
    assert expected in str(raised.value), case


def test_the_pinned_delivery_cart_is_allowed():
    """The shape Ruby really sends, against the store it really names."""
    cart_mutate.assert_deliverable_from_the_pinned_store({"fulfillment": DELIVERABLE}, PINNED_STORE)


def test_the_guard_reads_the_store_id_and_not_the_whole_object():
    """The production bug: str(dict) never equals a store id."""
    with pytest.raises(cart_mutate.CartMutationRefused):
        cart_mutate.assert_deliverable_from_the_pinned_store(
            {"fulfillment": {"intent": "DELIVERY", "store_id": str(PINNED_STORE)}}, PINNED_STORE
        )


def test_the_store_guard_runs_before_anything_is_sent(mutation_run, assuming_a_success_contract):
    session = MutatingSession(before=1, after=2)
    with pytest.raises(cart_mutate.CartMutationRefused):
        mutation_run(session, {"item_id": ITEM_ID, "delta": 1}, options=dict(OPTIONS, pinned_store=None))
    assert session.sent == []


# --- ambiguity is not a tie to be broken ----------------------------------------------


def test_two_lines_for_one_item_is_refused_not_guessed():
    cart = {
        "lines": [
            {"item_id": ITEM_ID, "quantity": 1, "name": "one"},
            {"item_id": ITEM_ID, "quantity": 9, "name": "another"},
        ]
    }
    with pytest.raises(cart_mutate.CartMutationRefused) as raised:
        cart_mutate.find_line(cart, ITEM_ID)
    assert "will not guess" in str(raised.value)


@pytest.mark.parametrize("missing", ["item_id", "offer_id"])
def test_a_line_missing_an_identifier_is_refused(missing):
    line = {"item_id": ITEM_ID, "offer_id": "X"} | {missing: ""}
    with pytest.raises(cart_mutate.CartMutationRefused):
        cart_mutate.assert_line_is_addressable(line)


# --- the request is validated at the boundary that executes ---------------------------
#
# The protocol is directly invocable, so the worker validates the request
# rather than trusting that Ruby built it.

BAD_REQUESTS = {
    "remove as a string": ("cart.remove", {"item_id": ITEM_ID, "remove": "false"}),
    "remove as a number": ("cart.add", {"item_id": ITEM_ID, "remove": 1, "delta": 1}),
    "add asking to remove": ("cart.add", {"item_id": ITEM_ID, "remove": True}),
    "remove not asking to": ("cart.remove", {"item_id": ITEM_ID, "remove": False}),
    "removal with a delta": ("cart.remove", {"item_id": ITEM_ID, "remove": True, "delta": 2}),
    "delta as a string": ("cart.add", {"item_id": ITEM_ID, "delta": "2"}),
    "delta as a float": ("cart.add", {"item_id": ITEM_ID, "delta": 1.5}),
    "delta as a bool": ("cart.add", {"item_id": ITEM_ID, "delta": True}),
    "no delta at all": ("cart.add", {"item_id": ITEM_ID}),
    "delta of zero": ("cart.add", {"item_id": ITEM_ID, "delta": 0}),
    "no item": ("cart.add", {"delta": 1}),
    "blank item": ("cart.add", {"item_id": "   ", "delta": 1}),
    "params not an object": ("cart.add", []),
}


@pytest.mark.parametrize("case", sorted(BAD_REQUESTS))
def test_a_malformed_request_is_refused(case):
    action, params = BAD_REQUESTS[case]
    with pytest.raises(ValueError):
        cart_mutate._requested({"action": action, "params": params})


def test_the_string_false_is_not_a_removal():
    """bool("false") is True, which is how a typo deletes a cart line."""
    with pytest.raises(ValueError):
        cart_mutate._requested({"action": "cart.add", "params": {"item_id": "1", "remove": "false"}})


def test_a_well_formed_request_is_accepted():
    assert cart_mutate._requested({"action": "cart.add", "params": {"item_id": " 1 ", "delta": -2}}) == (
        "1",
        -2,
        False,
    )
    assert cart_mutate._requested({"action": "cart.remove", "params": {"item_id": "1", "remove": True}}) == (
        "1",
        None,
        True,
    )


# --- a failure before the send is not a maybe ------------------------------------------


def test_a_failure_before_sending_is_not_reported_as_unverified(
    mutation_run, monkeypatch, assuming_a_success_contract
):
    session = MutatingSession(before=1, after=2)
    monkeypatch.setattr("cart_parse.parse_cart", lambda _p: (_ for _ in ()).throw(RuntimeError("blocked")))
    with pytest.raises(cart_mutate.PreflightFailed):
        mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert session.sent == []


def test_a_preflight_failure_keeps_the_original_error_name(
    mutation_run, monkeypatch, assuming_a_success_contract
):
    blocked = RuntimeError("press and hold")
    blocked.code = "challenge_detected"
    monkeypatch.setattr("cart_parse.parse_cart", lambda _p: (_ for _ in ()).throw(blocked))
    with pytest.raises(cart_mutate.PreflightFailed) as raised:
        mutation_run(MutatingSession(before=1), {"item_id": ITEM_ID, "delta": 1})
    assert raised.value.code == "challenge_detected"


def test_a_failure_after_sending_stays_unverified(mutation_run, monkeypatch, assuming_a_success_contract):
    """The post-send half must not be converted into a clean failure."""
    session = MutatingSession(before=1, after=2)
    monkeypatch.setattr(
        cart_mutate, "acknowledgement", lambda *_a: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError) as raised:
        mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert not isinstance(raised.value, cart_mutate.PreflightFailed)
    assert session.sent != []


def test_a_server_error_echoing_the_cart_id_does_not_leak_it(
    mutation_run, capsys, assuming_a_success_contract
):
    """The leak that the original canaries could not have caught.

    They used errors that did not contain the id, so they proved only that
    happy-path values stay in. Walmart echoing the offending variables is the
    realistic way this escapes, and it reaches the result document through
    ack.detail -> message.
    """
    echoed = {"errors": [{"message": f"invalid variables: cartId={CART_ID}, quantity=2"}]}
    session = MutatingSession(before=1, after=1, response=echoed)
    result, events = mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    captured = capsys.readouterr()

    assert result["outcome"] == REJECTED
    for text in (json.dumps(result), json.dumps(events), captured.out, captured.err):
        assert CART_ID not in text


def test_a_safety_refusal_at_the_send_boundary_is_a_clean_failure(
    mutation_run, monkeypatch, assuming_a_success_contract
):
    """The phase boundary must sit after the last thing that can refuse.

    checked_destination validates the URL and the body's operation. When those
    lived inside send_update the caller had already flagged the attempt as
    begun, so a refusal -- nothing sent -- was reported as possibly-sent.
    """
    session = MutatingSession(before=1, after=2)
    monkeypatch.setattr(
        cart_mutate,
        "checked_destination",
        lambda _b: (_ for _ in ()).throw(safety.UnsafeNavigation("no")),
    )
    with pytest.raises(cart_mutate.PreflightFailed):
        mutation_run(session, {"item_id": ITEM_ID, "delta": 1})
    assert session.sent == []


def test_the_quantity_field_rejects_a_boolean():
    """bool subclasses int, so True would have incremented to 2."""
    assert cart_mutate.quantity_of({"lines": [{"item_id": "1", "quantity": True}]}, "1") is None


# --- what the refusal actually reads like -------------------------------------------
#
# The first version joined whole messages, so two unmet contracts produced one
# paragraph that said "changing the cart is not enabled yet" twice and buried
# the difference between them. Reported from a real run.


def test_the_heading_is_said_once_however_many_reasons_there_are():
    message = cart_mutate.not_ready_message(["first reason", "second reason"])
    assert message.count(cart_mutate.NOT_READY_PREFIX) == 1


def test_several_reasons_are_listed_not_run_together():
    message = cart_mutate.not_ready_message(["first reason", "second reason"])
    assert "\n  - first reason" in message
    assert "\n  - second reason" in message


def test_one_reason_stays_a_sentence():
    message = cart_mutate.not_ready_message(["the only reason"])
    assert message == f"{cart_mutate.NOT_READY_PREFIX}: the only reason. Nothing was sent."


def test_the_refusal_always_says_nothing_was_sent():
    """The one fact a person needs from a refusal."""
    for reasons in (["one"], ["one", "two"], cart_mutate.unmet_contracts()):
        assert "Nothing was sent." in cart_mutate.not_ready_message(reasons)
