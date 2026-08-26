"""Item page parsing and the item action. Payloads written from the contract."""

from __future__ import annotations

import pytest

import browser
import challenge
import item
import item_parse

PRODUCT = {
    "usItemId": "10450114",
    "offerId": "OFFER1",
    "name": "Great Value Whole Milk, 1 Gallon",
    "brand": "Great Value",
    "sellerId": "F55CDC31AB754BB68FE0B39041159D63",
    "sellerName": "Walmart.com",
    "sellerType": "INTERNAL",
    "availabilityStatus": "IN_STOCK",
    "availabilityStatusV2": {"display": "In stock", "value": "IN_STOCK"},
    "fulfillmentType": "STORE",
    "fulfillmentOptions": [
        {"type": "DELIVERY", "availabilityStatus": "IN_STOCK", "displayName": "Delivery"},
        {"type": "PICKUP", "availabilityStatus": "OUT_OF_STOCK", "displayName": "Pickup"},
    ],
    "orderLimit": 12,
    "orderMinLimit": 1,
    "weightIncrement": 1.0,
    "salesUnitType": "EACH",
    "snapEligible": True,
    "isSubstitutionEligible": True,
    "canonicalUrl": "/ip/great-value-whole-milk-1-gallon/10450114",
    "priceInfo": {
        "currentPrice": {"price": 3.12, "priceString": "$3.12"},
        "wasPrice": {"price": 3.48, "priceString": "$3.48"},
        "unitPrice": {"price": 0.024, "priceString": "2.4 ¢/fl oz"},
        "savings": {"price": 0.36, "priceString": "$0.36"},
        "priceDisplayCodes": {"priceDisplayCondition": None, "finalCostByWeight": True},
    },
    "variantCriteria": [],
    "variantsMap": {},
}


def page(**overrides):
    product = {**PRODUCT, **overrides}
    return {"props": {"pageProps": {"initialData": {"data": {"product": product}}}}}


# --- Reading the page ------------------------------------------------------------


def test_an_item_is_flattened():
    parsed = item_parse.parse_item(page())

    assert parsed["item_id"] == "10450114"
    assert parsed["name"] == "Great Value Whole Milk, 1 Gallon"
    assert parsed["price"] == 3.12


def test_the_item_page_price_shape_is_read_on_its_own_terms():
    # currentPrice/wasPrice/unitPrice here, priceLines[].values[] in search.
    # A parser that tries both silently picks the wrong one the day a field
    # goes missing.
    parsed = item_parse.parse_item(page())

    assert parsed["price_display"] == "$3.12"
    assert parsed["was_price"] == 3.48
    assert parsed["unit_price_display"] == "2.4 ¢/fl oz"
    assert parsed["savings"] == 0.36


def test_quantity_limits_are_read_not_assumed():
    parsed = item_parse.parse_item(page())

    assert parsed["max_quantity"] == 12
    assert parsed["min_quantity"] == 1


def test_a_page_without_a_product_is_refused():
    with pytest.raises(ValueError):
        item_parse.parse_item({"props": {"pageProps": {"initialData": {"data": {}}}}})


def test_a_product_without_an_id_is_refused():
    # Everything downstream keys on the item id. A record without one is not a
    # thing you can buy again.
    with pytest.raises(ValueError):
        item_parse.parse_item(page(usItemId=None))


def test_price_display_codes_are_kept_verbatim():
    # They govern whether a price may be shown at all. Inventing a reading of
    # them is how a tool prints a number it was asked not to.
    codes = item_parse.read_prices(PRODUCT["priceInfo"])["codes"]

    assert codes == {"finalCostByWeight": True}


def test_a_missing_price_block_is_not_a_free_item():
    parsed = item_parse.parse_item(page(priceInfo=None))

    assert parsed["price"] is None
    assert parsed["price_display"] is None


# --- First-party proof ----------------------------------------------------------------


def test_walmarts_own_offer_is_first_party():
    assert item_parse.parse_item(page())["first_party"] is True


