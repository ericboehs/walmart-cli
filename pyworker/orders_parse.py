"""Reading order history into archive records.

Two payloads, two jobs:

``parse_history`` reads the server-rendered /orders list. Those rows are an
index, not a record: their items carry no usItemId and no price, so an archive
built from the list alone would know what was bought but not what it cost or how
to join it to a catalog item later. What the list is good for is deciding what
to fetch -- id, date, type, and the cursor.

``parse_order`` reads one server-rendered /orders/<id> page, which is the actual
record.

Redaction happens here rather than at the writer, because the safest place to
drop a field is the moment it enters our own data structures. Nothing downstream
can leak what it was never handed. The denylist is applied at every depth: a
nested deliveryAddress inside a fulfillment group is exactly the one a top-level
check would miss.
"""

from __future__ import annotations

from typing import Any

# Never archived, at any depth. Payment, identity, and location, plus the
# in-store barcode, which is a scannable identifier and not a receipt detail.
DROP_KEYS = frozenset(
    {
        # payment and money identity
        "paymentmethods",
        "refundpaymentmethods",
        "chargehistory",
        "authorizationamount",
        "monthlypayment",
        "orderinvoicedetails",
        "digitalinvoicedetail",
        "idbarcodeimageurl",
        # people
        "customer",
        "driver",
        "shopper",
        "pickedby",
        "alternatepickupperson",
        "alternategrouprecipients",
        "calldriverdetails",
        "checkindetails",
        "vehicleinfo",
        "tippingpaymentmethod",
        # places
        "deliveryaddress",
        "deliveryinstructions",
        "deliverypreferences",
        "accesspointid",
        "address",
        "postalcode",
        "phone",
        "phonenumber",
        "email",
    }
)

# Fulfillment groups hide behind a dynamic, numeric-suffixed key (groups_2101 on
# the order we read). Found by prefix, never by the suffix we happened to see.
GROUPS_PREFIX = "groups_"

# Walmart's own labels, mapped to the four words DESIGN.md uses. An unrecognized
# label is kept verbatim rather than forced into the nearest bucket: a wrong
# normalization is a silent lie about how something arrived.
TYPE_LABELS = {
    "in_store": "in-store",
    "instore": "in-store",
    "delivery": "delivery",
    "scheduled_delivery": "delivery",
    "express_delivery": "delivery",
    "pickup": "pickup",
    "scheduled_pickup": "pickup",
    "shipping": "shipping",
    "shipped": "shipping",
    "fc": "shipping",
}

# Totals are copied under Walmart's own names. We never recompute one: a total
# we derived is not the total that was charged.
TOTAL_KEYS = (
    "subTotal",
    "taxTotal",
    "grandTotal",
    "grandTotalWithTips",
    "strikethroughSubTotal",
    "savings",
    "allSavings",
    "discounts",
    "fees",
    "belowMinimumFee",
    "driverTip",
    "donations",
    "refund",
    "rewards",
)


def redact(node: Any) -> Any:
    """Return the payload with every denylisted key removed, at any depth."""
    if isinstance(node, dict):
        return {key: redact(value) for key, value in node.items() if _normalize(key) not in DROP_KEYS}
    if isinstance(node, list):
        return [redact(entry) for entry in node]
    return node


def _normalize(key: str) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


# --- The list -----------------------------------------------------------------


def parse_history(payload: Any) -> dict[str, Any]:
    """Read one page of purchase history: index rows plus the next cursor."""
    history = _history_node(payload)
    orders = [_index_row(raw) for raw in history.get("orders") or []]
    page_info = history.get("pageInfo") if isinstance(history.get("pageInfo"), dict) else {}

    return {
        "orders": [order for order in orders if order is not None],
        # An absent cursor means "no more pages". Absent and empty-string are
        # both treated as absent so the walk stops rather than asking for page
        # one forever.
        "next_cursor": _string(page_info.get("nextPageCursor")),
    }


def _history_node(payload: Any) -> dict[str, Any]:
    node: Any = payload if isinstance(payload, dict) else {}
    for key in ("props", "pageProps", "phRedesignInitialData", "data", "purchaseHistory"):
        if isinstance(node, dict) and isinstance(node.get(key), dict):
            node = node[key]
    return node if isinstance(node, dict) else {}


