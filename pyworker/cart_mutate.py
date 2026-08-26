"""Changing the cart, and knowing whether it changed.

Every quantity change Walmart's own pages make goes through one GraphQL
operation, `updateItems`: adding, incrementing, decrementing and removing are
the same request with a different number in it. That is a recorded observation
from a capture of the real site, not a guess -- see docs/CONTRACTS.md.

Three properties shape everything here.

**The wire is absolute; the interface is a delta.** `updateItems` sets a
quantity outright, and `quantity: 0` is removal. A tool that exposed that
directly would let a stale number silently overwrite a cart someone else just
changed, so no absolute setter is reachable from outside this module. Callers
say "one more" or "take it out"; the target is derived here from a cart read
taken moments before.

**A 200 is not a success.** GraphQL answers errors with 200, and a persisted
query that Walmart has rotated away from fails inside a perfectly healthy HTTP
response. Nothing is reported as applied until the response is acknowledged
*and* a fresh read of the cart shows the intended quantity.

**Neither is a matching post-state.** If the acknowledgement was unreadable but
the cart now holds the target, that is not proof we did it -- an edit from a
phone would look identical. That case is UNVERIFIED, and it stays UNVERIFIED,
because a second corrective write is how one lost update becomes two.

The cart id is required by the operation and never leaves this module: read
from the live cart, used in the request it was read for, dropped when the
action returns. It is never returned over the protocol, persisted, logged, or
placed in an error message. See docs/CONTRACTS.md.
"""

from __future__ import annotations

from typing import Any

import browser
import cart as cart_module
import cart_parse
import item_parse
import protocol
import safety

# The operation, and the persisted-query hash observed alongside it. Walmart
# rotates the hash with each web build; when that happens every mutation fails
# in the same recognisable way, and the tool says so rather than guessing.
UPDATE_ITEMS = "updateItems"
UPDATE_ITEMS_HASH = "45fca98d0f0fb2ffc2ccf8a3290b89141cdad6c9096c3fe39303e2fdf8f524b1"
UPDATE_ITEMS_PATH = f"/orchestra/cartxo/graphql/{UPDATE_ITEMS}/{UPDATE_ITEMS_HASH}"

# Copied from the capture, not composed. We do not know what most of these
# flags do, which is exactly why they are reproduced rather than tidied: the
# request that is known to work is the one the site sent.
OBSERVED_FEATURES = (
    "lmpdel",
    "mlrx",
    "vsrx",
    "maappl",
    "accfournudge",
    "potp",
    "byod",
    "vptires",
    "pdr",
    "gepmss",
    "dd",
    "qsr",
    "qsr_qty",
    "qsro4w",
    "cbs",
    "tfd",
    "wfss",
    "dynevgn",
)

# Outcomes. Deliberately four, not two: "we could not tell" is a real answer
# about a shared cart and must not be rounded to either of its neighbours.
APPLIED = "applied"
REJECTED = "rejected"
UNVERIFIED = "unverified"
CONTRACT_STALE = "contract_stale"

CONTRACT_STALE_MESSAGE = (
    "Walmart no longer recognises the cart update request this tool was built "
    "against; the cart was not changed. This needs walmart-cli to be updated."
)


class CartMutationRefused(Exception):
    """A change this tool will not attempt. Raised before anything is sent."""


class Acknowledgement:
    """What the server said, reduced to whether it accepted the change.

    Kept as a value rather than a bare bool because the reason survives into
    the outcome: a rotated persisted query and an out-of-stock rejection are
    both "not accepted" and want different words in front of the person.
    """

    def __init__(self, accepted: bool, reason: str | None = None, detail: str | None = None):
        self.accepted = accepted
        self.reason = reason
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Acknowledgement(accepted={self.accepted!r}, reason={self.reason!r})"


