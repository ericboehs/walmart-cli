"""Cart parsing. Payloads here are written by hand from the observed schema."""

from __future__ import annotations

import pytest

import cart_parse

# Two lines, because the interesting bugs live in the difference between them:
# one countable first-party item and one weighted-produce item from a
# marketplace seller.
MILK = {
    "id": "line-1",
    "quantity": 2,
    "quantityString": "2 ct",
    "priceInfo": {
        "itemPrice": {"value": 3.12, "displayValue": "$3.12"},
        "linePrice": {"value": 6.24, "displayValue": "$6.24"},
        "wasPrice": {"value": 3.48, "displayValue": "$3.48"},
        "unitPrice": {"value": 0.024, "displayValue": "2.4 ¢/fl oz"},
    },
    "product": {
        "usItemId": "10450114",
        "offerId": "OFFER1",
        "name": "Great Value Whole Milk, 1 Gallon",
        "brand": "Great Value",
        "sellerName": "Walmart.com",
        "sellerDisplayName": "Walmart",
        "sellerType": "INTERNAL",
        "availabilityStatus": "IN_STOCK",
        "fulfillmentType": "STORE",
        "fulfillmentTitle": "Delivery",
        "salesUnitType": "EACH",
        "orderLimit": 12,
        "orderMinLimit": 1,
        "isSubstitutionEligible": True,
        "isAlcohol": False,
        "snapEligible": True,
        "rhPath": "/ip/10450114",
    },
}

BANANAS = {
    "id": "line-2",
    "quantity": 2.5,
    "quantityString": "2.5 lb",
    "priceInfo": {
        "itemPrice": {"value": 0.58, "displayValue": "$0.58"},
        "linePrice": {"value": 1.45, "displayValue": "$1.45"},
        "unitPrice": {"value": 0.58, "displayValue": "$0.58/lb"},
    },
    "product": {
        "usItemId": "44390948",
        "name": "Fresh Banana Fruit, each",
        "sellerName": "Walmart.com",
        "availabilityStatus": "IN_STOCK",
        "fulfillmentType": "STORE",
        "salesUnitType": "LB",
        "weightIncrement": 0.1,
        "orderLimit": 20,
    },
}


def cart(lines=None, **extra):
    payload = {
        "data": {
            "cart": {
                "id": "SESSION-CART-UUID",
                "lineItems": lines if lines is not None else [MILK, BANANAS],
                "priceDetails": {
                    "subTotal": {"value": 7.69, "displayValue": "$7.69"},
                    "grandTotal": {"value": 8.34, "displayValue": "$8.34"},
                    "totalSavings": {"value": 0.72, "displayValue": "$0.72"},
                },
                "fulfillment": {"intent": "DELIVERY", "storeId": "1234"},
                **extra,
            }
        }
    }
    return payload


# --- Lines --------------------------------------------------------------------


def test_lines_are_flattened_in_order():
    parsed = cart_parse.parse_cart(cart())

    assert [line["item_id"] for line in parsed["lines"]] == ["10450114", "44390948"]
    assert parsed["line_count"] == 2


def test_the_cart_id_is_never_returned():
    # It is a session identifier and nothing printed needs it. What is never
    # carried cannot leak.
    parsed = cart_parse.parse_cart(cart())

    assert "id" not in parsed
    assert "SESSION-CART-UUID" not in repr(parsed)


def test_quantities_keep_their_fractions():
    # Rounding 2.5 lb of bananas to 2 misreports the cart.
    parsed = cart_parse.parse_cart(cart())

    assert parsed["lines"][1]["quantity"] == 2.5
    assert parsed["item_count"] == 4.5


def test_walmarts_own_quantity_label_is_kept():
    # Rebuilding "2.5 lb" from the number and the unit is how a display ends up
    # saying "2.5 EACH".
    parsed = cart_parse.parse_cart(cart())

    assert parsed["lines"][1]["quantity_label"] == "2.5 lb"


def test_line_price_and_item_price_are_both_kept():
    # One is what the item costs, the other is what this line costs. Showing
    # the wrong one makes a two-gallon line look like a bargain.
    line = cart_parse.parse_cart(cart())["lines"][0]

    assert line["item_price"] == 3.12
    assert line["line_price"] == 6.24


def test_unit_price_keeps_its_units():
    line = cart_parse.parse_cart(cart())["lines"][0]

    assert line["unit_price_display"] == "2.4 ¢/fl oz"


