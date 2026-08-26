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

# THE SUCCESS CONTRACT IS UNOBSERVED, AND THAT IS A GATE, NOT A CAVEAT.
#
# The capture recorded requests only. No successful updateItems response has
# ever been seen, so there is no shape to check and no honest definition of
# "Walmart accepted this". The first version of this module accepted any
# non-empty `data` object, which is not evidence-first -- it is a guess that
# fails open, and {"data": {"anything": false}} satisfied it.
#
# So this stays None until a response is captured, and while it is None:
#   - acknowledgement() can never return accepted
#   - the action refuses before sending anything
#
# Filling this in requires evidence, not a decision. See docs/CONTRACTS.md.
ACCEPTED_RESPONSE_SHAPE: dict[str, Any] | None = None

# A SECOND, INDEPENDENT GATE.
#
# Delivery eligibility is checked at the cart level -- fulfillment.intent --
# because that is the only signal the parsed cart carries. Whether a particular
# line can actually be delivered has never been observed. That is a different
# unknown from the response shape, and it was previously only a paragraph in
# the docs, which meant capturing one response would have silently authorised
# the other.
#
# Two contracts, two constants, and readiness needs both. Evidence for one is
# never evidence for the other.
PER_LINE_DELIVERY_PROVEN = False

NO_PER_LINE_DELIVERY_REASON = (
    "this tool can see that the cart is a delivery cart, but not that a particular line "
    "can be delivered, and it will not assume the two are the same"
)

CONTRACT_UNPROVEN = "contract_unproven"
NOT_READY_PREFIX = "changing the cart is not enabled yet"