def acknowledgement(status: Any, payload: Any) -> Acknowledgement:
    """Read a response as accepted or not. Pure; no network, no browser.

    The order of these checks is the point. A GraphQL error inside a 200 is the
    normal way this operation fails, so the status is consulted but never
    trusted on its own, and `data` must actually be there: a response of
    `{"data": null, "errors": [...]}` is a failure wearing a success's status
    code.
    """
    if not isinstance(status, int) or not 200 <= status < 300:
        return Acknowledgement(False, "http_status", f"Walmart answered {status}")

    if not isinstance(payload, dict):
        return Acknowledgement(False, "unreadable", "the response was not a JSON object")

    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        if _mentions_persisted_query(errors):
            return Acknowledgement(False, CONTRACT_STALE, CONTRACT_STALE_MESSAGE)
        return Acknowledgement(False, "rejected", _first_error_message(errors))

    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        # No errors and no data is not agreement, it is silence. We have never
        # observed a successful response body -- the capture recorded requests
        # only -- so this deliberately refuses to read an empty object as yes.
        return Acknowledgement(False, "unreadable", "the response carried no data")

    return Acknowledgement(True)


def _mentions_persisted_query(errors: list[Any]) -> bool:
    for error in errors:
        text = ""
        if isinstance(error, dict):
            text = f"{error.get('message', '')} {_extension_code(error)}"
        elif isinstance(error, str):
            text = error
        if "persistedquery" in safety.normalize(text):
            return True
    return False


def _extension_code(error: dict[str, Any]) -> str:
    extensions = error.get("extensions")
    if isinstance(extensions, dict):
        return str(extensions.get("code", ""))
    return ""


def _first_error_message(errors: list[Any]) -> str:
    for error in errors:
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
    return "Walmart rejected the change without saying why"


def classify(ack: Acknowledgement, before: Any, after: Any, target: int) -> tuple[str, str]:
    """Decide what actually happened. Pure, and the heart of this module.

    `before` and `after` are the quantities read either side of the request,
    either of which may be None when a read failed.

    The table, and why each row is where it is:

    - the post-read failed          -> UNVERIFIED. We sent something and cannot
                                       see the result. Nothing else is honest.
    - accepted, cart shows target   -> APPLIED. The only success.
    - accepted, cart shows otherwise-> UNVERIFIED. The server agreed and the
                                       cart disagrees; someone else may have
                                       written in between.
    - refused, cart unmoved         -> REJECTED (or CONTRACT_STALE). A clean
                                       failure: we know it did not happen.
    - refused, cart moved           -> UNVERIFIED. The worst case, and the
                                       reason this function exists.
    """
    if after is None:
        return UNVERIFIED, (
            "the change was sent but the cart could not be read afterwards, "
            "so this tool cannot say whether it took effect"
        )

    if ack.accepted:
        if after == target:
            return APPLIED, ""
        return UNVERIFIED, (
            f"Walmart accepted the change but the cart now shows {after}, not {target}; "
            "something else may have changed this cart at the same time"
        )

    # A clean failure is a claim that nothing happened, and that claim needs a
    # before to rest on. Without one we cannot tell an unmoved cart from a cart
    # that was already sitting at the target, so we do not get to say "rejected".
    unmoved = before is not None and after == before
    if not unmoved:
        return UNVERIFIED, (
            f"Walmart did not accept the change, but the cart shows {after} "
            f"rather than the {before} read beforehand"
            if before is not None
            else f"Walmart did not accept the change and the cart now shows {after}, "
            "which this tool cannot compare against anything"
        ) + "; this tool will not send a second request to correct it"

    if ack.reason == CONTRACT_STALE:
        return CONTRACT_STALE, ack.detail or CONTRACT_STALE_MESSAGE
    return REJECTED, ack.detail or "Walmart did not accept the change"


