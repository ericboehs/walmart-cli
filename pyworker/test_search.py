"""The search action's knowable halves: parameters in, response out."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

import browser
import challenge
import search

# --- Parameters --------------------------------------------------------------


def test_a_query_is_required():
    with pytest.raises(ValueError):
        search.normalize_params({"params": {"query": "   "}})


def test_defaults_are_filled_in():
    normalized = search.normalize_params({"params": {"query": "whole milk"}})

    assert normalized == {
        "query": "whole milk",
        "page": 1,
        "limit": search.DEFAULT_LIMIT,
        "sort": search.DEFAULT_SORT,
        "store_slot_booked": "",
    }


def test_the_query_is_trimmed_but_not_otherwise_touched():
    normalized = search.normalize_params({"params": {"query": "  Great Value 2% milk  "}})

    assert normalized["query"] == "Great Value 2% milk"


def test_page_and_limit_are_read_as_numbers():
    normalized = search.normalize_params({"params": {"query": "milk", "page": "3", "limit": "5"}})

    assert normalized["page"] == 3
    assert normalized["limit"] == 5


@pytest.mark.parametrize("bad", ["zero", "1.5", "-2", "0"])
def test_a_page_that_is_not_a_positive_number_is_refused(bad):
    with pytest.raises(ValueError):
        search.normalize_params({"params": {"query": "milk", "page": bad}})


def test_an_absurd_limit_is_refused_rather_than_silently_capped():
    # Silently returning 20 when the user asked for 5000 is a lie about what
    # the command did.
    with pytest.raises(ValueError):
        search.normalize_params({"params": {"query": "milk", "limit": 5000}})


# --- The request -------------------------------------------------------------
#
# Recovered by read-only observation of a signed-in session. The values here are
# copied, not improved: the flag set is what the web app sends, and changing one
# to "get more data" changes the response shape our parser was built against.


def params(**overrides):
    base = {"query": "bananas", "page": 1, "limit": 20, "sort": "best_match", "store_slot_booked": ""}
    base.update(overrides)
    return base


def variables_of(url):
    return json.loads(parse_qs(urlsplit(url).query)["variables"][0])


def test_the_request_is_a_persisted_query_get():
    url = search.build_request(params())

    assert url.startswith("https://www.walmart.com/orchestra/snb/graphql/Search/")
    assert search.SEARCH_PERSISTED_HASH in url
    assert "?variables=" in url


def test_the_query_and_page_travel_in_the_variables():
    variables = variables_of(search.build_request(params(query="whole milk", page=3)))

    assert variables["query"] == "whole milk"
    assert variables["page"] == 3


def test_the_page_size_is_the_observed_forty():
    variables = variables_of(search.build_request(params()))

    assert variables["ps"] == 40
    assert variables["limit"] == 40


def test_scalars_are_consistent_across_the_three_places_they_appear():
    # The real request repeats them; drift between copies is the kind of thing
    # a server-side validator notices and a human never does.
    variables = variables_of(search.build_request(params(query="eggs", page=2)))

    for section in ("searchParams", "fitmentSearchParams"):
        assert variables[section]["query"] == "eggs"
        assert variables[section]["page"] == 2


def test_observed_flags_are_copied_not_improved():
    variables = variables_of(search.build_request(params()))

    assert variables["enableUnifiedSchema"] is True
    # This one is why sellerType was null in the response we built the parser
    # against. Flipping it would change the shape out from under search_parse.
    assert variables["enableSellerType"] is False
    assert variables["enableUnifiedProductFragment"] is False


def test_store_slot_booked_is_sent_empty_not_omitted():
    # Observed empty in ordinary signed-in traffic. The page sends the key, so
    # we send the key: a missing field and an empty one are different requests.
    variables = variables_of(search.build_request(params()))

    assert "storeSlotBooked" in variables
    assert variables["storeSlotBooked"] == ""


def test_translation_is_sent_as_null():
    variables = variables_of(search.build_request(params()))

    assert "translation" in variables["additionalQueryParams"]
    assert variables["additionalQueryParams"]["translation"] is None


def test_no_device_identifier_is_synthesized():
    # The page supplies its own. One we invented would be both a lie and a
    # fingerprint we chose to carry around.
    header_names = {name.lower() for name in search.REQUEST_HEADERS}

    assert "device_profile_ref_id" not in header_names
    assert not any("device" in name for name in header_names)


def test_the_operation_headers_are_the_stable_ones():
    assert search.REQUEST_HEADERS["X-APOLLO-OPERATION-NAME"] == "Search"
    assert search.REQUEST_HEADERS["x-o-platform"] == "rweb"


def test_the_persisted_hash_can_be_pinned_without_a_code_change():
    url = search.build_request(params(), persisted_hash="deadbeef")

    assert "/Search/deadbeef/search" in url


# --- Status handling ----------------------------------------------------------


def ok_result(items=None):
    payload = {
        "data": {
            "search": {
                "searchResult": {
                    "itemStacks": [{"itemsV2": items if items is not None else [{"usItemId": "1"}]}]
                }
            }
        }
    }
    return {"status": 200, "body": json.dumps(payload)}


def test_a_healthy_result_is_parsed():
    data = search.handle_result(ok_result())

    assert [item["item_id"] for item in data["items"]] == ["1"]


def test_a_block_outranks_the_status_code():
    body = json.dumps({"blockScript": "/px/c.js", "jsClientSrc": "/px/x.js", "appId": "PXtest"})

    with pytest.raises(challenge.Blocked):
        search.handle_result({"status": 200, "body": body})


def test_an_unauthenticated_response_asks_for_a_login():
    with pytest.raises(search.NotSignedIn) as raised:
        search.handle_result({"status": 401, "body": ""})

    assert raised.value.code == "login_required"


def test_a_missing_persisted_query_reports_a_stale_contract():
    # Walmart rotates these when they ship a build. That must not read as a
    # parser bug, and the message says exactly what to do.
    with pytest.raises(search.StaleContract) as raised:
        search.handle_result({"status": 404, "body": ""})

    assert raised.value.code == "contract_stale"
    assert "recon.py" in str(raised.value)


def test_an_unexpected_status_is_reported_with_its_number():
    with pytest.raises(RuntimeError) as raised:
        search.handle_result({"status": 503, "body": "upstream down"})

    assert "503" in str(raised.value)


def test_a_non_json_two_hundred_is_not_silently_empty():
    with pytest.raises(RuntimeError):
        search.handle_result({"status": 200, "body": "<html>maintenance</html>"})


def test_a_throttle_is_not_reported_as_a_bot_block():
    # Telling a throttled user to go clear a puzzle that does not exist sends
    # them hunting for a challenge window that will never appear.
    with pytest.raises(search.RateLimited) as raised:
        search.handle_result({"status": 429, "body": ""})

    assert raised.value.code == "rate_limited"
    assert not isinstance(raised.value, challenge.Blocked)


def test_a_throttle_advises_waiting_rather_than_retrying():
    assert "wait" in search.RATE_LIMITED.lower()


# --- Interpreting a response --------------------------------------------------


def test_a_healthy_response_is_parsed():
    payload = {
        "data": {
            "search": {
                "searchResult": {
                    "itemStacks": [{"itemsV2": [{"usItemId": "1", "name": "Milk"}]}],
                    "hasMorePages": False,
                }
            }
        }
    }

    parsed = search.interpret_response(payload, status=200)

    assert [item["item_id"] for item in parsed["items"]] == ["1"]


def test_a_block_is_not_parsed_as_an_empty_result():
    # This is the whole reason interpret_response exists: reporting "no matches
    # for whole milk" to someone who is actually locked out is a lie that sends
    # them shopping somewhere else.
    payload = {"blockScript": "/px/c.js", "jsClientSrc": "/px/x.js", "appId": "PXtest"}

    with pytest.raises(challenge.Blocked):
        search.interpret_response(payload, status=412)


def test_the_block_check_happens_before_parsing():
    payload = {
        "blockScript": "/px/c.js",
        "jsClientSrc": "/px/x.js",
        "data": {"search": {"searchResult": {"itemStacks": []}}},
    }

    with pytest.raises(challenge.Blocked):
        search.interpret_response(payload, status=412)


# --- The action end to end (no browser, no network) ---------------------------


class FakeSession:
    """Records what was fetched and hands back a canned page response."""

    def __init__(self, result):
        self.result = result
        self.fetched = []

    def fetch_same_origin(self, url, headers=None, **_kwargs):
        self.fetched.append((url, headers))
        return dict(self.result)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


OPTIONS = {"profile_dir": "/tmp/profile", "profile_lock": "/tmp/profile.lock"}


def install_session(monkeypatch, session):
    monkeypatch.setattr(browser, "open_session", lambda _options: session)


def search_request(**params):
    return {"action": "search", "options": OPTIONS, "params": params}


def test_the_action_issues_one_request_and_returns_items(monkeypatch):
    session = FakeSession(ok_result([{"usItemId": "1", "name": "Bananas"}]))
    install_session(monkeypatch, session)

    data = search.action_search(search_request(query="bananas", transport="graphql"))

    assert len(session.fetched) == 1
    assert data["query"] == "bananas"
    assert [item["name"] for item in data["items"]] == ["Bananas"]


def test_the_limit_trims_results_without_changing_the_request(monkeypatch):
    # Walmart's page size is fixed at 40; --limit is ours, applied after.
    items = [{"usItemId": str(n), "name": f"item {n}"} for n in range(10)]
    session = FakeSession(ok_result(items))
    install_session(monkeypatch, session)

    data = search.action_search(search_request(query="milk", limit=3, transport="graphql"))

    assert len(data["items"]) == 3
    assert variables_of(session.fetched[0][0])["limit"] == 40


def test_a_bad_parameter_never_opens_a_browser(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("parameters must be validated before a browser opens")

    monkeypatch.setattr(browser, "open_session", explode)

    with pytest.raises(ValueError):
        search.action_search(search_request(query="milk", page="0"))


def test_a_blocked_search_stops_instead_of_retrying(monkeypatch):
    body = json.dumps({"blockScript": "/px/c.js", "jsClientSrc": "/px/x.js", "appId": "PXtest"})
    session = FakeSession({"status": 412, "body": body})
    install_session(monkeypatch, session)

    with pytest.raises(challenge.Blocked):
        search.action_search(search_request(query="milk", transport="graphql"))

    assert len(session.fetched) == 1


# --- The server-rendered path (the default) -----------------------------------


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakePageSession:
    """A session that navigates and hands back an embedded payload."""

    def __init__(self, payload=None, status=200, url="https://www.walmart.com/search?q=milk"):
        self.payload = payload
        self.status = status
        self.page = self
        self.navigated = []
        self.evaluated = []
        self.url = url

    def goto(self, url, **_kwargs):
        self.navigated.append(url)
        return FakeResponse(self.status)

    def evaluate(self, script, argument=None):
        self.evaluated.append(script)
        if argument is not None:
            raise AssertionError("the page path takes no arguments")
        return self.payload

    def fetch_same_origin(self, *_args, **_kwargs):
        raise AssertionError("the default path must not synthesize a request")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


SSR_RESULT = {
    "itemStacks": [
        {
            "itemsV2": None,
            # itemsV2 can be null while items holds the products. Reading only
            # the first would report an empty catalog for a full page.
            "items": [{"usItemId": "77", "name": "Bananas", "price": 0.58, "averageWeight": 0.4}],
        }
    ],
    "hasMorePages": True,
    "paginationV2": {"maxPage": 8},
}


def test_the_default_path_navigates_like_a_person(monkeypatch):
    session = FakePageSession(payload=SSR_RESULT)
    install_session(monkeypatch, session)

    data = search.action_search(search_request(query="bananas"))

    assert session.navigated == ["https://www.walmart.com/search?q=bananas"]
    assert [item["name"] for item in data["items"]] == ["Bananas"]


def test_paging_and_sorting_travel_in_the_url(monkeypatch):
    session = FakePageSession(payload=SSR_RESULT)
    install_session(monkeypatch, session)

    search.action_search(search_request(query="milk", page=3, sort="price_low"))

    assert session.navigated == ["https://www.walmart.com/search?q=milk&page=3&sort=price_low"]


def test_the_graphql_transport_is_still_reachable(monkeypatch):
    session = FakeSession(ok_result())
    install_session(monkeypatch, session)

    search.action_search(search_request(query="milk", transport="graphql"))

    assert len(session.fetched) == 1


def test_an_empty_page_is_not_reported_as_an_empty_catalog(monkeypatch):
    session = FakePageSession(payload=None)
    install_session(monkeypatch, session)

    with pytest.raises(RuntimeError) as raised:
        search.action_search(search_request(query="milk"))

    assert "did not ship" in str(raised.value)


def test_landing_on_the_block_page_is_named_as_a_block(monkeypatch):
    # A block page is still a page, and it parses to nothing. Where we landed
    # is the diagnosis; the empty document is only the symptom.
    session = FakePageSession(payload=None, url="https://identity.walmart.com/blocked?ref=1")
    install_session(monkeypatch, session)

    with pytest.raises(challenge.Blocked):
        search.action_search(search_request(query="milk"))


def test_a_throttled_navigation_is_reported_as_a_throttle(monkeypatch):
    session = FakePageSession(payload=SSR_RESULT, status=429)
    install_session(monkeypatch, session)

    with pytest.raises(search.RateLimited):
        search.action_search(search_request(query="milk"))


def test_weighted_produce_is_flagged(monkeypatch):
    # You order two pounds of bananas, not two bananas. averageWeight being
    # present at all is the signal.
    session = FakePageSession(payload=SSR_RESULT)
    install_session(monkeypatch, session)

    item = search.action_search(search_request(query="bananas"))["items"][0]

    assert item["weighted"] is True
    assert item["price"] == 0.58
