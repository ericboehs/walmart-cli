"""The search action's knowable halves: parameters in, response out."""

from __future__ import annotations

import pytest

import challenge
import search

# --- Parameters --------------------------------------------------------------


def test_a_query_is_required():
    with pytest.raises(ValueError):
        search.normalize_params({"params": {"query": "   "}})


def test_defaults_are_filled_in():
    params = search.normalize_params({"params": {"query": "whole milk"}})

    assert params == {"query": "whole milk", "page": 1, "limit": search.DEFAULT_LIMIT}


def test_the_query_is_trimmed_but_not_otherwise_touched():
    params = search.normalize_params({"params": {"query": "  Great Value 2% milk  "}})

    assert params["query"] == "Great Value 2% milk"


def test_page_and_limit_are_read_as_numbers():
    params = search.normalize_params({"params": {"query": "milk", "page": "3", "limit": "5"}})

    assert params["page"] == 3
    assert params["limit"] == 5


@pytest.mark.parametrize("bad", ["zero", "1.5", "-2", "0"])
def test_a_page_that_is_not_a_positive_number_is_refused(bad):
    with pytest.raises(ValueError):
        search.normalize_params({"params": {"query": "milk", "page": bad}})


def test_an_absurd_limit_is_refused_rather_than_silently_capped():
    # Silently returning 20 when the user asked for 5000 is a lie about what
    # the command did.
    with pytest.raises(ValueError):
        search.normalize_params({"params": {"query": "milk", "limit": 5000}})


# --- The blocked seam --------------------------------------------------------


def test_the_request_builder_refuses_to_guess():
    with pytest.raises(NotImplementedError) as raised:
        search.build_request({"query": "milk", "page": 1, "limit": 20})

    assert "variables" in str(raised.value)


def test_the_blocker_names_how_to_unblock_it():
    # An error that does not say what would fix it wastes the next person's
    # afternoon.
    assert "recon.py" in search.REQUEST_CONTRACT_UNKNOWN
    assert "search" in search.REQUEST_CONTRACT_UNKNOWN


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
