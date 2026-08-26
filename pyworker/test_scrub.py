"""Scrubber tests.

The pattern list below is a deliberate *subset* of the one in
test/walmart/fixture_hygiene_test.rb: these are the categories the scrubber
itself removes. The full gate lives in Ruby, and scripts/promote-fixture.py
mirrors it label for label (asserted by a Ruby test).
"""

from __future__ import annotations

import json
import re

import scrub

# A subset of the Ruby gate: the categories scrub.py is responsible for.
HYGIENE_PATTERNS = {
    "email address": re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.IGNORECASE),
    "US phone number": re.compile(r"\b(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"),
    "payment card digits": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "cookie header": re.compile(r"\bset-cookie\s*:", re.IGNORECASE),
}

ALLOWED_PLACEHOLDERS = (scrub.EMAIL_PLACEHOLDER, scrub.PHONE_PLACEHOLDER, scrub.REDACTED)


def assert_clean(text: str) -> None:
    for placeholder in ALLOWED_PLACEHOLDERS:
        text = text.replace(placeholder, "")
    for label, pattern in HYGIENE_PATTERNS.items():
        assert not pattern.search(text), f"{label} survived scrubbing: {text!r}"


def test_email_is_replaced_with_a_placeholder():
    out = scrub.scrub_text("order confirmation sent to eric.person@gmail.com")

    assert scrub.EMAIL_PLACEHOLDER in out
    assert_clean(out)


def test_phone_numbers_are_replaced():
    for raw in ["918-555-0134", "(918) 555-0134", "+1 918 555 0134", "918.555.0134"]:
        assert_clean(scrub.scrub_text(f"call {raw}"))


def test_card_digits_and_last_four_go_away():
    assert_clean(scrub.scrub_text("Visa 4111 1111 1111 1111"))
    assert scrub.REDACTED in scrub.scrub_text("Visa ending in 4242")


def test_street_addresses_are_removed():
    assert scrub.REDACTED in scrub.scrub_text("Deliver to 1234 Country Road, Somewhere")


def test_bearer_tokens_and_jwts_are_removed():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"  # gitleaks:allow

    assert token not in scrub.scrub_text(f"Authorization: Bearer {token}")


def test_sensitive_json_keys_are_replaced_at_any_depth():
    payload = {
        "order": {
            "id": "1234",
            "customerId": "cust-9",
            "shipTo": {"firstName": "Eric", "addressLine1": "1234 Country Road", "city": "Somewhere"},
            "items": [{"name": "Great Value Whole Milk", "price": 3.12}],
        }
    }
    cleaned = scrub.scrub_json(payload)

    assert cleaned["order"]["id"] == "1234"
    assert cleaned["order"]["customerId"] == scrub.REDACTED
    assert cleaned["order"]["shipTo"]["firstName"] == scrub.REDACTED
    assert cleaned["order"]["shipTo"]["city"] == scrub.REDACTED
    # Item titles are the whole point of a fixture, but they arrive under
    # "name" too, so the parser must read titles from a non-sensitive key.
    assert cleaned["order"]["items"][0]["name"] == scrub.REDACTED
    assert cleaned["order"]["items"][0]["price"] == 3.12


def test_credential_headers_are_dropped_entirely():
    headers = {"Cookie": "CID=abc", "Set-Cookie": "x=1", "Authorization": "Bearer x", "Accept": "*/*"}
    cleaned = scrub.scrub_headers(headers)

    assert cleaned == {"Accept": "*/*"}


def test_extra_literals_remove_values_never_written_to_the_repo():
    out = scrub.scrub_text("Hello Ericson Q. Personname", extra_literals=["Ericson Q. Personname"])

    assert "Ericson" not in out


def test_scrub_document_keeps_json_shape():
    raw = json.dumps({"email": "a@b.com", "items": [{"id": 1}]})
    out = scrub.scrub_document(raw)
    parsed = json.loads(out)

    assert parsed["email"] == scrub.REDACTED
    assert parsed["items"][0]["id"] == 1


def test_scrub_document_falls_back_to_text_for_html():
    html = "<div>contact a@b.com</div>"

    assert_clean(scrub.scrub_document(html))