def _index_row(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    order_id = _string(raw.get("id"))
    if not order_id:
        return None

    return {
        "order_id": order_id,
        "display_id": _string(raw.get("displayId")),
        "date": _string(raw.get("orderDate")),
        "type": normalize_type(raw.get("type"), in_store=raw.get("isInStore")),
        "raw_type": _string(raw.get("type")),
        "item_count": _number(raw.get("itemCount")),
        "title": _string(raw.get("shortTitle") or raw.get("title")),
    }


def normalize_type(raw_type: Any, in_store: Any = None, group_type: Any = None) -> str:
    """One of delivery / pickup / shipping / in-store, or Walmart's own label.

    isInStore is trusted first because it is a boolean and cannot be ambiguous;
    "GLASS" only tells us the order came through the website, which is why an
    index row for an online order reads as "online" until its detail page says
    how it actually arrived. Claiming "delivery" there would be a guess with a
    one-in-three chance of being wrong.
    """
    if in_store is True:
        return "in-store"

    online = False
    for candidate in (group_type, raw_type):
        key = _normalize(candidate) if candidate is not None else ""
        if key in TYPE_LABELS:
            return TYPE_LABELS[key]
        if key.startswith("glass"):
            online = True
            continue
        if key:
            return str(candidate)
    return "online" if online else "unknown"


# --- One order ----------------------------------------------------------------


def parse_order(payload: Any) -> dict[str, Any]:
    """Read one order detail page into an archive record."""
    order = redact(_order_node(payload))
    if not order:
        raise ValueError("that page did not contain an order")

    groups = [_group(raw) for raw in _group_nodes(order)]
    items = [item for group in groups for item in group["items"]]

    return {
        "order_id": _string(order.get("id")),
        "display_id": _string(order.get("displayId")),
        "date": _string(order.get("orderDate")),
        "type": _order_type(order, groups),
        "raw_type": _string(order.get("type")),
        "title": _string(order.get("shortTitle")),
        "item_count": _number(order.get("itemCount")) or len(items),
        "totals": _totals(order.get("priceDetails")),
        "groups": groups,
        "items": items,
    }


def _order_node(payload: Any) -> dict[str, Any]:
    node: Any = payload if isinstance(payload, dict) else {}
    for key in ("props", "pageProps", "initialData", "data", "order"):
        if isinstance(node, dict) and isinstance(node.get(key), dict):
            node = node[key]
    return node if isinstance(node, dict) else {}


def _group_nodes(order: dict[str, Any]) -> list[dict[str, Any]]:
    """Every groups_<n> list, in key order.

    All of them, not the first: an order split across two fulfillments would
    otherwise archive half its items and look complete.
    """
    found: list[dict[str, Any]] = []
    for key, value in order.items():
        if not str(key).startswith(GROUPS_PREFIX) or not isinstance(value, list):
            continue
        found.extend(entry for entry in value if isinstance(entry, dict))
    return found


def _group(raw: dict[str, Any]) -> dict[str, Any]:
    store = raw.get("store") if isinstance(raw.get("store"), dict) else {}
    return {
        "group_id": _string(raw.get("groupId")),
        "status": _string(raw.get("status")),
        "type": normalize_type(
            raw.get("fulfillmentType"),
            group_type=raw.get("deliveryType") or raw.get("derivedFulfillmentType"),
        ),
        "delivered_date": _string(raw.get("deliveredDate")),
        # Store id and name only. Which store filled an order is history; where
        # it went is not ours to keep.
        "store_id": _string(store.get("id") or raw.get("storeId")),
        "store_name": _string(store.get("name") or store.get("displayName")),
        "seller": _string(raw.get("seller")),
        "items": [item for item in (_item(entry) for entry in raw.get("items") or []) if item],
    }


def _item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    product = raw.get("productInfo") if isinstance(raw.get("productInfo"), dict) else {}
    item_id = _string(product.get("usItemId"))
    offer_id = _string(product.get("offerId") or raw.get("offerId"))
    if not item_id and not offer_id:
        return None

    price = raw.get("priceInfo") if isinstance(raw.get("priceInfo"), dict) else {}
    return {
        # usItemId is the join key for bought-before; offerId is a fallback,
        # and DESIGN.md is explicit that titles are not a join key at all.
        "item_id": item_id,
        "offer_id": offer_id,
        "name": _string(product.get("name")),
        "quantity": _number(raw.get("quantity")),
        "line_price": _amount(price.get("linePrice")),
        "sales_unit": _string(raw.get("orderedSalesUnit") or product.get("salesUnitType")),
        # Populated shape unobserved: null on every item read so far. Kept as a
        # passthrough number so a weighted order archives honestly the first
        # time we see one.
        "requested_weight": _number(raw.get("requestedWeight")),
        "image_url": _image(product.get("imageInfo")),
        "status": _string(raw.get("statusCode")),
        "unavailable": raw.get("isUnavailable") if isinstance(raw.get("isUnavailable"), bool) else None,
    }


def _order_type(order: dict[str, Any], groups: list[dict[str, Any]]) -> str:
    group_type = groups[0]["type"] if groups else None
    return normalize_type(order.get("type"), in_store=order.get("isInStore"), group_type=group_type)


def _totals(details: Any) -> dict[str, Any]:
    details = details if isinstance(details, dict) else {}
    totals: dict[str, Any] = {}
    for key in TOTAL_KEYS:
        if key in details:
            totals[_snake(key)] = _amount(details[key])
    return totals


def _snake(key: str) -> str:
    out: list[str] = []
    for character in key:
        if character.isupper() and out:
            out.append("_")
        out.append(character.lower())
    return "".join(out)


def _amount(value: Any) -> float | int | None:
    if isinstance(value, dict):
        for key in ("value", "amount", "price"):
            if key in value:
                return _number(value[key])
        return None
    return _number(value)


def _image(info: Any) -> str | None:
    if not isinstance(info, dict):
        return None
    for key in ("thumbnailUrl", "url", "size200Url"):
        url = _string(info.get(key))
        if url:
            return url
    return None


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
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
