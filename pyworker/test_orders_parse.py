"""Order history parsing. Payloads written by hand from the observed schema."""

from __future__ import annotations

import pytest

import orders_parse

# --- Payloads -----------------------------------------------------------------


def history(orders=None, cursor="Y3Vyc29yOjU="):
    return {
        "props": {
            "pageProps": {
                "phRedesignInitialData": {
                    "data": {
                        "purchaseHistory": {
                            "orders": orders if orders is not None else [ONLINE_ROW, IN_STORE_ROW],
                            "pageInfo": {"nextPageCursor": cursor, "prevPageCursor": None},
                        }
                    }
                }
            }
        }
    }


ONLINE_ROW = {
    "id": "200012345678901",
    "displayId": "2000123-45678",
    "orderDate": "2026-08-20T14:02:00.000Z",
    "type": "GLASS",
    "isInStore": False,
    "itemCount": 12,
    "shortTitle": "Aug 20 order",
}

IN_STORE_ROW = {
    "id": "300098765432109",
    "displayId": "3000987-65432",
    "orderDate": "2026-08-18T19:40:00.000Z",
    "type": "IN_STORE",
    "isInStore": True,
    "itemCount": 4,
    "shortTitle": "In-store purchase",
}

ITEM = {
    "id": "item-1",
    "quantity": 2,
    "priceInfo": {"linePrice": {"value": 6.24, "displayValue": "$6.24"}},
    "productInfo": {
        "usItemId": "10450114",
        "offerId": "OFFER1",
        "name": "Great Value Whole Milk, 1 Gallon",
        "salesUnitType": "EACH",
        "imageInfo": {"thumbnailUrl": "https://i5.walmartimages.com/milk.jpg"},
    },
    "orderedSalesUnit": "EACH",
    "requestedWeight": None,
    "statusCode": "DELIVERED",
    "isUnavailable": False,
}


def order(groups_key="groups_2101", groups=None, **extra):
    payload = {
        "id": "200012345678901",
        "displayId": "2000123-45678",
        "orderDate": "2026-08-20T14:02:00.000Z",
        "shortTitle": "Aug 20 order",
        "itemCount": 2,
        "isInStore": False,
        "type": "GLASS",
        "priceDetails": {
            "subTotal": {"value": 41.18},
            "taxTotal": {"value": 1.24},
            "grandTotal": {"value": 44.32},
            "savings": {"value": 3.10},
        },
        **extra,
    }
    payload[groups_key] = (
        groups
        if groups is not None
        else [
            {
                "groupId": "g-1",
                "status": "DELIVERED",
                "deliveryType": "DELIVERY",
                "deliveredDate": "2026-08-20T18:11:00.000Z",
                "store": {"id": "1234", "name": "Bartlesville Supercenter"},
                "items": [ITEM],
            }
        ]
    )
    return {"props": {"pageProps": {"initialData": {"data": {"order": payload}}}}}


# --- The list is an index ------------------------------------------------------


def test_history_rows_carry_what_the_walk_needs():
    parsed = orders_parse.parse_history(history())

    assert [row["order_id"] for row in parsed["orders"]] == ["200012345678901", "300098765432109"]
    assert parsed["orders"][0]["date"] == "2026-08-20T14:02:00.000Z"


def test_in_store_receipts_ride_in_the_same_list():
    # The two-year archive and the receipt feature are one traversal, not two.
    parsed = orders_parse.parse_history(history())

    assert [row["type"] for row in parsed["orders"]] == ["online", "in-store"]


def test_the_index_does_not_claim_to_know_how_an_order_arrived():
    # The list row says GLASS, which means "came through the website". Reading
    # that as "delivery" is a guess with a one-in-three chance of being wrong;
    # the detail page is where the answer lives.
    assert orders_parse.parse_history(history())["orders"][0]["type"] == "online"