def test_scrub_document_survives_malformed_json():
    out = scrub.scrub_document('{"email": "a@b.com"')

    assert_clean(out)


# --- URL scrubbing ----------------------------------------------------------
#
# From the first live capture: a /shoppable-basket navigation carried a
# 7,044-character signed token in its query string. Bodies and headers were
# scrubbed; the URL was recorded verbatim and its first 120 characters became a
# filename. A URL is data, not metadata.


def test_a_url_without_a_query_survives_intact():
    url = "https://www.walmart.com/orchestra/home/graphql/getCart/abc123"

    assert scrub.scrub_url(url) == url


def test_the_path_is_preserved_because_it_is_the_useful_part():
    scrubbed = scrub.scrub_url("https://www.walmart.com/ip/Some-Item/10324290?athbdg=L1600")

    assert "/ip/Some-Item/10324290" in scrubbed


def test_a_long_opaque_query_value_is_replaced():
    token = "eyJ" + "a" * 7000
    scrubbed = scrub.scrub_url(f"https://www.walmart.com/shoppable-basket?data={token}")

    assert token not in scrubbed
    assert "REDACTED" in scrubbed


def test_a_sensitive_query_name_is_replaced_however_short():
    scrubbed = scrub.scrub_url("https://www.walmart.com/x?email=eric@example.com&sig=deadbeef")

    assert "eric@example.com" not in scrubbed
    assert "deadbeef" not in scrubbed


def test_short_harmless_query_values_are_kept_for_readability():
    scrubbed = scrub.scrub_url("https://www.walmart.com/search?q=whole+milk&page=2")

    assert "whole" in scrubbed
    assert "page=2" in scrubbed


def test_textual_rules_still_apply_inside_a_query():
    scrubbed = scrub.scrub_url("https://www.walmart.com/x?note=call+555-123-4567")

    assert "555-123-4567" not in scrubbed


def test_an_empty_url_is_not_a_crash():
    assert scrub.scrub_url(None) == ""
    assert scrub.scrub_url("") == ""


# --- Postal codes and store ids ---------------------------------------------
#
# The second live capture recorded zipcode=<zip> unredacted in 20 typeahead
# URLs. Only names like "address" were covered; a bare five-digit zip did not
# look like anything. It is a person's location to within a few miles.


def test_a_zip_code_in_a_query_is_redacted():
    scrubbed = scrub.scrub_url("https://www.walmart.com/typeahead/v3/complete?term=milk&zipcode=73754")

    assert "73754" not in scrubbed
    assert "term=milk" in scrubbed


def test_postal_code_spellings_are_all_covered():
    for name in ("zip", "zipCode", "postalCode", "postal_code", "postcode"):
        scrubbed = scrub.scrub_url(f"https://www.walmart.com/x?{name}=73754")
        assert "73754" not in scrubbed, name


def test_a_nested_zip_is_redacted_in_a_body():
    scrubbed = scrub.scrub_json({"store": {"zipCode": "73754", "id": "0100"}})

    assert scrubbed["store"]["zipCode"] == scrub.REDACTED


def test_a_store_id_is_location_by_another_name():
    # Which store you shop is where you live. The key survives because it is
    # part of the contract; the value does not, because it is not.
    scrubbed = scrub.scrub_json({"storeId": "5260", "sortBy": "best_match"})

    assert scrubbed["storeId"] == scrub.REDACTED
    assert scrubbed["sortBy"] == "best_match"


# --- Structured query values -------------------------------------------------
#
# Length alone was the wrong test. Walmart's persisted Search call carries a
# ~4,000-character `variables` object holding the query, page, sort, and store
# context -- the whole request contract -- and the blanket length rule discarded
# it exactly as if it were the 7,044-character signed token that motivated the
# rule. Both are long. Only one is a secret.


def _variables(**extra):
    payload = {"query": "cheese pizza", "page": 2, "sort": "best_match", "prg": "desktop"}
    payload.update(extra)
    return json.dumps(payload)


def test_a_json_query_value_keeps_its_shape():
    url = f"https://www.walmart.com/orchestra/snb/graphql/Search/abc/search?variables={_variables()}"

    scrubbed = scrub.scrub_url(url)

    assert "cheese+pizza" in scrubbed or "cheese%20pizza" in scrubbed
    assert "page" in scrubbed
    assert "best_match" in scrubbed


