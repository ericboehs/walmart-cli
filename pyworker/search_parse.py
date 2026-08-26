"""Normalizing a Walmart search response into the offers the CLI displays.

Shape observed live (search recon, 2026-08-26), one 200 response:

    data.search.searchResult.itemStacks[].itemsV2[]

Each entry is a product or an advertising slot; the ad-ish entries have no
usItemId, which is how they are told apart. Product fields seen: usItemId,
offerId, name, sellerId, sellerName, availabilityStatus and
availabilityStatusV2.{display,value}, priceInfo.priceDetails.priceLines[] with
each line carrying lineType and values[] of {key, value} for PRICE, WAS_PRICE,
and UNIT_PRICE, fulfillmentType, fulfillmentSummary[], salesUnit,
salesUnitType, imageInfo, canonicalUrl, badges, sponsoredProduct. Pagination
lives beside the stacks as hasMorePages and paginationV2.maxPage, with the
echoed request in pageProperties.

Everything here is a pure function over decoded JSON. Nothing in this module
touches a browser, the network, or the filesystem, so the parser can be tested
exhaustively against synthetic payloads.

Design rules, in order of importance:

1. A field we cannot find is None, never a guess and never a crash. Walmart
   changes this shape without telling anyone, and a search that returns 49 of
   50 items with one price missing is far better than a stack trace.
2. Prices are reported as both a number and the string Walmart rendered.
   Formatting money from a float is how a display ends up saying $3.1.
3. First-party filtering is deliberately conservative: see is_first_party.
"""

from __future__ import annotations

from typing import Any

# Price line labels observed live. lineType says what the line is for, and the
# value dict is keyed by these names.
CURRENT_PRICE_KEYS = ("PRICE", "CURRENT_PRICE")
WAS_PRICE_KEYS = ("WAS_PRICE", "COMPARISON", "LIST_PRICE")
UNIT_PRICE_KEYS = ("UNIT_PRICE",)

# Seller and fulfillment values that mean "sold and shipped by Walmart itself".
WALMART_SELLER_NAMES = ("walmart.com", "walmart")
FIRST_PARTY_FULFILLMENT = "STORE"


def parse_search(payload: Any) -> dict[str, Any]:
    """Turn a decoded search response into items plus pagination.

    Accepts the whole response (with or without the GraphQL ``data`` envelope)
    so a caller does not have to know how deeply Walmart nested it this month.
    """
    result = _search_result(payload)
    items = [normalize_item(raw) for raw in _raw_items(result)]
    return {
        "items": [item for item in items if item is not None],
        "pagination": _pagination(result),
    }


def _search_result(payload: Any) -> dict[str, Any]:
    node = payload if isinstance(payload, dict) else {}
    for key in ("data", "search", "searchResult"):
        if isinstance(node, dict) and isinstance(node.get(key), dict):
            node = node[key]
    return node if isinstance(node, dict) else {}


def _raw_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    stacks = result.get("itemStacks")
    if not isinstance(stacks, list):
        return []
    items: list[dict[str, Any]] = []
    for stack in stacks:
        if not isinstance(stack, dict):
            continue
        for entry in stack.get("itemsV2") or stack.get("items") or []:
            if isinstance(entry, dict):
                items.append(entry)
    return items