def test_a_marketplace_offer_is_not():
    parsed = item_parse.parse_item(page(sellerName="Food Service Direct", sellerType="EXTERNAL"))

    assert parsed["first_party"] is False


def test_a_walmart_shaped_name_with_a_marketplace_type_is_not_first_party():
    # "Walmart Restored" and friends carry a Walmart-shaped name while being
    # marketplace sellers. Either signal alone can lie.
    parsed = item_parse.parse_item(page(sellerName="Walmart Restored", sellerType="EXTERNAL"))

    assert parsed["first_party"] is False


def test_an_internal_type_with_a_third_party_name_is_not_first_party():
    parsed = item_parse.parse_item(page(sellerName="Some Reseller", sellerType="INTERNAL"))

    assert parsed["first_party"] is False


def test_the_seller_type_is_reported_because_search_cannot_provide_it():
    # This page is the authority for the check DESIGN.md requires before an add.
    assert item_parse.parse_item(page())["seller_type"] == "INTERNAL"


# --- Variants -----------------------------------------------------------------------


def test_an_item_without_variants_says_so_plainly():
    variants = item_parse.parse_item(page())["variants"]

    assert variants["has_variants"] is False
    assert variants["shape_unobserved"] is False


def test_a_populated_variant_family_is_flagged_as_unobserved():
    # We have never seen one populated, so the nesting is unknown. Saying "we
    # cannot read these" is a better basis for asking the user to choose than a
    # guess that looks like an answer.
    parsed = item_parse.parse_item(
        page(
            variantCriteria=[{"name": "size", "variantList": []}],
            variantsMap={"a": {}, "b": {}},
        )
    )

    assert parsed["variants"]["has_variants"] is True
    assert parsed["variants"]["count"] == 2
    assert parsed["variants"]["criteria_names"] == ["size"]
    assert parsed["variants"]["shape_unobserved"] is True


# --- Fulfillment --------------------------------------------------------------------


def test_fulfillment_options_keep_their_availability():
    options = item_parse.parse_item(page())["fulfillment_options"]

    assert options[0] == {"type": "DELIVERY", "available": True, "label": "Delivery"}
    assert options[1]["available"] is False


# --- Identifying the item --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "10450114",
        "https://www.walmart.com/ip/great-value-whole-milk-1-gallon/10450114",
        "https://www.walmart.com/ip/10450114",
        "/ip/great-value-whole-milk/10450114?athbdg=L1600",
    ],
)
def test_an_item_id_is_found_in_whatever_was_pasted(text):
    # A URL is what a browser gives you; making someone dig the number out by
    # hand is a papercut we can just not have.
    assert item.item_id_from(text) == "10450114"


@pytest.mark.parametrize("text", ["milk", "", "https://www.walmart.com/cp/food/976759", "12"])
def test_something_that_is_not_an_item_is_refused(text):
    with pytest.raises(ValueError):
        item.item_id_from(text)


# --- The action ------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakeSession:
    def __init__(self, payload=None, status=200, url="https://www.walmart.com/ip/10450114"):
        self.payload = payload
        self.status = status
        self.url = url
        self.page = self
        self.navigated = []

    def goto(self, url, **_kwargs):
        self.navigated.append(url)
        return FakeResponse(self.status)

    def evaluate(self, _script, _argument=None):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


REQUEST = {
    "action": "item",
    "options": {"profile_dir": "/tmp/profile", "profile_lock": "/tmp/profile.lock"},
}


def run_item(monkeypatch, session, **params):
    monkeypatch.setattr(browser, "open_session", lambda _options: session)
    return item.action_item({**REQUEST, "params": params})


def test_the_action_navigates_to_the_item_page(monkeypatch):
    session = FakeSession(payload=page())

    parsed = run_item(monkeypatch, session, item="10450114")

    assert session.navigated == ["https://www.walmart.com/ip/10450114"]
    assert parsed["item_id"] == "10450114"