def test_a_big_variables_blob_survives_as_structure():
    # Shaped like the real one: long, but made of contract, not of secret.
    padding = {f"flag{n}": f"value-{n}" for n in range(200)}
    url = f"https://www.walmart.com/search/x?variables={_variables(**padding)}"

    scrubbed = scrub.scrub_url(url)

    assert len(_variables(**padding)) > 4000
    assert "-chars" not in scrubbed
    assert "cheese" in scrubbed


def test_sensitive_fields_inside_a_json_value_are_still_removed():
    url = f"https://www.walmart.com/x?variables={_variables(zipCode='73754', customerId='C-9')}"

    scrubbed = scrub.scrub_url(url)

    assert "73754" not in scrubbed
    assert "C-9" not in scrubbed
    assert "cheese" in scrubbed


def test_a_long_leaf_inside_a_structure_is_bounded():
    url = f"https://www.walmart.com/x?variables={_variables(pxCookie='z' * 4000)}"

    scrubbed = scrub.scrub_url(url)

    assert "zzzz" not in scrubbed
    assert "4000-chars" in scrubbed
    assert "cheese" in scrubbed


def test_a_long_value_that_is_not_json_is_still_redacted_wholesale():
    token = "eyJ" + "a" * 7000
    scrubbed = scrub.scrub_url(f"https://www.walmart.com/shoppable-basket?data={token}")

    assert token not in scrubbed
    assert "REDACTED" in scrubbed


def test_malformed_json_is_treated_as_an_opaque_value():
    scrubbed = scrub.scrub_url("https://www.walmart.com/x?variables=%7Bnot+json" + "x" * 200)

    assert "REDACTED" in scrubbed


# --- Device identifiers ------------------------------------------------------


def test_the_device_fingerprint_header_is_dropped():
    headers = scrub.scrub_headers({"device_profile_ref_id": "ilh-6nf2cvxek", "x-o-platform": "rweb"})

    assert "device_profile_ref_id" not in headers
    assert headers["x-o-platform"] == "rweb"


def test_per_request_trace_ids_are_kept_because_they_are_contract():
    headers = scrub.scrub_headers({"traceparent": "00-18cf3a-c9a5fe-00"})

    assert "traceparent" in headers


# --- Bot-protection identifiers ---------------------------------------------
#
# The 412 block payload from the search capture carried uuid and vid at the top
# level, and carried them again inside the query strings of blockScript and
# redirectUrl, where key-based scrubbing cannot see them.


def test_a_visitor_id_key_is_redacted():
    scrubbed = scrub.scrub_json({"uuid": "9f0b-1", "vid": "aa11", "appId": "PXabc"})

    assert scrubbed["uuid"] == scrub.REDACTED
    assert scrubbed["vid"] == scrub.REDACTED


def test_the_app_id_survives_because_it_identifies_the_site_not_the_person():
    assert scrub.scrub_json({"appId": "PXu6b0qd2s"})["appId"] == "PXu6b0qd2s"


def test_identifiers_inside_an_embedded_url_are_redacted():
    payload = {"blockScript": "/px/captcha.js?uuid=9f0b-1&vid=aa11&a=c"}

    scrubbed = scrub.scrub_json(payload)["blockScript"]

    assert "9f0b-1" not in scrubbed
    assert "aa11" not in scrubbed
    assert "a=c" in scrubbed


def test_the_shape_of_an_embedded_url_survives_redaction():
    # The path is the contract; only the values go.
    scrubbed = scrub.scrub_json({"redirectUrl": "/blocked?uuid=9f0b-1"})["redirectUrl"]

    assert scrubbed.startswith("/blocked?uuid=")


def test_embedded_identifiers_are_caught_in_plain_text_too():
    scrubbed = scrub.scrub_text("redirect to /blocked?vid=aa11&uuid=9f0b-1 now")

    assert "aa11" not in scrubbed
    assert "9f0b-1" not in scrubbed


def test_a_word_ending_in_uuid_is_not_treated_as_an_identifier():
    # "productGuid=" would be a different field; only the named ones are cut.
    assert "keepme" in scrub.scrub_text("myuuid=keepme")