def test_the_cursor_is_carried_for_the_next_page():
    assert orders_parse.parse_history(history())["next_cursor"] == "Y3Vyc29yOjU="


def test_no_cursor_means_stop_rather_than_start_over():
    # An empty cursor asked for page one again on the live site. Treating empty
    # and absent alike is what keeps a walk from looping forever.
    assert orders_parse.parse_history(history(cursor=""))["next_cursor"] is None
    assert orders_parse.parse_history(history(cursor=None))["next_cursor"] is None


def test_a_row_without_an_id_is_dropped():
    parsed = orders_parse.parse_history(history(orders=[{"orderDate": "2026-01-01"}, ONLINE_ROW]))

    assert len(parsed["orders"]) == 1


def test_an_empty_history_is_an_empty_list():
    assert orders_parse.parse_history(history(orders=[]))["orders"] == []


def test_nonsense_does_not_crash_the_walk():
    assert orders_parse.parse_history("not a page")["orders"] == []


# --- Type normalization ---------------------------------------------------------


def test_glass_alone_is_not_a_fulfillment_type():
    # "GLASS" says the order came through the website, not how it arrived.
    assert orders_parse.normalize_type("GLASS", in_store=False, group_type="PICKUP") == "pickup"


def test_an_unrecognized_label_is_kept_verbatim():
    # Forcing an unknown label into the nearest bucket is a silent lie about
    # how something arrived.
    assert orders_parse.normalize_type("DRONE_DROP") == "DRONE_DROP"


def test_in_store_wins_because_a_boolean_cannot_be_ambiguous():
    assert orders_parse.normalize_type("GLASS", in_store=True) == "in-store"


# --- The record ------------------------------------------------------------------


def test_an_order_is_read_into_an_archive_record():
    record = orders_parse.parse_order(order())

    assert record["order_id"] == "200012345678901"
    assert record["type"] == "delivery"
    assert [item["item_id"] for item in record["items"]] == ["10450114"]


def test_the_groups_key_is_found_by_prefix_not_by_the_suffix_we_saw():
    # It was groups_2101 on the order we read. Hardcoding that number would
    # archive an empty order for everyone else.
    record = orders_parse.parse_order(order(groups_key="groups_5"))

    assert len(record["items"]) == 1


def test_every_group_is_read_not_just_the_first():
    # An order split across two fulfillments would otherwise archive half its
    # items and look complete.
    payload = order()
    inner = payload["props"]["pageProps"]["initialData"]["data"]["order"]
    inner["groups_9"] = [{"groupId": "g-2", "deliveryType": "PICKUP", "items": [ITEM]}]

    record = orders_parse.parse_order(payload)

    assert len(record["groups"]) == 2
    assert len(record["items"]) == 2


def test_item_join_keys_survive_because_titles_are_not_join_keys():
    item = orders_parse.parse_order(order())["items"][0]

    assert item["item_id"] == "10450114"
    assert item["offer_id"] == "OFFER1"
    assert item["line_price"] == 6.24


def test_an_item_with_no_ids_is_dropped():
    payload = order(groups=[{"groupId": "g", "items": [{"quantity": 1, "productInfo": {"name": "Mystery"}}]}])

    assert orders_parse.parse_order(payload)["items"] == []


def test_totals_are_copied_under_walmarts_own_names():
    totals = orders_parse.parse_order(order())["totals"]

    assert totals["sub_total"] == 41.18
    assert totals["grand_total"] == 44.32
    assert totals["savings"] == 3.10


def test_a_missing_total_is_absent_rather_than_computed():
    # A total we derived is not the total that was charged.
    payload = order()
    del payload["props"]["pageProps"]["initialData"]["data"]["order"]["priceDetails"]["grandTotal"]

    totals = orders_parse.parse_order(payload)["totals"]

    assert "grand_total" not in totals
    assert totals["sub_total"] == 41.18


