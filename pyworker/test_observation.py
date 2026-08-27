"""The closed vocabulary, and the proof that nothing else gets through.

These tests are the privacy boundary. The tool they protect does not exist yet
-- see docs/DOM-OBSERVATION.md -- and this file comes first deliberately: the
last thing aimed at the cart DOM was written first and reviewed second.

A note on how the guarantee is stated, because the first version of this file
got it wrong. Searching the output for the needle as a substring proved less
than it looked. It passed because the sample quantity was 3 while the sample
emitted 1 and 2; with a quantity of 2 the assertion could not have told a leak
from a legitimate depth. Coincidence is not a test. The property asserted here
instead is containment: every scalar in the output is a boolean or a member of
the frozen vocabulary. That is decidable, and it does not care what the needle
happens to be.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import observation as obs  # noqa: E402

# Synthetic. A canary from a real cart would prove only what that cart happened
# to contain, and would put the thing being protected into the repository in
# order to protect it.
ITEM_ID = "33282303"
QUANTITY = 2
STORE_ID = "499"
PRODUCT = "Great Value Whole Milk, 1 Gallon"
NEEDLES = (ITEM_ID, str(QUANTITY), STORE_ID, PRODUCT)

VOCABULARY = frozenset(member.value for group in obs.LEAVES + (obs.SerialiseError,) for member in group)


def line(**over):
    fields = {
        "tag": obs.Tag.DIV,
        "role": obs.Role.LISTITEM,
        "attribute": obs.Attribute.DATA_ITEM_ID,
        "relation": obs.Relation.EQUALS_ITEM_ID,
        "matches": obs.Cardinality.ONE,
        "ancestors": (obs.AncestorFinding(obs.Tag.LI, obs.Role.LISTITEM, obs.Depth.D1),),  # a path
    }
    return obs.LineFinding(**{**fields, **over})


def control(**over):
    fields = {
        "role": obs.Role.BUTTON,
        "name": obs.ControlName.DECREASE_QUANTITY,
        "inside_line": True,
        "disabled": False,
    }
    return obs.ControlFinding(**{**fields, **over})


def quantity(**over):
    fields = {"relation": obs.Relation.EQUALS_QUANTITY, "depth": obs.Depth.D2}
    return obs.QuantityFinding(**{**fields, **over})


def observed(**over):
    fields = {"line": line(), "control": control(), "quantity": quantity()}
    return obs.ObservedResult(**{**fields, **over})


def scalars(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from scalars(value)
    elif isinstance(node, list):
        for item in node:
            yield from scalars(item)
    else:
        yield node


# --- the property: every scalar is a token ---------------------------------------------

KEYS = frozenset(
    {"outcome", "refusal", "reason", "limit", "line", "control", "quantity"}
    | {"tag", "role", "attribute", "relation", "matches", "ancestors", "depth"}
    | {"name", "inside_line", "disabled"}
)


@pytest.mark.parametrize(
    "result",
    [
        observed(),
        obs.RefusedResult(obs.Refusal.STORE_DISAGREEMENT),
        obs.InconclusiveResult(obs.Inconclusive.AMBIGUOUS),
        obs.InconclusiveResult(obs.Inconclusive.BOUND_HIT, obs.Limit.NODES),
    ],
)
def test_every_scalar_in_the_output_is_a_token_or_a_boolean(result):
    """The guarantee, stated so it cannot pass by luck."""
    for scalar in scalars(json.loads(obs.render(result))):
        assert scalar in VOCABULARY or scalar in KEYS or type(scalar) is bool, scalar


def test_the_output_carries_no_numbers_at_all():
    """Cardinality and depth are categories, so no JSON number should appear.

    A number in the output is the one thing that could coincide with the
    quantity, and a substring search could not tell the two apart.
    """
    for scalar in scalars(json.loads(obs.render(observed()))):
        assert type(scalar) is not int or type(scalar) is bool
        assert type(scalar) is not float


# --- the writer takes exact types, not a document ---------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        {"outcome": "observed"},
        [{"outcome": "observed"}],
        "observed",
        3,
        None,
        obs.Outcome.OBSERVED,
        line(),
    ],
)
def test_the_writer_takes_nothing_but_a_result(value):
    """There is no generic document to smuggle a key into."""
    with pytest.raises(obs.Unserialisable) as exc:
        obs.serialise(value)

    assert exc.value.code is obs.SerialiseError.UNKNOWN_TYPE


@pytest.mark.parametrize("needle", NEEDLES)
def test_a_finding_field_will_not_hold_a_string(needle):
    for build in (
        lambda: line(tag=needle),
        lambda: line(attribute=needle),
        lambda: control(name=needle),
        lambda: control(inside_line=needle),
        lambda: quantity(depth=needle),
        lambda: obs.AncestorFinding(needle, obs.Role.LIST, obs.Depth.D1),
        lambda: obs.RefusedResult(needle),
        lambda: obs.InconclusiveResult(needle),
    ):
        with pytest.raises(obs.Unserialisable) as exc:
            build()

        assert exc.value.code is obs.SerialiseError.WRONG_FIELD_TYPE


def test_a_bool_is_not_a_stand_in_for_a_token():
    """bool is an int in Python, and `type(x) is` is why that stops mattering."""
    with pytest.raises(obs.Unserialisable):
        line(matches=True)


def test_disabled_must_be_an_actual_bool():
    with pytest.raises(obs.Unserialisable):
        control(disabled=1)


# --- invalid states are unrepresentable, not merely noticed -----------------------------


def test_an_observation_cannot_be_built_from_a_redacted_tag():
    """The deleted driver is why this is a type rule and not a caller expectation."""
    with pytest.raises(obs.Unserialisable) as exc:
        observed(line=line(tag=obs.Sentinel.REDACTED))

    assert exc.value.code is obs.SerialiseError.INCOHERENT_OBSERVATION


@pytest.mark.parametrize(
    "broken",
    [
        lambda: observed(line=line(matches=obs.Cardinality.MANY)),
        lambda: observed(line=line(matches=obs.Cardinality.ZERO)),
        lambda: observed(line=line(matches=obs.Cardinality.OVER_BUDGET)),
        lambda: observed(line=line(relation=obs.Relation.NO_MATCH)),
        lambda: observed(line=line(attribute=obs.Sentinel.NOT_PROBED)),
        lambda: observed(line=line(role=obs.Sentinel.REDACTED)),
        lambda: observed(control=control(name=obs.Sentinel.REDACTED)),
        lambda: observed(control=control(role=obs.Sentinel.REDACTED)),
        lambda: observed(control=control(inside_line=False)),
        lambda: observed(quantity=quantity(relation=obs.Relation.NO_MATCH)),
        lambda: observed(quantity=quantity(depth=obs.Depth.OVER_BUDGET)),
        # An over-budget ancestor is refused too, but by the chain rule, which
        # is the more precise complaint; see the chain tests below.
    ],
)
def test_an_incomplete_observation_is_not_an_observation(broken):
    with pytest.raises(obs.Unserialisable) as exc:
        broken()

    assert exc.value.code is obs.SerialiseError.INCOHERENT_OBSERVATION


def test_a_refusal_cannot_carry_findings():
    with pytest.raises(TypeError):
        obs.RefusedResult(obs.Refusal.NO_CART, line())


def test_an_observation_cannot_carry_a_refusal():
    with pytest.raises(TypeError):
        obs.ObservedResult(line(), control(), quantity(), obs.Refusal.NO_CART)


def test_a_bound_hit_must_name_its_budget():
    with pytest.raises(obs.Unserialisable) as exc:
        obs.InconclusiveResult(obs.Inconclusive.BOUND_HIT)

    assert exc.value.code is obs.SerialiseError.INCOHERENT_INCONCLUSIVE


def test_a_budget_cannot_be_named_by_something_that_is_not_a_bound_hit():
    """The other direction, which is the one that would quietly mislead."""
    with pytest.raises(obs.Unserialisable):
        obs.InconclusiveResult(obs.Inconclusive.NO_MATCH, obs.Limit.NODES)


# --- bounds -----------------------------------------------------------------------------


def test_the_ancestor_chain_is_bounded_by_the_type():
    chain = tuple(
        obs.AncestorFinding(obs.Tag.DIV, obs.Role.GROUP, obs.Depth.D1) for _ in range(obs.MAX_ANCESTORS + 1)
    )
    with pytest.raises(obs.Unserialisable) as exc:
        line(ancestors=chain)

    assert exc.value.code is obs.SerialiseError.TOO_MANY_ANCESTORS


def test_depth_beyond_the_ladder_is_a_token_not_a_number():
    assert obs.Depth.of(9) is obs.Depth.OVER_BUDGET
    assert obs.Depth.of(2) is obs.Depth.D2


def test_cardinality_is_a_category_and_a_huge_count_does_not_become_one():
    assert obs.Cardinality.of(0, budget=20) is obs.Cardinality.ZERO
    assert obs.Cardinality.of(1, budget=20) is obs.Cardinality.ONE
    assert obs.Cardinality.of(7, budget=20) is obs.Cardinality.MANY
    assert obs.Cardinality.of(10**100, budget=20) is obs.Cardinality.OVER_BUDGET


def test_a_cardinality_refuses_a_bool_pretending_to_be_a_number():
    with pytest.raises(obs.Unserialisable):
        obs.Cardinality.of(True, budget=20)


def test_the_byte_cap_is_checked_before_anything_is_written():
    """A cap applied after a write is a cap on the next run."""
    text = obs.render(observed())

    assert len(text.encode()) <= obs.MAX_OUTPUT_BYTES


def test_output_over_the_cap_is_refused_with_the_fixed_code(monkeypatch):
    monkeypatch.setattr(obs, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(obs.Unserialisable) as exc:
        obs.render(observed())

    assert exc.value.code is obs.SerialiseError.OVER_BYTE_BUDGET


# --- the error type is itself closed ------------------------------------------------------


@pytest.mark.parametrize("needle", NEEDLES)
def test_the_error_cannot_be_constructed_with_text(needle):
    """It was constructible with an arbitrary string, which made it an egress."""
    with pytest.raises(TypeError):
        obs.Unserialisable(needle)


def test_the_error_says_only_its_code():
    error = obs.Unserialisable(obs.SerialiseError.UNKNOWN_TYPE)

    assert str(error) == "unknown_type"
    assert error.code is obs.SerialiseError.UNKNOWN_TYPE


# --- mapping page words, including hostile ones --------------------------------------------


class Hostile:
    """An object of the sort a browser library could hand back."""

    def __init__(self, payload):
        self.payload = payload

    def __str__(self):
        raise RuntimeError(self.payload)

    def __repr__(self):
        return self.payload


def test_a_hostile_object_is_redacted_without_being_asked_what_it_is():
    """Calling str() runs code somebody else wrote, and its answer is not evidence."""
    hostile = Hostile(PRODUCT)

    assert obs.tag_or_redacted(hostile) is obs.Sentinel.REDACTED
    assert obs.role_or_redacted(hostile) is obs.Sentinel.REDACTED
    assert obs.control_name_or_redacted(hostile) is obs.Sentinel.REDACTED


@pytest.mark.parametrize("value", [None, 3, b"div", ["div"], Hostile("div")])
def test_only_an_exact_string_is_mapped(value):
    assert obs.tag_or_redacted(value) is obs.Sentinel.REDACTED


def test_an_unknown_tag_is_redacted_rather_than_echoed():
    assert obs.tag_or_redacted("wm-item-33282303") is obs.Sentinel.REDACTED
    assert obs.tag_or_redacted("DIV") is obs.Tag.DIV


def test_an_unknown_role_is_redacted_rather_than_echoed():
    assert obs.role_or_redacted(PRODUCT) is obs.Sentinel.REDACTED
    assert obs.role_or_redacted("button") is obs.Role.BUTTON


def test_a_control_name_is_matched_exactly_and_not_lowered():
    assert obs.control_name_or_redacted("Decrease quantity") is obs.ControlName.DECREASE_QUANTITY
    assert obs.control_name_or_redacted("decrease quantity") is obs.Sentinel.REDACTED


def test_the_probe_list_is_fixed_at_five():
    """Named in advance so nobody adds one after seeing live output."""
    assert [a.value for a in obs.Attribute] == [
        "data-item-id",
        "data-usitemid",
        "data-testid",
        "id",
        "href",
    ]


class Sneaky(str):
    """A str subclass, which is what `isinstance` would have let through."""


def test_the_leaf_writer_refuses_a_subclass_of_str():
    """The writer's inner guard, tested apart from the outer one.

    Field checks already stop a str reaching here, so a test going through a
    finding would exercise only the outer guard and report on both. A guard and
    the test hunting what it catches must not share a code path -- this
    repository has made that mistake before, and a surviving mutant on this
    line is how it turned up again.
    """
    with pytest.raises(obs.Unserialisable) as exc:
        obs._leaf(Sneaky("div"))

    assert exc.value.code is obs.SerialiseError.UNKNOWN_TYPE


def test_the_leaf_writer_passes_a_real_bool_and_a_real_token():
    assert obs._leaf(True) is True
    assert obs._leaf(obs.Tag.DIV) == "div"


# --- an ancestor chain is a path, not a bag ---------------------------------------------


def step(depth, tag=obs.Tag.LI, role=obs.Role.LISTITEM):
    return obs.AncestorFinding(tag, role, depth)


def test_a_real_contiguous_chain_is_accepted():
    observed(line=line(ancestors=(step(obs.Depth.D1), step(obs.Depth.D2), step(obs.Depth.D3))))


@pytest.mark.parametrize(
    ("label", "chain"),
    [
        ("a redacted tag", (step(obs.Depth.D1, tag=obs.Sentinel.REDACTED),)),
        ("a redacted role", (step(obs.Depth.D1, role=obs.Sentinel.REDACTED),)),
        ("both redacted", (obs.AncestorFinding(obs.Sentinel.REDACTED, obs.Sentinel.REDACTED, obs.Depth.D1),)),
        ("duplicates", (step(obs.Depth.D1), step(obs.Depth.D1))),
        ("reversed", (step(obs.Depth.D2), step(obs.Depth.D1))),
        ("a skipped step", (step(obs.Depth.D1), step(obs.Depth.D3))),
        ("starting at the line itself", (step(obs.Depth.D0), step(obs.Depth.D1))),
        ("only D0", (step(obs.Depth.D0),)),
        ("starting part way up", (step(obs.Depth.D2),)),
        ("an over-budget step", (step(obs.Depth.D1), step(obs.Depth.OVER_BUDGET))),
        ("four identical steps", tuple(step(obs.Depth.D2) for _ in range(4))),
    ],
)
def test_an_unordered_bag_is_not_an_ancestor_chain(label, chain):
    """An observation made of sentinels is a record of not having seen anything."""
    with pytest.raises(obs.Unserialisable) as exc:
        observed(line=line(ancestors=chain))

    assert exc.value.code is obs.SerialiseError.BROKEN_CHAIN, label


def test_the_chain_stops_at_four_because_the_ladder_does():
    """D0 is the line element, reported separately, not a step in its own ancestry."""
    assert obs.MAX_ANCESTORS == 4

    with pytest.raises(obs.Unserialisable) as exc:
        line(ancestors=tuple(step(obs.Depth.D1) for _ in range(5)))

    assert exc.value.code is obs.SerialiseError.TOO_MANY_ANCESTORS


# --- being in the vocabulary is not being the thing ---------------------------------------


@pytest.mark.parametrize(
    "role",
    [obs.Role.LINK, obs.Role.IMG, obs.Role.HEADING, obs.Role.LIST, obs.Role.NONE, obs.Role.TEXTBOX],
)
def test_only_a_button_can_be_the_quantity_control(role):
    """The name is probed on a button, so a button is what may be reported.

    A link captioned "Decrease quantity" is not a quantity control. Membership
    of a vocabulary is not the same as being the thing the vocabulary
    describes, and this is the gap that matters most, because the next step
    after identifying a control is pressing one.
    """
    with pytest.raises(obs.Unserialisable) as exc:
        observed(control=control(role=role))

    assert exc.value.code is obs.SerialiseError.INCOHERENT_OBSERVATION


def test_a_disabled_control_is_still_an_identified_control():
    """Deliberate, not left over: observed means identified, not actionable.

    A decrease button is legitimately disabled at a quantity of one, and that
    is a correct identification. Whether it can be used is a question for the
    run that wants to use it, asked at that moment.
    """
    result = observed(control=control(disabled=True))

    assert json.loads(obs.render(result))["control"]["disabled"] is True


# --- the factory's own arguments ------------------------------------------------------------


@pytest.mark.parametrize("budget", [True, False, 0, -1, "20", None, 2.0, Hostile("20")])
def test_a_bad_budget_fails_with_a_fixed_code_not_whatever_python_raises(budget):
    """The budget is internal, which is exactly the argument for not checking it.

    A layer claiming closed failure behaviour does not get to hold one
    uncontrolled exception, and a comparison against a string is one.
    """
    with pytest.raises(obs.Unserialisable) as exc:
        obs.Cardinality.of(1, budget)

    assert exc.value.code is obs.SerialiseError.BAD_BUDGET


def test_a_good_budget_still_works():
    assert obs.Cardinality.of(1, 20) is obs.Cardinality.ONE