def test_quantity_limits_are_read_not_assumed():
    # A tool that guesses the ceiling finds it by being refused; one that
    # guesses the floor can order more than someone asked for.
    line = cart_parse.parse_cart(cart())["lines"][0]

    assert line["max_quantity"] == 12
    assert line["min_quantity"] == 1


def test_an_absent_limit_is_unknown_rather_than_unlimited():
    line = cart_parse.parse_cart(cart())["lines"][1]

    assert line["max_quantity"] == 20
    assert line["min_quantity"] is None


def test_a_line_without_a_product_is_dropped():
    parsed = cart_parse.parse_cart(cart(lines=[{"id": "empty"}, MILK]))

    assert [line["item_id"] for line in parsed["lines"]] == ["10450114"]


def test_junk_in_the_line_list_does_not_crash_the_read():
    parsed = cart_parse.parse_cart(cart(lines=[None, "nonsense", MILK]))

    assert parsed["line_count"] == 1


def test_a_missing_price_block_leaves_prices_unknown():
    bare = {"id": "x", "quantity": 1, "product": {"usItemId": "9", "name": "Mystery"}}

    line = cart_parse.parse_cart(cart(lines=[bare]))["lines"][0]

    assert line["line_price"] is None
    assert line["name"] == "Mystery"


# --- Totals -------------------------------------------------------------------


def test_totals_are_read_by_walmarts_names():
    totals = cart_parse.parse_cart(cart())["totals"]

    assert totals["subtotal"] == 7.69
    assert totals["total"] == 8.34
    assert totals["savings"] == 0.72
    assert totals["total_display"] == "$8.34"


def test_a_missing_total_is_none_and_never_derived():
    # A total we computed is not the total Walmart will charge. Summing the
    # lines would produce a confident number that is wrong about tax and fees.
    payload = cart()
    del payload["data"]["cart"]["priceDetails"]["grandTotal"]

    totals = cart_parse.parse_cart(payload)["totals"]

    assert totals["total"] is None
    assert totals["subtotal"] == 7.69


def test_an_empty_cart_parses_to_an_empty_cart():
    parsed = cart_parse.parse_cart(cart(lines=[]))

    assert parsed["lines"] == []
    assert parsed["item_count"] == 0


# --- Problems -----------------------------------------------------------------


def test_walmarts_complaints_are_surfaced():
    # A cart with an unavailable line still renders. Silence here would make a
    # broken cart look fine, and the shopper would find out at delivery.
    parsed = cart_parse.parse_cart(
        cart(
            errorLineItems=[{"message": "Sold out: Great Value Whole Milk"}],
            operationalErrors=[{"code": "SLOT_EXPIRED"}],
        )
    )

    assert parsed["problems"] == ["Sold out: Great Value Whole Milk", "SLOT_EXPIRED"]


def test_a_healthy_cart_has_no_problems():
    assert cart_parse.parse_cart(cart())["problems"] == []


# --- Envelope tolerance --------------------------------------------------------


def test_a_null_cart_is_unreadable_not_empty():
    with pytest.raises(cart_parse.CartUnreadable):
        cart_parse.parse_cart({"data": {"cart": None}})


def test_a_cart_without_a_line_list_is_unreadable():
    # Unobserved shape: we have never seen an empty cart's response, so the
    # ambiguous case resolves to "I could not read it" rather than "you have
    # nothing". Reporting an unreadable cart as empty is how someone shops twice.
    payload = cart()
    del payload["data"]["cart"]["lineItems"]

    with pytest.raises(cart_parse.CartUnreadable):
        cart_parse.parse_cart(payload)


def test_a_line_list_that_is_not_a_list_is_unreadable():
    with pytest.raises(cart_parse.CartUnreadable):
        cart_parse.parse_cart({"data": {"cart": {"lineItems": "soon"}}})


def test_the_graphql_envelope_is_optional():
    inner = cart()["data"]["cart"]

    assert cart_parse.parse_cart(inner)["line_count"] == 2


def test_nonsense_is_unreadable_rather_than_empty():
    with pytest.raises(cart_parse.CartUnreadable):
        cart_parse.parse_cart("not a cart")


def test_fulfillment_context_is_kept():
    fulfillment = cart_parse.parse_cart(cart())["fulfillment"]

    assert fulfillment["intent"] == "DELIVERY"
    assert fulfillment["store_id"] == "1234"