def normalize_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One offer, flattened. None for entries that are not products.

    Advertising slots ride in the same array as products and carry no
    usItemId. Returning None for them keeps "what can I buy" separate from
    "what is Walmart showing me".
    """
    item_id = _string(raw.get("usItemId"))
    if not item_id:
        return None

    prices = _prices(raw)
    fulfillment = _fulfillment(raw)
    return {
        "item_id": item_id,
        "offer_id": _string(raw.get("offerId")),
        "name": _string(raw.get("name")),
        "seller_id": _string(raw.get("sellerId")),
        "seller_name": _string(raw.get("sellerName")),
        "availability": _availability(raw),
        "price": prices["current"] if prices["current"] is not None else _number(raw.get("price")),
        "price_display": prices["current_display"],
        "was_price": prices["was"],
        "unit_price_display": prices["unit_display"],
        "currency": prices["currency"],
        "sales_unit": _string(raw.get("salesUnitType") or raw.get("salesUnit")),
        "fulfillment_type": _string(raw.get("fulfillmentType")),
        "delivery": fulfillment["delivery"],
        "pickup": fulfillment["pickup"],
        "delivery_date": fulfillment["delivery_date"],
        "store_id": fulfillment["store_id"],
        "sponsored": bool(raw.get("sponsoredProduct")),
        "url": _string(raw.get("canonicalUrl")),
        "first_party": is_first_party(raw),
        # Present on the server-rendered payload, absent from the GraphQL one.
        # None means "this response did not say", which is different from False:
        # a shopper told "cannot add" about an item nobody asked about is worse
        # served than one told nothing.
        "can_add": _bool_or_none(raw.get("canAddToCart")),
        "out_of_stock": _bool_or_none(raw.get("isOutOfStock")),
        "snap_eligible": _bool_or_none(raw.get("snapEligible")),
        # Weighted produce: you order 2 lb of bananas, not 2 bananas. The signal
        # is averageWeight being present at all, which is why it is read as
        # "is there a number here" rather than as a truthy value.
        "weighted": _number(raw.get("averageWeight")) is not None,
        "average_weight": _number(raw.get("averageWeight")),
        "weight_increment": _number(raw.get("weightIncrement")),
    }


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def is_first_party(raw: dict[str, Any]) -> bool:
    """Sold by Walmart itself, checked two independent ways.

    sellerType was null on every item in the observed response, so it cannot be
    the test. Name and fulfillment type are both checked because either alone
    is a single point of failure: a marketplace seller could be named
    "Walmart Restored", and STORE fulfillment could in principle appear for a
    third party. Requiring both is defense in depth, and the cost of being
    wrong in this direction is only that an item is offered as marketplace.
    """
    seller = (_string(raw.get("sellerName")) or "").strip().lower()
    fulfillment = (_string(raw.get("fulfillmentType")) or "").strip().upper()
    return seller in WALMART_SELLER_NAMES and fulfillment == FIRST_PARTY_FULFILLMENT


def _availability(raw: dict[str, Any]) -> str | None:
    """The availability enum, preferring V2's value over its display text.

    An unrecognized string is passed through as itself rather than being
    flattened to "out of stock": we do not know what a new enum means, and
    guessing the pessimistic answer hides items a shopper could have bought.
    """
    v2 = raw.get("availabilityStatusV2")
    if isinstance(v2, dict):
        value = _string(v2.get("value")) or _string(v2.get("display"))
        if value:
            return value
    return _string(raw.get("availabilityStatus"))


def _prices(raw: dict[str, Any]) -> dict[str, Any]:
    details = _dig(raw, "priceInfo", "priceDetails") or {}
    lines = details.get("priceLines") if isinstance(details, dict) else None
    found: dict[str, Any] = {
        "current": None,
        "current_display": None,
        "was": None,
        "unit_display": None,
        "currency": _string(details.get("currency")) if isinstance(details, dict) else None,
    }
    if not isinstance(lines, list):
        return found

    for line in lines:
        if not isinstance(line, dict):
            continue
        for key, value in _line_values(line):
            _record_price(found, key, value)
    return found


def _line_values(line: dict[str, Any]) -> list[tuple[str, Any]]:
    """The (label, value) pairs in a price line.

    Observed shape: each line has a lineType and a values[] array of
    {key, value}, where key is PRICE, WAS_PRICE, or UNIT_PRICE. The value is
    read as either an object (price plus rendered string) or a bare string,
    because the observation reported the key names with more confidence than
    the leaf shape, and a display that prints a dict is worse than one that
    prints nothing.
    """
    values = line.get("values")
    if not isinstance(values, list):
        return []

    pairs: list[tuple[str, Any]] = []
    for entry in values:
        if isinstance(entry, dict) and entry.get("key"):
            pairs.append((str(entry["key"]).upper(), entry.get("value")))
    return pairs


def _record_price(found: dict[str, Any], key: str, value: Any) -> None:
    amount, display = _amount_and_display(value)

    if key in CURRENT_PRICE_KEYS and found["current"] is None:
        found["current"] = amount
        found["current_display"] = display
    elif key in WAS_PRICE_KEYS and found["was"] is None:
        found["was"] = amount
    elif key in UNIT_PRICE_KEYS and found["unit_display"] is None:
        # Unit price is displayed verbatim ("2.4 ¢/fl oz"): the units matter as
        # much as the number, and they only exist in the rendered string.
        found["unit_display"] = display


def _amount_and_display(value: Any) -> tuple[float | int | None, str | None]:
    if isinstance(value, dict):
        return _number(value.get("price")), _string(value.get("priceString") or value.get("priceDisplay"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value, None
    text = _string(value)
    return _number(text), text


def _fulfillment(raw: dict[str, Any]) -> dict[str, Any]:
    summary = raw.get("fulfillmentSummary")
    state: dict[str, Any] = {
        "delivery": False,
        "pickup": False,
        "delivery_date": None,
        "store_id": None,
    }
    if not isinstance(summary, list):
        return state

    for entry in summary:
        if not isinstance(entry, dict):
            continue
        kind = (_string(entry.get("fulfillment")) or "").upper()
        if kind == "DELIVERY":
            state["delivery"] = True
            state["delivery_date"] = state["delivery_date"] or _string(entry.get("deliveryDate"))
        elif kind == "PICKUP":
            state["pickup"] = True
        state["store_id"] = state["store_id"] or _string(entry.get("storeId"))
    return state


def _pagination(result: dict[str, Any]) -> dict[str, Any]:
    page_properties = result.get("pageProperties")
    page_properties = page_properties if isinstance(page_properties, dict) else {}
    pagination = result.get("paginationV2")
    pagination = pagination if isinstance(pagination, dict) else {}
    return {
        "has_more": bool(result.get("hasMorePages")),
        "max_page": _number(pagination.get("maxPage")),
        "page": _number(page_properties.get("page")),
        "query": _string(page_properties.get("query")),
        "sort": _string(page_properties.get("sort")),
    }


def _dig(node: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _string(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except ValueError:
        return None
