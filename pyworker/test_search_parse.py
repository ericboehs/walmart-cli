"""Search response parsing, against synthetic payloads only.

Every payload here is written by hand from the schema reported by the live
capture. No live body is copied in, not even a scrubbed one: a parser test
needs a shape, and a shape is not personal data. When a real capture is
promoted to pyworker/fixtures/ these tests keep working, because they assert on
structure rather than on values.
"""

from __future__ import annotations

import json

import pytest

import search_parse


def product(**overrides):
    """One product entry, shaped like the observed itemsV2 element."""
    item = {
        "usItemId": "10450114",
        "offerId": "AB12CD34",
        "name": "Great Value Whole Milk, 1 Gallon",
        "sellerId": "F55CDC31AB",
        "sellerName": "Walmart.com",
        "sellerType": None,
        "availabilityStatus": "IN_STOCK",
        "availabilityStatusV2": {"display": "In stock", "value": "IN_STOCK"},
        "fulfillmentType": "STORE",
        "fulfillmentSummary": [
            {
                "fulfillment": "DELIVERY",
                "fulfillmentMethods": "SCHEDULED",
                "deliveryDate": "2026-08-27",
                "storeId": "0100",
            },
            {
                "fulfillment": "PICKUP",
                "fulfillmentMethods": "UNSCHEDULED",
                "deliveryDate": None,
                "storeId": "0100",
            },
        ],
        "priceInfo": {
            "priceDetails": {
                "currency": "USD",
                "priceLines": [
                    {
                        "lineType": "CURRENT_PRICE",
                        "price": {"PRICE": {"price": 3.12, "priceString": "$3.12"}},
                    },
                    {
                        "lineType": "COMPARISON",
                        "price": {"WAS_PRICE": {"price": 3.48, "priceString": "$3.48"}},
                    },
                    {
                        "lineType": "UNIT_PRICE",
                        "price": {"UNIT_PRICE": {"priceString": "2.4 ¢/fl oz"}},
                    },
                ],
            }
        },
        "salesUnit": "EACH",
        "salesUnitType": "EACH",
        "canonicalUrl": "/ip/Great-Value-Whole-Milk-1-Gallon/10450114",
        "badges": {},
        "sponsoredProduct": None,
        "imageInfo": {"thumbnailUrl": "https://i5.walmartimages.com/asr/x.jpeg"},
    }
    item.update(overrides)
    return item


def response(items=None, **result_overrides):
    result = {
        "itemStacks": [{"itemsV2": items if items is not None else [product()]}],
        "hasMorePages": True,
        "paginationV2": {"maxPage": 25},
        "pageProperties": {"page": "1", "query": "whole milk", "sort": "best_match"},
    }
    result.update(result_overrides)
    return {"data": {"search": {"searchResult": result}}}


# --- The happy path ---------------------------------------------------------


def test_a_product_is_flattened_into_an_offer():
    parsed = search_parse.parse_search(response())

    assert len(parsed["items"]) == 1
    item = parsed["items"][0]
    assert item["item_id"] == "10450114"
    assert item["offer_id"] == "AB12CD34"
    assert item["name"] == "Great Value Whole Milk, 1 Gallon"
    assert item["seller_name"] == "Walmart.com"
    assert item["availability"] == "IN_STOCK"


def test_prices_carry_both_a_number_and_the_rendered_string():
    # Formatting money from a float is how a display ends up saying $3.1.
    item = search_parse.parse_search(response())["items"][0]

    assert item["price"] == 3.12
    assert item["price_display"] == "$3.12"
    assert item["was_price"] == 3.48
    assert item["currency"] == "USD"


def test_the_unit_price_keeps_its_units():
    # "2.4" is not an answer to "how much per fluid ounce".
    item = search_parse.parse_search(response())["items"][0]

    assert item["unit_price_display"] == "2.4 ¢/fl oz"


def test_fulfillment_is_reduced_to_what_a_shopper_asks():
    item = search_parse.parse_search(response())["items"][0]

    assert item["delivery"] is True
    assert item["pickup"] is True
    assert item["delivery_date"] == "2026-08-27"
    assert item["store_id"] == "0100"


def test_pagination_is_reported():
    parsed = search_parse.parse_search(response())

    assert parsed["pagination"] == {
        "has_more": True,
        "max_page": 25,
        "page": 1,
        "query": "whole milk",
        "sort": "best_match",
    }


# --- Entries that are not products ------------------------------------------


def test_an_advertising_slot_is_not_an_offer():
    # Ads ride in the same array as products and have no usItemId. They are not
    # things you can buy, so they are not results.
    parsed = search_parse.parse_search(response([{"__typename": "AdPlaceholder"}, product()]))

    assert len(parsed["items"]) == 1


