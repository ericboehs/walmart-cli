"""Safety invariant 1, from the Python side.

If any of these fail, the worker must not run: they are the reason browser
automation cannot wander into an order-submitting flow.
"""

from __future__ import annotations

import pytest

import safety


@pytest.mark.parametrize(
    "url",
    [
        "https://www.walmart.com/checkout",
        "https://www.walmart.com/cart/checkout?tab=1",
        "https://www.walmart.com/payment/methods",
        "https://www.walmart.com/order-confirmation/12345",
        "HTTPS://WWW.WALMART.COM/CHECKOUT",
    ],
)
def test_forbidden_urls_are_refused(url):
    assert safety.is_forbidden_url(url)
    with pytest.raises(safety.UnsafeNavigation):
        safety.assert_navigable(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.walmart.com/cart",
        "https://www.walmart.com/ip/Great-Value-Whole-Milk/10450114",
        "https://www.walmart.com/account/orders",
        "https://www.walmart.com/search?q=milk",
    ],
)
def test_ordinary_urls_are_allowed(url):
    assert not safety.is_forbidden_url(url)
    assert safety.assert_navigable(url) == url


@pytest.mark.parametrize(
    "url",
    [
        # The browser decodes before it resolves, so a guard reading the raw
        # string is not looking at the destination the browser will reach.
        "https://www.walmart.com/%63heckout",
        "https://www.walmart.com/orders/..%2F..%2Fcheckout",
        # %252f decodes to %2f, which decodes to a separator.
        "https://www.walmart.com/orders/..%252F..%252Fcheckout",
        "https://www.walmart.com/%70ayment/methods",
        # A backslash resolves as a path separator too.
        "https://www.walmart.com/orders\\..\\checkout",
        "https://www.walmart.com/PLACE%2DORDER",
    ],
)
def test_an_encoded_forbidden_destination_is_still_forbidden(url):
    assert safety.is_forbidden_url(url)
    with pytest.raises(safety.UnsafeNavigation):
        safety.assert_navigable(url)


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://www.walmart.com/orders", True),
        ("https://walmart.com/orders", True),
        ("https://sub.walmart.com/orders", True),
        ("https://WWW.WALMART.COM/orders", True),
        # Every interesting way to fake a host is a string containing the real
        # one, which is why this is parsed rather than matched.
        ("https://www.walmart.com.evil.example/orders", False),
        ("https://www.walmart.com@evil.example/orders", False),
        ("https://evil.example/orders?x=www.walmart.com", False),
        ("https://notwalmart.com/orders", False),
        ("file:///etc/passwd", False),
        ("javascript:alert(1)", False),
    ],
)
def test_navigation_is_confined_to_walmart(url, allowed):
    assert safety.is_allowed_host(url) is allowed
    if allowed:
        assert safety.assert_navigable(url) == url
    else:
        with pytest.raises(safety.UnsafeNavigation):
            safety.assert_navigable(url)


def test_an_ordinary_word_in_a_query_is_not_a_destination():
    # Decoding the whole URL means query strings are read too, and shoppers do
    # search for checkout counters. The fragments all begin with a separator
    # precisely so a word in a query is not mistaken for a path.
    assert safety.assert_navigable("https://www.walmart.com/search?q=checkout+organizer")


def test_a_query_that_encodes_a_path_is_refused_anyway():
    # This is deliberate over-blocking: nobody is shopping for "../checkout",
    # and a refused search costs a retry while the alternative costs an order.
    with pytest.raises(safety.UnsafeNavigation):
        safety.assert_navigable("https://www.walmart.com/search?q=..%2Fcheckout")


@pytest.mark.parametrize(
    "label",
    [
        "Check out",
        "Checkout",
        "Place order",
        "Continue to checkout",
        "Continue to payment",
        "Reserve a time",
        "Reserve slot",
        "Buy now",
        "Submit order",
        "Pay now",
    ],
)
def test_forbidden_controls_are_refused(label):
    assert safety.is_forbidden_control(label)
    with pytest.raises(safety.UnsafeNavigation):
        safety.assert_clickable(label)


@pytest.mark.parametrize(
    "label",
    ["Add to cart", "Increase quantity", "Remove", "Sign in", "My Items", None],
)
def test_ordinary_controls_are_allowed(label):
    assert not safety.is_forbidden_control(label)
    safety.assert_clickable(label)


@pytest.mark.parametrize("text", ["place_order", "place-order", "placeOrder", "PLACE ORDER", "check.out"])
def test_separator_styles_cannot_smuggle_a_term(text):
    assert safety.violations(text)


def test_unrelated_text_is_clean():
    assert safety.violations("add milk to the cart") == []


# --- order-flow operations ---------------------------------------------------------

# Observed, not imagined. A capture taken while a human kept clicking recorded
# three of these, and the URL guard could not see any of them: Walmart's order
# flow rides the same /orchestra/<app>/graphql/<Operation>/<hash> shape as an
# ordinary product read, so the URL carries none of the forbidden fragments.
ORDER_FLOW_URLS = (
    "https://www.walmart.com/orchestra/cartxo/graphql/reserveSlotMutation/deadbeef",
    "https://www.walmart.com/orchestra/cartxo/graphql/CreateContract/deadbeef",
    "https://www.walmart.com/orchestra/cartxo/graphql/updateTipping/deadbeef",
)

