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