def target_quantity(current: int, delta: int | None, remove: bool) -> int:
    """Derive the absolute quantity from a relative intent.

    Never exposed: this is where "one more" becomes a number, using a count
    read moments ago. Refusing rather than clamping is deliberate -- asking to
    remove three of two is a mistake about the cart, and silently doing
    something adjacent is how a tool loses the right to be trusted.
    """
    if remove:
        return 0
    if delta is None:
        raise CartMutationRefused("no change was requested")
    target = current + delta
    if target < 0:
        raise CartMutationRefused(
            f"cannot remove {abs(delta)} when the cart holds {current}; "
            "use `walmart cart remove` to take the line out entirely"
        )
    return target


def build_update_items_body(cart_id: str, line: dict[str, Any], quantity: int) -> dict[str, Any]:
    """Compose the request, copying the observed contract field for field.

    Only `quantity` varies from what the site sent. The flags are reproduced
    verbatim because a request known to work is worth more than a tidy one, and
    we cannot tell from outside which of them the server actually reads.
    """
    return {
        "variables": {
            "input": {
                "cartId": cart_id,
                "items": [
                    {
                        "offerId": line["offer_id"],
                        "usItemId": line["item_id"],
                        "quantity": quantity,
                        "name": line.get("name") or "",
                        "isPharmacyPrescription": False,
                        "isQSRItem": False,
                    }
                ],
                "enableLiquorBox": True,
                "skipPolicyCheck": False,
                "cartLeanMode": False,
                "enableCartSplitClarity": False,
                "features": list(OBSERVED_FEATURES),
            }
        }
    }


