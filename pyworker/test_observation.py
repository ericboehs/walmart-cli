"""The closed vocabulary, and the proof that nothing else gets through.

These tests are the privacy boundary. The tool they protect does not exist yet
-- see docs/DOM-OBSERVATION.md -- and this file comes first deliberately: the
last tool aimed at the cart DOM was written first and reviewed second.

The needles are the values a real run would hold in memory: an item id, a
quantity, a store id, a product name. Every test here asks the same question in
a different place. Can this value reach an output?
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import observation as obs  # noqa: E402

# Synthetic. A canary taken from a real cart would only prove what that cart
# happened to contain, and would put the thing we are protecting into the
# repository in order to protect it.
ITEM_ID = "33282303"
QUANTITY = 3
STORE_ID = "499"
PRODUCT = "Great Value Whole Milk, 1 Gallon"
NEEDLES = (ITEM_ID, str(QUANTITY), STORE_ID, PRODUCT)


# --- the writer takes a closed type, not a filtered dict ------------------------------


def test_a_string_never_serialises():
    """The whole boundary in one assertion: page content is a str."""
    with pytest.raises(obs.Unserialisable):
        obs.serialise({"tag": PRODUCT})


def test_a_bare_int_never_serialises():
    """The quantity is an int needle.

    A writer that accepts plain integers cannot tell a cardinality from Eric's
    quantity, so it accepts neither and the walker wraps what it means.
    """
    with pytest.raises(obs.Unserialisable):
        obs.serialise({"matches": QUANTITY})


def test_a_count_serialises_because_the_walker_made_it():
    assert obs.serialise({"matches": obs.Count(2)}) == {"matches": 2}


def test_an_unknown_key_is_rejected_not_dropped():
    """Keys are values. A key taken from the page is page content."""
    with pytest.raises(obs.Unserialisable) as exc:
        obs.serialise({PRODUCT: obs.Outcome.OBSERVED})

    assert exc.value.code == "unknown_key"


@pytest.mark.parametrize(
    "value",
    [3.5, None, object(), b"bytes", ["nested", "strings"], {"tag": [PRODUCT]}],
)
def test_nothing_outside_the_vocabulary_serialises(value):
    with pytest.raises(obs.Unserialisable):
        obs.serialise({"tag": value} if not isinstance(value, dict) else value)


def test_a_rejection_never_says_what_it_rejected():
    """The one egress that fires when something nobody expected arrives.

    `got {value!r}` here would be the most likely leak in the tool, because it
    is on the path taken precisely when an unanticipated value shows up.
    """
    for needle in NEEDLES:
        with pytest.raises(obs.Unserialisable) as exc:
            obs.serialise({"tag": needle})

        assert needle not in str(exc.value)
        assert needle not in repr(exc.value)
        assert exc.value.code in ("unknown_type", "unknown_key")


def test_booleans_survive_as_booleans():
    """bool is an int in Python, so a stray True must not become 1."""
    assert obs.serialise({"disabled": True, "inside_line": False}) == {
        "disabled": True,
        "inside_line": False,
    }


# --- the vocabularies are closed ------------------------------------------------------


def test_an_unknown_tag_is_redacted_rather_than_echoed():
    """A custom element name is identifier-shaped page content, like a dynamic key."""
    assert obs.tag_or_redacted("wm-item-33282303") is obs.Sentinel.REDACTED
    assert obs.tag_or_redacted("DIV") is obs.Tag.DIV


def test_an_unknown_role_is_redacted_rather_than_echoed():
    assert obs.role_or_redacted(PRODUCT) is obs.Sentinel.REDACTED
    assert obs.role_or_redacted("button") is obs.Role.BUTTON


def test_the_probe_list_is_fixed_at_five():
    """Named in advance so nobody adds one after seeing live output.

    Widening the list after a look is how a guess becomes a finding.
    """
    assert [a.value for a in obs.Attribute] == [
        "data-item-id",
        "data-usitemid",
        "data-testid",
        "id",
        "href",
    ]


def test_control_names_are_generic_enough_to_be_nobodys_business():
    for name in obs.ControlName:
        assert name.value in (
            "Increase quantity",
            "Decrease quantity",
            "Increase",
            "Decrease",
            "Add one",
            "Remove one",
        )


def test_a_count_refuses_a_bool_pretending_to_be_a_number():
    with pytest.raises(TypeError):
        obs.Count(True)


def test_a_count_refuses_a_negative():
    with pytest.raises(TypeError):
        obs.Count(-1)


# --- the property: no needle reaches any egress ---------------------------------------


@pytest.mark.parametrize("needle", NEEDLES)
def test_no_needle_survives_a_round_trip_through_the_writer(needle):
    """Fuzz-ish: whatever position a needle is put in, it does not come out.

    Not exhaustive, and not claimed to be. It is the alarm, and the closed type
    is the guarantee -- a distinction this repository has already got wrong
    once, in the comment above a denylist.
    """
    attempts = [
        {"tag": needle},
        {"name": needle},
        {"line": {"attribute": needle}},
        {"ancestors": [{"tag": needle}]},
        {needle: obs.Outcome.OBSERVED},
        {"quantity": {"relation": needle}},
    ]
    for attempt in attempts:
        with pytest.raises(obs.Unserialisable):
            obs.serialise(attempt)


def test_a_whole_document_of_findings_contains_no_needle():
    """The shape a real observation would produce, serialised and searched."""
    document = {
        "outcome": obs.Outcome.OBSERVED,
        "line": {
            "tag": obs.Tag.DIV,
            "role": obs.Role.LISTITEM,
            "attribute": obs.Attribute.DATA_ITEM_ID,
            "relation": obs.Relation.EQUALS_ITEM_ID,
            "matches": obs.Count(1),
            "ancestors": [
                {"tag": obs.Tag.LI, "role": obs.Role.LISTITEM, "depth": obs.Depth(1)},
                {"tag": obs.Tag.UL, "role": obs.Role.LIST, "depth": obs.Depth(2)},
            ],
        },
        "control": {
            "role": obs.Role.BUTTON,
            "name": obs.ControlName.DECREASE_QUANTITY,
            "inside_line": True,
            "disabled": False,
        },
        "quantity": {"relation": obs.Relation.EQUALS_QUANTITY, "depth": obs.Depth(2)},
    }

    text = json.dumps(obs.serialise(document))

    for needle in NEEDLES:
        assert needle not in text
    assert "Decrease quantity" in text, "the safe vocabulary should survive"


def test_the_document_says_which_relation_not_which_value():
    """The relation is the finding. We knew the value before we looked."""
    text = json.dumps(obs.serialise({"quantity": {"relation": obs.Relation.EQUALS_QUANTITY}}))

    assert "equals_quantity" in text
    assert str(QUANTITY) not in text
