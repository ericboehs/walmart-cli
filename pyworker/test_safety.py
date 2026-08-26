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