def find_line(cart: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    """The cart line for an item id, or None. Compared as strings, always."""
    wanted = str(item_id).strip()
    for line in cart.get("lines") or []:
        if str(line.get("item_id") or "").strip() == wanted:
            return line
    return None


def quantity_of(cart: dict[str, Any] | None, item_id: str) -> int | None:
    """Current quantity of an item, 0 when absent, None when the cart is unknown.

    The three-way return is the same distinction the read path already draws:
    an unreadable cart is not an empty cart, and neither is an empty one a
    missing answer.
    """
    if cart is None:
        return None
    line = find_line(cart, item_id)
    if line is None:
        return 0
    value = line.get("quantity")
    return value if isinstance(value, int) else None


def cart_id_of(payload: Any) -> str:
    """Pull the cart id out of a live getCart payload.

    The one place this value is read. It goes straight into the request being
    built and is never returned, stored, or logged; see the module docstring.
    """
    if isinstance(payload, dict):
        cart = (payload.get("data") or {}).get("cart")
        if isinstance(cart, dict):
            value = cart.get("id")
            if isinstance(value, str) and value:
                return value
    raise cart_parse.CartUnreadable("the cart response did not contain a cart id, so no change can be made")


def update_items_url() -> str:
    """The endpoint, gated by the same guard as every other destination."""
    return safety.assert_navigable(f"{browser.WALMART_ORIGIN}{UPDATE_ITEMS_PATH}")


def send_update(session, body: dict[str, Any]) -> tuple[Any, Any]:
    """Issue the mutation from inside the page. Returns (status, payload).

    From the page rather than replayed, for the reason the read path already
    learned the hard way: a reconstructed request with a partial header set was
    answered 429 twice while the page's own identical request succeeded. Asking
    the page to make its own call means every cookie and client hint is the
    real one, because it is the real one.
    """
    url = update_items_url()
    mismatch = safety.operation_mismatch(url, body)
    if mismatch:
        raise safety.UnsafeNavigation(f"refusing to send a body naming {mismatch}")

    result = session.page.evaluate(
        """async ({url, body}) => {
            const response = await fetch(url, {
                method: 'POST',
                credentials: 'include',
                headers: {'content-type': 'application/json', 'accept': 'application/json'},
                body: JSON.stringify(body),
            });
            let payload = null;
            try { payload = await response.json(); } catch (e) { payload = null; }
            return {status: response.status, payload};
        }""",
        {"url": url, "body": body},
    )
    if not isinstance(result, dict):
        return None, None
    return result.get("status"), result.get("payload")


def _requested(request: dict[str, Any]) -> tuple[str, int | None, bool]:
    params = request.get("params") or {}
    item_id = str(params.get("item_id") or "").strip()
    if not item_id:
        raise ValueError("an item id is required")
    remove = bool(params.get("remove"))
    raw = params.get("delta")
    if remove:
        return item_id, None, True
    try:
        delta = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected a whole number to change by, got {raw!r}") from exc
    if delta == 0:
        raise ValueError("a change of zero is not a change")
    return item_id, delta, False


def is_first_party_line(line: dict[str, Any]) -> bool:
    """Whether Walmart sells this cart line, by the same test as everywhere else.

    The parsed cart line spells the signals differently from a product node --
    seller_type and seller_name rather than sellerType and sellerName -- so
    they are translated and handed to the one predicate that decides this.
    Reimplementing the rule here would be a second definition of first-party,
    and two definitions is one more than can stay correct.
    """
    return item_parse.is_first_party(
        {"sellerType": line.get("seller_type"), "sellerName": line.get("seller_name")}
    )


def _assert_first_party(line: dict[str, Any]) -> None:
    """Refuse to touch a marketplace line.

    The same two-signal test the search and item paths use, applied at the
    moment of writing rather than the moment of looking, because a cart read
    can be minutes old and this is the last point where refusing is free.
    """
    if not is_first_party_line(line):
        raise CartMutationRefused(
            f"{line.get('name') or line.get('item_id')} is sold by a marketplace seller; "
            "this tool only changes Walmart's own items"
        )


def action_cart_change(request: dict[str, Any]) -> dict[str, Any]:
    """Worker entry point for every cart quantity change.

    One handler for four verbs. The verbs differ only in the number they ask
    for, and giving each its own path would be four chances for one of them to
    skip a check.
    """
    item_id, delta, remove = _requested(request)
    options = browser.BrowserOptions.from_request(request.get("options", {}))

    with browser.open_session(options) as session:
        # A cart read taken now, not one cached from an earlier command. The
        # window between this read and the write below is the lost-update
        # window, and it is narrow on purpose; it is not zero, and nothing here
        # pretends otherwise.
        protocol.progress("reading the cart")
        payload = cart_module.capture_cart(session)
        before_cart = cart_parse.parse_cart(payload)

        line = find_line(before_cart, item_id)
        if line is None:
            raise CartMutationRefused(
                f"item {item_id} is not in the cart; "
                "`walmart cart add` can only change lines that are already there"
            )
        _assert_first_party(line)

        before = quantity_of(before_cart, item_id)
        if before is None:
            raise cart_parse.CartUnreadable(
                "the cart did not report a quantity for this item, so no change can be derived"
            )
        target = target_quantity(before, delta, remove)
        if target == before:
            return _result(item_id, before, before, target, APPLIED, "", changed=False)

        body = build_update_items_body(cart_id_of(payload), line, target)

        protocol.progress("updating the cart")
        status, response = send_update(session, body)
        ack = acknowledgement(status, response)

        # Read the cart again from the site, never from the response. What we
        # want to know is what the cart holds, and only the cart can say.
        protocol.progress("checking the cart")
        after = quantity_of(_reread(session), item_id)

    outcome, message = classify(ack, before, after, target)
    return _result(item_id, before, after, target, outcome, message)


def _reread(session) -> dict[str, Any] | None:
    try:
        return cart_parse.parse_cart(cart_module.capture_cart(session))
    except Exception:  # noqa: BLE001 - an unreadable post-state is an outcome, not a crash
        return None


def _result(
    item_id: str,
    before: Any,
    after: Any,
    target: int,
    outcome: str,
    message: str,
    changed: bool = True,
) -> dict[str, Any]:
    """The document the CLI sees. Note what is absent: the cart id."""
    return {
        "item_id": item_id,
        "outcome": outcome,
        "quantity_before": before,
        "quantity_after": after,
        "quantity_intended": target,
        "changed": changed and outcome == APPLIED,
        "message": message,
    }