NO_RESPONSE_CONTRACT_REASON = (
    "no successful response from Walmart's cart update has ever been recorded, so this "
    "tool cannot tell a change that worked from one that silently did nothing"
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


class PreflightFailed(Exception):
    """Something went wrong before the request went out.

    Carries the original error's code so the failure keeps its own name. The
    distinction matters more than it looks: every mutating action reports
    exceptions as unverified, which is right after a request has gone out and
    wrong before one has. A browser that never opened cannot have changed the
    cart, and telling someone their cart "may have changed" sends them to check
    something that provably did not happen.
    """

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.code = getattr(cause, "code", None) or "preflight_failed"


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
        # The server's own words are never repeated. A GraphQL error can echo
        # the variables that caused it, and those variables contain the cart
        # id -- so passing this text through to the result document would be a
        # side door around every canary that watches the front one.
        return Acknowledgement(False, "rejected", GENERIC_REJECTION)

    if ACCEPTED_RESPONSE_SHAPE is None:
        # Fail closed. Any success we declare here would be invented.
        return Acknowledgement(False, CONTRACT_UNPROVEN, not_ready_message(unmet_contracts()))

    if not _matches(payload.get("data"), ACCEPTED_RESPONSE_SHAPE):
        return Acknowledgement(False, "unreadable", "the response did not match the known shape")

    return Acknowledgement(True)


GENERIC_REJECTION = "Walmart did not accept the change"


def _matches(data: Any, shape: dict[str, Any]) -> bool:
    """Whether a response body carries the keys a success is known to carry.

    Structural and shallow on purpose: enough to tell an acknowledgement from
    an unrelated payload, without pretending to know more of the shape than was
    observed.
    """
    if not isinstance(data, dict):
        return False
    for key, expected in shape.items():
        if key not in data:
            return False
        if isinstance(expected, dict) and not _matches(data[key], expected):
            return False
    return True


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
    """The cart line for an item id, or None. Compared as strings, always.

    Two lines with the same item id is ambiguity, not a tie to be broken.
    Returning the first would mean "exact variant" quietly meant "first match",
    and the wrong line would be changed with no sign anything was uncertain.
    """
    wanted = str(item_id).strip()
    found = [line for line in cart.get("lines") or [] if str(line.get("item_id") or "").strip() == wanted]
    if len(found) > 1:
        raise CartMutationRefused(
            f"the cart holds {len(found)} separate lines for item {wanted}; "
            "this tool will not guess which one you meant"
        )
    return found[0] if found else None


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
    # bool is a subclass of int, so isinstance(True, int) is True and a cart
    # reporting quantity=True would arithmetic its way to 2. Same reasoning as
    # the request validation.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


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


def checked_destination(body: dict[str, Any]) -> str:
    """The URL to post to, having refused every reason not to.

    Split out from the sending so that the phase boundary is exact. These
    checks can refuse, and a refusal here means nothing was sent -- which is a
    clean failure, not an unverified one. When this lived inside send_update
    the caller had already recorded the attempt as begun.
    """
    url = update_items_url()
    mismatch = safety.operation_mismatch(url, body)
    if mismatch:
        raise safety.UnsafeNavigation(f"refusing to send a body naming {mismatch}")
    return url


def send_update(session, url: str, body: dict[str, Any]) -> tuple[Any, Any]:
    """Issue the mutation from inside the page. Returns (status, payload).

    Nothing in here may refuse: by the time this is called the request is
    considered sent, because from the caller's point of view it cannot be
    proven otherwise. Validation belongs in checked_destination, above.

    From the page rather than replayed, for the reason the read path already
    learned the hard way: a reconstructed request with a partial header set was
    answered 429 twice while the page's own identical request succeeded. Asking
    the page to make its own call means every cookie and client hint is the
    real one, because it is the real one.
    """
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


# What each action is allowed to ask for. The protocol is directly invocable,
# so the worker validates the request rather than trusting that Ruby built it:
# a caller who sends cart.add with remove=true must not get a removal.
REMOVING_ACTIONS = ("cart.remove",)
CHANGING_ACTIONS = ("cart.add",)


def _boolean(value: Any, field: str) -> bool:
    """A boolean, and only a boolean.

    bool(value) would read the string "false" as True, which is how a typo in a
    JSON client becomes a deleted cart line. Note bool is a subclass of int in
    Python, so the int branch below has to exclude it explicitly.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    raise ValueError(f"{field} must be true or false, got {value!r}")


def _whole_number(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a whole number, got {value!r}")
    return value


def _requested(request: dict[str, Any]) -> tuple[str, int | None, bool]:
    action = str(request.get("action") or "")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")

    item_id = str(params.get("item_id") or "").strip()
    if not item_id:
        raise ValueError("an item id is required")

    remove = _boolean(params.get("remove"), "remove")

    # The action and the parameters must agree. Either alone can be wrong.
    if action in REMOVING_ACTIONS and not remove:
        raise ValueError(f"{action} requires remove=true")
    if action in CHANGING_ACTIONS and remove:
        raise ValueError(f"{action} cannot remove a line; use cart.remove")

    if remove:
        if params.get("delta") is not None:
            raise ValueError("a removal takes no delta")
        return item_id, None, True

    delta = _whole_number(params.get("delta"), "delta")
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


# The cart-level fulfillment intent that means delivery. Copied from the
# observed request, where the item carried preferredItemLevelIntent="DELIVERY".
DELIVERY_INTENTS = ("delivery",)


def assert_deliverable_from_the_pinned_store(cart: dict[str, Any], pinned_store: Any) -> None:
    """Refuse unless the cart is the delivery cart for the store we pinned.

    Three separate questions, and every one of them has to be answered from the
    live cart rather than assumed:

    - is a store pinned at all
    - is this cart that store's cart
    - is this cart a delivery cart

    A change made against a pickup cart, or against a different store's cart,
    is a change to something the person did not ask about. None of these can be
    inferred from silence, so a missing value refuses just as a wrong one does.
    """
    # pinned_store is the whole store object Ruby's config holds, not an id.
    # An earlier version stringified whatever arrived, which compared a live
    # store id against the rendering of a dict -- it would have refused every
    # correctly configured request in production while every test passed.
    # Read the one field, and refuse anything that is not the producer's shape.
    if pinned_store is None:
        raise CartMutationRefused(
            "no store is pinned, so this tool cannot tell which store's cart it is about to change"
        )
    if not isinstance(pinned_store, dict):
        raise CartMutationRefused("the pinned store setting is not a store object")
    pinned = str(pinned_store.get("store_id") or "").strip()
    if not pinned:
        raise CartMutationRefused("the pinned store has no store_id")

    fulfillment = cart.get("fulfillment") or {}
    store_id = str(fulfillment.get("store_id") or "").strip()
    if not store_id:
        raise CartMutationRefused("the cart did not say which store it belongs to")
    if store_id != pinned:
        raise CartMutationRefused(f"the live cart belongs to store {store_id}, but store {pinned} is pinned")

    intent = str(fulfillment.get("intent") or "").strip().lower()
    if not intent:
        raise CartMutationRefused("the cart did not say whether it is for delivery or pickup")
    if intent not in DELIVERY_INTENTS:
        raise CartMutationRefused(f"this is a {intent} cart, and this tool only changes delivery carts")


def assert_line_is_addressable(line: dict[str, Any]) -> None:
    """The observed request needs both ids, exactly, and neither may be blank."""
    for field in ("item_id", "offer_id"):
        if not str(line.get(field) or "").strip():
            raise CartMutationRefused(
                f"this cart line has no {field}, so the exact item cannot be identified"
            )


def unmet_contracts() -> list[str]:
    """Everything still unobserved that this action would have to assume.

    A list rather than a boolean so the refusal can say which, and so adding a
    third gate later is a line here rather than a rewrite of the caller.
    """
    missing = []
    if ACCEPTED_RESPONSE_SHAPE is None:
        missing.append(NO_RESPONSE_CONTRACT_REASON)
    if not PER_LINE_DELIVERY_PROVEN:
        missing.append(NO_PER_LINE_DELIVERY_REASON)
    return missing


def not_ready_message(reasons: list[str]) -> str:
    """One heading, then the reasons. Not two sentences glued together.

    Joining whole messages meant each one repeated "changing the cart is not
    enabled yet", so a person with two unmet contracts was told twice, in one
    paragraph, and had to read both to notice they said different things.
    """
    if len(reasons) == 1:
        return f"{NOT_READY_PREFIX}: {reasons[0]}. Nothing was sent."
    listed = "".join(f"\n  - {reason}" for reason in reasons)
    return f"{NOT_READY_PREFIX}, for {len(reasons)} reasons:{listed}\nNothing was sent."


def assert_ready() -> None:
    missing = unmet_contracts()
    if missing:
        raise CartMutationRefused(not_ready_message(missing))


def action_cart_change(request: dict[str, Any]) -> dict[str, Any]:
    """Worker entry point for every cart quantity change.

    One handler for four verbs. The verbs differ only in the number they ask
    for, and giving each its own path would be four chances for one of them to
    skip a check.
    """
    item_id, delta, remove = _requested(request)

    # Refuse before opening a browser. Both contracts, checked separately, so
    # that evidence for one cannot be mistaken for evidence for the other.
    assert_ready()

    options = browser.BrowserOptions.from_request(request.get("options", {}))
    pinned_store = (request.get("options") or {}).get("pinned_store")

    sent = False
    try:
        with browser.open_session(options) as session:
            # A cart read taken now, not one cached from an earlier command.
            # The window between this read and the write below is the
            # lost-update window: narrow on purpose, and not zero.
            protocol.progress("reading the cart")
            payload = cart_module.capture_cart(session)
            before_cart = cart_parse.parse_cart(payload)

            assert_deliverable_from_the_pinned_store(before_cart, pinned_store)

            line = find_line(before_cart, item_id)
            if line is None:
                raise CartMutationRefused(
                    f"item {item_id} is not in the cart; "
                    "`walmart cart add` can only change lines that are already there"
                )
            _assert_first_party(line)
            assert_line_is_addressable(line)

            before = quantity_of(before_cart, item_id)
            if before is None:
                raise CartMutationRefused(
                    "the cart did not report a quantity for this item, so no change can be derived"
                )
            target = target_quantity(before, delta, remove)
            if target == before:
                return _result(item_id, before, before, target, APPLIED, "", changed=False)

            body = build_update_items_body(cart_id_of(payload), line, target)

            # Everything that can still refuse happens before the flag flips.
            # The flag means "a request may be in flight", and it must not be
            # set by anything that can turn around and raise instead.
            destination = checked_destination(body)

            protocol.progress("updating the cart")
            sent = True
            status, response = send_update(session, destination, body)
            ack = acknowledgement(status, response)

            # Read the cart again from the site, never from the response. What
            # we want to know is what the cart holds, and only the cart can say.
            protocol.progress("checking the cart")
            after = quantity_of(_reread(session), item_id)
    except CartMutationRefused:
        raise
    except Exception as exc:
        # Before the request went out, an error is a clean failure: nothing was
        # sent, so nothing can have changed. Afterwards it is genuinely
        # unverified and must stay that way.
        if sent:
            raise
        raise PreflightFailed(exc) from exc

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