def test_a_url_reaches_the_same_page(monkeypatch):
    session = FakeSession(payload=page())

    run_item(monkeypatch, session, item="https://www.walmart.com/ip/whole-milk/10450114")

    assert session.navigated == ["https://www.walmart.com/ip/10450114"]


def test_a_missing_item_is_reported_as_missing(monkeypatch):
    session = FakeSession(payload=None, status=404)

    with pytest.raises(item.ItemNotFound) as raised:
        run_item(monkeypatch, session, item="10450114")

    assert raised.value.code == "not_found"


def test_a_block_stops_the_read(monkeypatch):
    session = FakeSession(payload=None, url="https://identity.walmart.com/blocked")

    with pytest.raises(challenge.Blocked):
        run_item(monkeypatch, session, item="10450114")


def test_a_throttle_is_reported_as_itself(monkeypatch):
    session = FakeSession(payload=page(), status=429)

    with pytest.raises(item.RateLimited):
        run_item(monkeypatch, session, item="10450114")


def test_an_empty_page_is_not_a_missing_item(monkeypatch):
    # Walmart answered, we could not read it. Reporting "no such item" would
    # send someone looking for a product that exists.
    session = FakeSession(payload=None)

    with pytest.raises(RuntimeError) as raised:
        run_item(monkeypatch, session, item="10450114")

    assert "did not ship" in str(raised.value)


def test_a_bad_argument_never_opens_a_browser(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("parameters must be validated before a browser opens")

    monkeypatch.setattr(browser, "open_session", explode)

    with pytest.raises(ValueError):
        item.action_item({**REQUEST, "params": {"item": "milk"}})


# --- Configuration a mechanism reads ------------------------------------------
#
# The denylist bug (docs/TESTING.md, instance eight) was a list with no test on
# its contents. These are the other lists in this module, audited by asking a
# single question: which direction does the list fail in?
#
# Shrinking WALMART_SELLERS or FIRST_PARTY_SELLER_TYPES fails closed -- an item
# reads as marketplace, and cart add refuses it. Annoying, visible, safe.
# Growing either one fails open: a marketplace seller reads as Walmart's own,
# which is the precise thing "both signals must agree" exists to prevent, and it
# is silent. So the sets are pinned, and an addition has to be a deliberate edit
# rather than a plausible-looking convenience.
#
# TYPE_LABELS and NEXT_PAGE_LABELS are deliberately not pinned. An unrecognized
# fulfillment label is kept verbatim by design, and a missing Next-page label
# stops the walk with a reason and a resumable archive. Both degrade into
# documented, visible behaviour, so a membership test there would be churn that
# fights ordinary maintenance.


def test_the_first_party_seller_names_are_pinned():
    """Growing this set is what fails open, so it may not happen by accident.

    "Walmart Restored" and friends carry a Walmart-shaped name while being
    marketplace storefronts. Anything added here must be a name Walmart itself
    sells under -- if this test is in your way, that is the question to answer.
    """
    assert {"walmart.com", "walmart", "walmart inc"} == item_parse.WALMART_SELLERS


def test_the_first_party_seller_types_are_pinned():
    assert {"internal", "1p", "first_party"} == item_parse.FIRST_PARTY_SELLER_TYPES


def test_a_walmart_shaped_marketplace_name_is_not_first_party():
    # The concrete case the pinned set protects, so the reason survives even if
    # someone decides the assertion above is too strict.
    restored = {"sellerType": "INTERNAL", "sellerName": "Walmart Restored"}

    assert item_parse.is_first_party(restored) is False


def test_every_observed_price_field_is_read():
    """PRICE_KEYS shrinking loses a price silently, which is the same shape.

    The names come from docs/CONTRACTS.md, where the observed priceInfo shape is
    recorded. A missing entry is not a crash; it is a field quietly absent from
    output nobody is comparing against Walmart's page.
    """
    documented = {
        "currentPrice",
        "basePrice",
        "wasPrice",
        "comparisonPrice",
        "unitPrice",
        "dropPrice",
        "savings",
    }

    assert {source for _label, source in item_parse.PRICE_KEYS} == documented
