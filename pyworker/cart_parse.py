"""Reading a cart response into something a person can check.

Shape observed live (read-only, 2026-08-26) from the getCart operation:

    data.cart.{id, lineItems[], priceDetails{}, fulfillment{}, ...}

Two things about this module are deliberate.

The cart id is never returned. It is a session-scoped identifier that belongs to
the browser profile, and nothing in a printed cart or a JSON dump needs it. What
is never carried cannot leak.

Quantity limits are read, never assumed. Line items carry orderLimit and
orderMinLimit, and a tool that guesses those is a tool that discovers them by
being refused -- or worse, by succeeding at something the shopper did not want.
"""

from __future__ import annotations

from typing import Any

# Keys under lineItem.priceInfo, in the order a receipt reads.
LINE_PRICE_KEYS = ("linePrice", "itemPrice", "wasPrice", "unitPrice", "savedPrice")


class CartUnreadable(Exception):
    """We got something back, but it was not a cart we can trust.

    Kept separate from "the cart is empty" on purpose. Reporting an unreadable
    cart as an empty one is how someone shops twice.
    """

    code = "cart_unreadable"


def parse_cart(payload: Any) -> dict[str, Any]:
    """Turn a decoded getCart response into lines plus totals.

    Raises CartUnreadable when the response does not contain a cart. Three
    shapes are refused and one is accepted:

    - no cart node at all (null, missing, or a non-object payload) -> unreadable
    - a cart whose lineItems is missing or is not a list -> unreadable
    - a cart with lineItems == [] -> genuinely empty

    The middle case is UNOBSERVED. We have never seen an empty cart's response,
    because checking would have meant emptying a real household's cart
    overnight with only an unobserved mutation path to put it back. Until
    someone loads a legitimately empty cart, that shape stays ambiguous, and
    ambiguous resolves to "I could not read it" rather than to "you have
    nothing". Do not simplify this branch into the empty one without that
    observation.
    """
    cart = _cart_node(payload)
    if cart is None:
        raise CartUnreadable("the response contained no cart")

    raw_lines = cart.get("lineItems")
    if not isinstance(raw_lines, list):
        raise CartUnreadable("the cart came back without a readable list of items")

    lines = [normalize_line(raw) for raw in raw_lines]
    lines = [line for line in lines if line is not None]

    return {
        "lines": lines,
        "line_count": len(lines),
        # Quantities are summed as floats because weighted produce arrives as
        # 2.5 lb, and rounding it to 2 would misreport the cart.
        "item_count": sum(line["quantity"] or 0 for line in lines),
        "totals": _totals(cart.get("priceDetails")),
        "fulfillment": _fulfillment(cart.get("fulfillment")),
        "problems": _problems(cart),
    }


def _cart_node(payload: Any) -> dict[str, Any] | None:
    """The cart object, or None when the response does not carry one.

    Accepts the response with or without its GraphQL envelope, since the same
    payload reaches us from a live response and from a recon capture.
    """
    if not isinstance(payload, dict):
        return None
    node: Any = payload
    if isinstance(node.get("data"), dict):
        node = node["data"]
    if "cart" in node:
        node = node["cart"]
    if not isinstance(node, dict) or "lineItems" not in node:
        return None
    return node