def test_store_identity_is_kept_but_nothing_about_where_it_went():
    group = orders_parse.parse_order(order())["groups"][0]

    assert group["store_id"] == "1234"
    assert group["store_name"] == "Bartlesville Supercenter"


def test_a_page_without_an_order_is_refused():
    with pytest.raises(ValueError):
        orders_parse.parse_order({"props": {"pageProps": {}}})


# --- Redaction ---------------------------------------------------------------------

SENSITIVE = (
    "paymentMethods",
    "refundPaymentMethods",
    "chargeHistory",
    "authorizationAmount",
    "customer",
    "idBarcodeImageUrl",
    "orderInvoiceDetails",
    "digitalInvoiceDetail",
    "deliveryAddress",
    "deliveryInstructions",
    "alternatePickupPerson",
    "callDriverDetails",
    "checkInDetails",
    "accessPointId",
    "driver",
    "shopper",
    "pickedBy",
)


def loaded_order():
    """An order carrying every field we must never archive."""
    payload = order(
        paymentMethods=[{"cardType": "VISA", "lastFour": "4242"}],
        refundPaymentMethods=[{"cardType": "VISA"}],
        chargeHistory=[{"amount": 44.32}],
        authorizationAmount={"value": 50.00},
        customer={"firstName": "Eric", "email": "someone@example.com"},
        idBarcodeImageUrl="https://walmart.com/barcode/abc123.png",
        orderInvoiceDetails={"invoiceId": "INV-1"},
        digitalInvoiceDetail={"url": "https://example.com/invoice"},
    )
    inner = payload["props"]["pageProps"]["initialData"]["data"]["order"]
    inner["groups_2101"][0].update(
        {
            # The nested ones are the point: a shallow check misses these.
            "deliveryAddress": {"addressLineOne": "1 Farm Rd", "postalCode": "74003"},
            "deliveryInstructions": "Gate code 1234",
            "alternatePickupPerson": {"firstName": "Ashley"},
            "callDriverDetails": {"phone": "555-0100"},
            "checkInDetails": {"vehicle": "blue truck"},
            "accessPointId": "AP-9",
            "driver": {"name": "Sam"},
            "shopper": {"name": "Pat"},
            "pickedBy": {"name": "Pat"},
        }
    )
    return payload


def deep_keys(node, found=None):
    """Every key name anywhere in the structure."""
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            deep_keys(value, found)
    elif isinstance(node, list):
        for entry in node:
            deep_keys(entry, found)
    return found


def test_no_forbidden_key_survives_at_any_depth():
    record = orders_parse.parse_order(loaded_order())

    present = deep_keys(record)
    assert not [key for key in SENSITIVE if key in present]


def test_no_forbidden_value_survives_either():
    # Keys can be renamed by a later refactor; the values are what actually
    # hurt if they reach disk.
    text = repr(orders_parse.parse_order(loaded_order()))

    for secret in ("4242", "someone@example.com", "1 Farm Rd", "74003", "Gate code", "555-0100", "Ashley"):
        assert secret not in text


def test_redaction_reaches_inside_lists():
    payload = {"groups": [{"deliveryAddress": {"postalCode": "74003"}, "keep": "yes"}]}

    cleaned = orders_parse.redact(payload)

    assert cleaned == {"groups": [{"keep": "yes"}]}


def test_redaction_matches_key_spelling_variants():
    # delivery_address and DeliveryAddress are the same field wearing a hat.
    cleaned = orders_parse.redact({"delivery_address": 1, "DeliveryAddress": 2, "keep": 3})

    assert cleaned == {"keep": 3}


def test_the_order_still_archives_everything_it_should():
    # Redaction that also removed the groceries would pass every test above.
    record = orders_parse.parse_order(loaded_order())

    assert record["items"][0]["name"] == "Great Value Whole Milk, 1 Gallon"
    assert record["totals"]["grand_total"] == 44.32
    assert record["groups"][0]["store_id"] == "1234"