def test_an_entry_missing_an_item_id_is_dropped():
    parsed = search_parse.parse_search(response([product(usItemId=None)]))

    assert parsed["items"] == []


def test_multiple_stacks_are_concatenated():
    payload = response()
    payload["data"]["search"]["searchResult"]["itemStacks"] = [
        {"itemsV2": [product(usItemId="1")]},
        {"itemsV2": [product(usItemId="2")]},
    ]

    assert [item["item_id"] for item in search_parse.parse_search(payload)["items"]] == ["1", "2"]


# --- First-party filtering ---------------------------------------------------
#
# Observed: 38 Walmart.com STORE items and 12 marketplace items, with
# sellerType null on every one of them. So sellerType cannot be the test.


def test_a_walmart_store_item_is_first_party():
    assert search_parse.is_first_party(product()) is True


def test_a_marketplace_item_is_not():
    marketplace = product(sellerName="Food Service Direct", fulfillmentType="MARKETPLACE")

    assert search_parse.is_first_party(marketplace) is False


def test_a_walmart_sounding_marketplace_seller_is_not_first_party():
    # Both signals are required precisely because either one alone can lie.
    assert search_parse.is_first_party(product(sellerName="Walmart Restored")) is False


def test_walmart_branding_without_store_fulfillment_is_not_first_party():
    assert search_parse.is_first_party(product(fulfillmentType="MARKETPLACE")) is False


def test_the_flag_travels_with_the_item():
    marketplace = product(usItemId="2", sellerName="Food Service Direct", fulfillmentType="MARKETPLACE")
    items = search_parse.parse_search(response([product(), marketplace]))["items"]

    assert [item["first_party"] for item in items] == [True, False]


# --- Missing and malformed data ---------------------------------------------
#
# Walmart changes this shape without telling anyone. A search that returns 49 of
# 50 items with one price missing is worth far more than a stack trace.


def test_a_missing_price_block_is_none_not_a_crash():
    item = search_parse.parse_search(response([product(priceInfo=None)]))["items"][0]

    assert item["price"] is None
    assert item["price_display"] is None


def test_an_unexpected_price_line_shape_is_ignored():
    broken = product()
    broken["priceInfo"]["priceDetails"]["priceLines"] = ["nonsense", 42, None]

    assert search_parse.parse_search(response([broken]))["items"][0]["price"] is None


def test_a_missing_fulfillment_summary_reads_as_unknown_not_available():
    item = search_parse.parse_search(response([product(fulfillmentSummary=None)]))["items"][0]

    assert item["delivery"] is False
    assert item["pickup"] is False
    assert item["delivery_date"] is None


def test_an_empty_result_set_is_a_valid_answer():
    parsed = search_parse.parse_search(response([]))

    assert parsed["items"] == []
    assert parsed["pagination"]["query"] == "whole milk"


def test_a_response_without_the_graphql_envelope_still_parses():
    envelope = response()
    inner = envelope["data"]["search"]

    assert len(search_parse.parse_search(inner)["items"]) == 1


def test_garbage_input_parses_to_nothing():
    for payload in (None, [], "text", 42, {}, {"data": None}):
        parsed = search_parse.parse_search(payload)
        assert parsed["items"] == []


def test_a_string_price_is_read_as_a_number():
    odd = product()
    odd["priceInfo"]["priceDetails"]["priceLines"][0]["price"]["PRICE"]["price"] = "$1,234.50"

    assert search_parse.parse_search(response([odd]))["items"][0]["price"] == 1234.50


def test_availability_falls_back_to_the_v1_field():
    item = search_parse.parse_search(response([product(availabilityStatusV2=None)]))["items"][0]

    assert item["availability"] == "IN_STOCK"


def test_a_sponsored_product_is_flagged():
    item = search_parse.parse_search(response([product(sponsoredProduct={"clickBeacon": "x"})]))["items"][0]

    assert item["sponsored"] is True


def test_the_parser_never_returns_a_bare_object_as_a_string():
    # _string must refuse dicts, or a display ends up printing {'value': ...}.
    item = search_parse.parse_search(response([product(name={"value": "nested"})]))["items"][0]

    assert item["name"] is None


def test_parsing_is_pure_and_does_not_mutate_its_input():
    payload = response()
    before = json.dumps(payload, sort_keys=True)

    search_parse.parse_search(payload)

    assert json.dumps(payload, sort_keys=True) == before


@pytest.mark.parametrize("sort_key", ["best_match", "price_low", "price_high"])
def test_the_echoed_request_is_reported_verbatim(sort_key):
    payload = response()
    payload["data"]["search"]["searchResult"]["pageProperties"]["sort"] = sort_key

    assert search_parse.parse_search(payload)["pagination"]["sort"] == sort_key