def normalize_line(raw: Any) -> dict[str, Any] | None:
    """One cart line, flattened. None for entries with no product."""
    if not isinstance(raw, dict):
        return None
    product = raw.get("product")
    product = product if isinstance(product, dict) else {}
    item_id = _string(product.get("usItemId"))
    if not item_id:
        return None

    prices = _line_prices(raw.get("priceInfo"))
    return {
        "line_id": _string(raw.get("id")),
        "item_id": item_id,
        "offer_id": _string(product.get("offerId")),
        "name": _string(product.get("name")),
        "brand": _string(product.get("brand")),
        "seller_name": _string(product.get("sellerDisplayName") or product.get("sellerName")),
        "seller_type": _string(product.get("sellerType")),
        "quantity": _number(raw.get("quantity")),
        # Walmart renders "2.5 lb" or "3 ct" itself. Rebuilding that from the
        # number and the unit is how a display ends up saying "2.5 EACH".
        "quantity_label": _string(raw.get("quantityString") or raw.get("quantityLabel")),
        "line_price": prices["linePrice"],
        "item_price": prices["itemPrice"],
        "was_price": prices["wasPrice"],
        "unit_price_display": prices["unitPrice_display"],
        "availability": _string(product.get("availabilityStatus")),
        "fulfillment_type": _string(product.get("fulfillmentType")),
        "fulfillment_label": _string(product.get("fulfillmentTitle")),
        "sales_unit": _string(product.get("salesUnitType") or product.get("salesUnit")),
        "weight_increment": _number(product.get("weightIncrement")),
        # Read, never assumed. A tool that guesses the ceiling finds it by
        # being refused, and a tool that guesses the floor can silently order
        # more than someone asked for.
        "max_quantity": _number(product.get("orderLimit")),
        "min_quantity": _number(product.get("orderMinLimit")),
        "substitutable": _bool_or_none(product.get("isSubstitutionEligible")),
        "alcohol": _bool_or_none(product.get("isAlcohol")),
        "snap_eligible": _bool_or_none(product.get("snapEligible")),
        "url": _string(product.get("rhPath")),
    }


def _line_prices(info: Any) -> dict[str, Any]:
    found: dict[str, Any] = {key: None for key in LINE_PRICE_KEYS}
    found["unitPrice_display"] = None
    if not isinstance(info, dict):
        return found

    for key in LINE_PRICE_KEYS:
        value = info.get(key)
        amount, display = _amount_and_display(value)
        found[key] = amount
        if key == "unitPrice":
            # Unit price is displayed verbatim: "$1.28/lb" answers the question,
            # "1.28" does not.
            found["unitPrice_display"] = display
    return found


def _amount_and_display(value: Any) -> tuple[float | int | None, str | None]:
    if isinstance(value, dict):
        return _number(value.get("value") if "value" in value else value.get("price")), _string(
            value.get("displayValue") or value.get("priceString")
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value, None
    text = _string(value)
    return _number(text), text


def _totals(details: Any) -> dict[str, Any]:
    """The money summary, by the names a shopper would use.

    Walmart's own key names are kept alongside nothing else: if a name is not
    present we return None for it rather than computing a substitute. A total
    we derived is not the total Walmart will charge.
    """
    details = details if isinstance(details, dict) else {}
    wanted = {
        "subtotal": ("subTotal", "subtotal", "itemSubTotal"),
        "total": ("grandTotal", "total", "estimatedTotal"),
        "savings": ("totalSavings", "savings"),
        "fees": ("fees", "totalFees"),
        "taxes": ("taxTotal", "totalTax", "taxes"),
    }
    totals: dict[str, Any] = {}
    for name, candidates in wanted.items():
        amount = None
        display = None
        for candidate in candidates:
            if candidate in details:
                amount, display = _amount_and_display(details[candidate])
                break
        totals[name] = amount
        totals[f"{name}_display"] = display
    return totals


def _fulfillment(node: Any) -> dict[str, Any]:
    node = node if isinstance(node, dict) else {}
    return {
        "intent": _string(node.get("intent") or node.get("fulfillmentIntent")),
        "store_id": _string(node.get("storeId")),
        "type": _string(node.get("fulfillmentType")),
    }


def _problems(cart: dict[str, Any]) -> list[str]:
    """Anything Walmart is complaining about, as plain sentences.

    A cart with an unavailable line still renders; the complaint is what tells
    the shopper why their total looks wrong. Silence here would make a broken
    cart look fine.
    """
    problems: list[str] = []
    for key in ("errorLineItems", "operationalErrors"):
        for entry in cart.get(key) or []:
            message = _problem_text(entry)
            if message:
                problems.append(message)
    return problems


def _problem_text(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry.strip() or None
    if not isinstance(entry, dict):
        return None
    for key in ("message", "description", "errorMessage", "title", "code"):
        text = _string(entry.get(key))
        if text:
            return text
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


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