ORDINARY_URLS = (
    "https://www.walmart.com/orchestra/cartxo/graphql/updateItems/deadbeef",
    "https://www.walmart.com/orchestra/home/graphql/MergeAndGetCart/deadbeef",
    "https://www.walmart.com/orchestra/pdp/graphql/ItemByIdBtf/deadbeef/ip/123",
)


def test_the_url_guard_cannot_see_order_flow_operations():
    """The gap this exists to close, pinned so nobody removes the new guard."""
    for url in ORDER_FLOW_URLS:
        assert not safety.is_forbidden_url(url)


def test_order_flow_operations_are_refused():
    for url in ORDER_FLOW_URLS:
        assert safety.is_forbidden_operation(safety.operation_of(url)), url


def test_ordinary_operations_are_not_refused():
    for url in ORDINARY_URLS:
        assert not safety.is_forbidden_operation(safety.operation_of(url)), url


def test_a_suffix_cannot_defeat_the_match():
    """Walmart's own naming appends "Mutation"; an exact match would miss it."""
    assert safety.is_forbidden_operation("reserveSlotMutation")
    assert safety.is_forbidden_operation("RESERVE_SLOT")


def test_operation_of_reads_the_path_not_the_query():
    smuggled = "https://www.walmart.com/orchestra/x/graphql/updateItems/h?q=/graphql/CreateContract"
    assert safety.operation_of(smuggled) == "updateItems"


def test_operation_of_is_total():
    for value in (None, "", "https://www.walmart.com/", "not a url", "https://x/graphql"):
        assert safety.operation_of(value) is None or isinstance(safety.operation_of(value), str)


def test_no_operation_means_no_refusal():
    assert not safety.is_forbidden_operation(None)
    assert not safety.is_forbidden_operation("")


# --- the four holes a review found in the first version -----------------------------
#
# Each of these was ALLOWED by assert_navigable when the operation guard was
# first written. They are kept as one table so the next person can see that the
# failure was a class, not four accidents: a new guard has to inherit every
# discipline the old one already had.

REFUSED_URLS = {
    # Decoded first, bounded, exactly as the URL guard has always done.
    "percent-encoded first letter": (
        "https://www.walmart.com/orchestra/cartxo/graphql/%72eserveSlotMutation/h"
    ),
    "double-encoded": ("https://www.walmart.com/orchestra/cartxo/graphql/%2572eserveSlotMutation/h"),
    # The marker was matched case-sensitively, so this returned no operation at
    # all and the whole check was skipped.
    "mixed-case marker": "https://www.walmart.com/orchestra/cartxo/GraphQL/CreateContract/h",
    "upper-case marker": "https://www.walmart.com/orchestra/cartxo/GRAPHQL/CreateContract/h",
    # Recognised by is_forbidden_operation and allowed by assert_navigable: the
    # guard existed and was not on the path that executes.
    "plain forbidden operation": ("https://www.walmart.com/orchestra/cartxo/graphql/CreateContract/h"),
    "suffixed name": "https://www.walmart.com/orchestra/cartxo/graphql/reserveSlotMutation/h",
    # Was refused only because "/placeorder" happens to appear in the path.
    # Now refused by name as well, so the overlap is deliberate.
    "an order operation the list did not name": (
        "https://www.walmart.com/orchestra/cartxo/graphql/placeOrderMutation/h"
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSED_URLS))
def test_the_navigation_boundary_refuses_order_operations(case):
    with pytest.raises(safety.UnsafeNavigation):
        safety.assert_navigable(REFUSED_URLS[case])


def test_ordinary_operations_still_pass_the_boundary():
    for url in ORDINARY_URLS:
        assert safety.assert_navigable(url) == url


# placeOrderMutation was caught by accidental overlap between two lists. Sharing
# FORBIDDEN_TERMS makes it deliberate.
def test_the_operation_list_inherits_every_forbidden_term():
    for term in safety.FORBIDDEN_TERMS:
        assert term in safety.FORBIDDEN_OPERATIONS


def test_operation_of_decodes_before_reading():
    url = "https://www.walmart.com/orchestra/x/graphql/%72eserveSlotMutation/h"
    assert safety.operation_of(url) == "reserveSlotMutation"


def test_operation_of_is_total_over_junk():
    for value in (None, "", 7, [], "not a url", "http://[", "https://x/graphql"):
        assert safety.operation_of(value) is None


# --- the body must name what the path names ----------------------------------------


def test_a_body_naming_another_operation_is_a_mismatch():
    url = "https://www.walmart.com/orchestra/x/graphql/updateItems/h"
    assert safety.operation_mismatch(url, {"operationName": "CreateContract"}) == "CreateContract"


def test_agreement_is_not_a_mismatch():
    url = "https://www.walmart.com/orchestra/x/graphql/updateItems/h"
    assert safety.operation_mismatch(url, {"operationName": "updateItems"}) is None


def test_a_body_that_names_nothing_is_not_a_mismatch():
    url = "https://www.walmart.com/orchestra/x/graphql/updateItems/h"
    for body in (None, {}, [], "text", {"operationName": ""}, {"operationName": 7}):
        assert safety.operation_mismatch(url, body) is None
