"""Reading one item page.

The item page is the authority for two things a search result cannot settle.

sellerType is populated here and null in search, and DESIGN.md requires proof
that an offer is first-party before it can be added to a cart. That proof only
exists on this page, which means an add has to read the item, not just trust the
search hit that led to it.

priceInfo is also a different shape here -- currentPrice, basePrice, wasPrice,
comparisonPrice, unitPrice, dropPrice, savings -- so this module has its own
price reader rather than bending the search one. A parser that handles two
schemas by trying both is a parser that silently picks the wrong one the day a
field goes missing.
"""

from __future__ import annotations

from typing import Any

# The seller names Walmart itself trades under, matched case-insensitively.
WALMART_SELLERS = {"walmart.com", "walmart", "walmart inc"}

# The sellerType Walmart uses for its own offers. Present on this page, null in
# search, which is the whole reason this page is the authority.
FIRST_PARTY_SELLER_TYPES = {"internal", "1p", "first_party"}

PRICE_KEYS = (
    ("current", "currentPrice"),
    ("base", "basePrice"),
    ("was", "wasPrice"),
    ("comparison", "comparisonPrice"),
    ("unit", "unitPrice"),
    ("drop", "dropPrice"),
    ("savings", "savings"),
)


def parse_item(payload: Any) -> dict[str, Any]:
    """Turn a decoded item page into one offer."""
    product = _product_node(payload)
    if not product:
        raise ValueError("that page did not contain a product")

    item_id = _string(product.get("usItemId"))
    if not item_id:
        raise ValueError("that product has no item id")

    prices = read_prices(product.get("priceInfo"))
    return {
        "item_id": item_id,
        "offer_id": _string(product.get("offerId")),
        "name": _string(product.get("name")),
        "brand": _string(product.get("brand")),
        "seller_id": _string(product.get("sellerId")),
        "seller_name": _string(product.get("sellerName")),
        "seller_type": _string(product.get("sellerType")),
        "first_party": is_first_party(product),
        "availability": _availability(product),
        "price": prices["current"],
        "price_display": prices["current_display"],
        "was_price": prices["was"],
        "unit_price_display": prices["unit_display"],
        "savings": prices["savings"],
        "fulfillment_type": _string(product.get("fulfillmentType")),
        "fulfillment_options": _fulfillment_options(product.get("fulfillmentOptions")),
        # Read, never assumed: guessing the ceiling means finding it by being
        # refused, and guessing the floor can order more than someone asked for.
        "max_quantity": _number(product.get("orderLimit")),
        "min_quantity": _number(product.get("orderMinLimit")),
        "weight_increment": _number(product.get("weightIncrement")),
        "sales_unit": _string(product.get("salesUnitType") or product.get("salesUnit")),
        "snap_eligible": _bool_or_none(product.get("snapEligible")),
        "substitutable": _bool_or_none(product.get("isSubstitutionEligible")),
        "url": _string(product.get("canonicalUrl")),
        "variants": read_variants(product),
    }


def _product_node(payload: Any) -> dict[str, Any]:
    node: Any = payload if isinstance(payload, dict) else {}
    for key in ("props", "pageProps", "initialData", "data", "product"):
        if isinstance(node, dict) and isinstance(node.get(key), dict):
            node = node[key]
    return node if isinstance(node, dict) and node.get("usItemId") else {}


def read_prices(info: Any) -> dict[str, Any]:
    """The item page's own price shape. Numbers and Walmart's rendered strings.

    Both are kept for the same reason as in search: formatting money from a
    float prints $3.1, and a unit price without its units ("2.4") is not an
    answer to "how much per fluid ounce".
    """
    found: dict[str, Any] = {}
    info = info if isinstance(info, dict) else {}
    for name, key in PRICE_KEYS:
        amount, display = _amount_and_display(info.get(key))
        found[name] = amount
        found[f"{name}_display"] = display
    found["codes"] = _price_codes(info.get("priceDisplayCodes"))
    return found


def _price_codes(codes: Any) -> dict[str, Any]:
    """Walmart's flags about how a price may be shown.

    Kept verbatim rather than interpreted. They govern things like whether a
    price may be displayed before adding to a cart, and inventing a reading of
    them is how a tool ends up printing a number it was asked not to.
    """
    if not isinstance(codes, dict):
        return {}
    return {key: value for key, value in codes.items() if value is not None}


def is_first_party(product: dict[str, Any]) -> bool:
    """Whether Walmart itself sells this offer.

    Both signals must agree. sellerType is the authoritative one and only
    exists here, but "Walmart Restored" and similar storefronts carry a
    Walmart-shaped name while being marketplace sellers, so the name is checked
    too. Either signal alone can lie; together they have not.
    """
    seller_type = (_string(product.get("sellerType")) or "").lower()
    name = (_string(product.get("sellerName")) or "").lower()

    if seller_type:
        return seller_type in FIRST_PARTY_SELLER_TYPES and name in WALMART_SELLERS
    # No sellerType at all: fall back to the name, and say so by being strict.
    return name in WALMART_SELLERS


def read_variants(product: dict[str, Any]) -> dict[str, Any]:
    """What we can honestly say about variant families.

    UNOBSERVED SHAPE. On every item read so far variantCriteria was an empty
    list and variantsMap an empty object, so the populated nesting has never
    been seen. Rather than infer it, this reports only what is checkable --
    whether variants exist and how many -- and flags the rest as unknown.
    DESIGN.md requires an ambiguous variant to force a selection, and "we do not
    know how to read these" is a better basis for asking than a guess.
    """
    criteria = product.get("variantCriteria")
    variants = product.get("variantsMap")
    criteria = criteria if isinstance(criteria, list) else []
    variants = variants if isinstance(variants, dict) else {}

    return {
        "has_variants": bool(criteria or variants),
        "count": len(variants),
        "criteria_names": _criteria_names(criteria),
        # True whenever there is something here we have never seen populated.
        "shape_unobserved": bool(criteria or variants),
    }


def _criteria_names(criteria: list[Any]) -> list[str]:
    names = (_string(entry.get("name")) for entry in criteria if isinstance(entry, dict))
    return [name for name in names if name]


def _fulfillment_options(options: Any) -> list[dict[str, Any]]:
    if not isinstance(options, list):
        return []
    found = []
    for entry in options:
        if not isinstance(entry, dict):
            continue
        found.append(
            {
                "type": _string(entry.get("type") or entry.get("fulfillmentType")),
                "available": _bool_or_none(entry.get("availabilityStatus") == "IN_STOCK")
                if "availabilityStatus" in entry
                else _bool_or_none(entry.get("available")),
                "label": _string(entry.get("displayName") or entry.get("title")),
            }
        )
    return found


def _availability(product: dict[str, Any]) -> str | None:
    v2 = product.get("availabilityStatusV2")
    if isinstance(v2, dict):
        value = _string(v2.get("value")) or _string(v2.get("display"))
        if value:
            return value
    return _string(product.get("availabilityStatus"))


def _amount_and_display(value: Any) -> tuple[float | int | None, str | None]:
    if isinstance(value, dict):
        return _number(value.get("price")), _string(value.get("priceString") or value.get("priceDisplay"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value, None
    text = _string(value)
    return _number(text), text


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


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
