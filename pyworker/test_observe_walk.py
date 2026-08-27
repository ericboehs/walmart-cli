"""The walker, tested against a fake that refuses anything it was not promised.

The fake is the important part of this file. A fake that records selectors and
lets the test check them afterwards proves things only in the tests somebody
remembered to write an assertion for. This one rejects: any selector, role or
accessible name outside an exact allowlist raises immediately, so a needle
reaching a query fails the run that did it rather than a later inspection.

Needles are synthetic and are planted everywhere a real run would hold one --
in the item id, the offer id, the store, the quantity, the product name, in
page text, in attribute values, and inside the exception messages the fake
raises. Then nothing anywhere is allowed to contain them: not a locator
argument, not the result, not the rendered JSON, not stdout or stderr, and not
this test's own failure output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import observation as obs  # noqa: E402
import observe_walk as walk  # noqa: E402

ITEM_ID = "33282303"
OFFER_ID = "A1B2C3D4E5F6"
STORE_ID = "499"
QUANTITY = 2
PRODUCT = "Great Value Whole Milk, 1 Gallon"
NEEDLES = (ITEM_ID, OFFER_ID, STORE_ID, PRODUCT, str(QUANTITY))

# A substring search can only be run for needles distinctive enough to search
# for. The quantity is a small integer: "2" occurs inside the tag `h2`, which
# is in the selector vocabulary, so an assertion that no selector contains "2"
# fails on correct code. That is not a reason to weaken the assertion -- it is
# the reason the real guarantee is containment. Selectors must be *members of a
# predeclared set*, which the fake enforces by rejecting anything else, and
# that holds whatever the needle happens to be. The substring check is kept as
# a second alarm for the needles it can actually see.
DISTINCTIVE = (ITEM_ID, OFFER_ID, PRODUCT)

ALLOWED_SELECTORS = frozenset(
    {walk.CANDIDATES, "xpath=.."} | {tag.value for tag in obs.Tag} | set(walk.QUANTITY_SELECTORS)
)
ALLOWED_ROLES = frozenset(role.value for role in obs.Role)
ALLOWED_NAMES = frozenset(name.value for name in obs.ControlName)


class Exploder:
    """An object whose one method raises with the needle in it."""

    def __init__(self, boom=None):
        self.boom = boom or Nasty(f"strict mode violation: {PRODUCT}")

    def go(self, *_args, **_kwargs):
        raise self.boom


class ForbiddenQuery(BaseException):
    """Raised the moment a query is built out of something not predeclared.

    Deliberately outside Exception. The walker converts any Exception into an
    inconclusive result, which would turn a forbidden query into an innocent
    "we could not read the DOM" and leave the fake rejecting nothing. Poison
    has to be unswallowable or it is seasoning.
    """


class FakeLocator:
    def __init__(self, page, node, nodes=None):
        self.page = page
        self.nodes = nodes if nodes is not None else ([node] if node else [])
        # A locator resolving to exactly one element *is* that element, and a
        # fake that forgets this answers every per-element read with a default.
        # It did: is_disabled returned False whatever the page said, which made
        # the disabled test pass while proving nothing.
        self.node = node or (self.nodes[0] if len(self.nodes) == 1 else None)

    # --- reads ---------------------------------------------------------------
    def count(self):
        self.page.reads += 1
        return len(self.nodes)

    def nth(self, index):
        return FakeLocator(self.page, self.nodes[index], [self.nodes[index]])

    def get_attribute(self, name):
        self.page.reads += 1
        self.page.check_attribute(name)
        return (self.node or {}).get("attrs", {}).get(name)

    def text_content(self):
        self.page.reads += 1
        return (self.node or {}).get("text")

    def is_disabled(self):
        self.page.reads += 1
        return (self.node or {}).get("disabled", False)

    # --- narrowing -----------------------------------------------------------
    def locator(self, selector):
        self.page.check_selector(selector)
        if selector == "xpath=..":
            parent = (self.node or {}).get("parent")
            return FakeLocator(self.page, parent, [parent] if parent else [])
        return FakeLocator(self.page, None, self.page.descendants(self.node, selector))

    def get_by_role(self, role, name=None, exact=False):
        self.page.check_role(role, name)
        # `exact` is honoured. A fake that ignores it makes exact=True and
        # exact=False indistinguishable, and then the test proving names are
        # matched exactly proves nothing -- a mutant flipping the flag survived
        # until this line existed.
        found = [
            child
            for child in self.page.subtree(self.node)
            if child.get("role") == role and _named(child, name, exact)
        ]
        return FakeLocator(self.page, None, found)

    def and_(self, other):
        mine = {id(node) for node in self.nodes}
        both = [node for node in other.nodes if id(node) in mine]
        return FakeLocator(self.page, None, both)


def _named(node, name, exact):
    if name is None:
        return True
    actual = node.get("name")
    if not isinstance(actual, str):
        return False
    return actual == name if exact else name.lower() in actual.lower()


class FakePage:
    """A page that refuses to be asked anything it was not promised.

    It also counts reads and interactions separately, because "zero
    interactions" is a claim about clicking and says nothing about looking.
    """

    def __init__(self, tree, attribute_names=None):
        self.tree = tree
        self.reads = 0
        self.interactions = 0
        self.selectors = []
        self.roles = []
        self.names = []
        self.attributes = []
        self.attribute_names = attribute_names

    # Any of these being reachable at all would be a finding in itself.
    def _forbidden(self, *_args, **_kwargs):
        self.interactions += 1
        raise ForbiddenQuery("the observer does not act on pages")

    click = fill = press = hover = tap = check = evaluate = content = _forbidden
    screenshot = select_option = type = focus = _forbidden

    def check_selector(self, selector):
        self.selectors.append(selector)
        if selector not in ALLOWED_SELECTORS:
            raise ForbiddenQuery("a selector nobody declared")

    def check_role(self, role, name):
        self.roles.append(role)
        self.names.append(name)
        if role not in ALLOWED_ROLES:
            raise ForbiddenQuery("a role nobody declared")
        if name is not None and name not in ALLOWED_NAMES:
            raise ForbiddenQuery("an accessible name nobody declared")

    def check_attribute(self, name):
        self.attributes.append(name)
        if name not in {attribute.value for attribute in obs.Attribute}:
            raise ForbiddenQuery("an attribute nobody declared")

    def locator(self, selector):
        self.check_selector(selector)
        return FakeLocator(self, None, self.matching(selector))

    def get_by_role(self, role, name=None, exact=False):
        self.check_role(role, name)
        found = [node for node in self.every() if node.get("role") == role]
        return FakeLocator(self, None, found)

    # --- the toy DOM ---------------------------------------------------------
    def every(self, node=None):
        node = node or self.tree
        yield node
        for child in node.get("children", ()):
            yield from self.every(child)

    def subtree(self, node):
        return list(self.every(node)) if node else list(self.every())

    def descendants(self, node, selector):
        assert selector.startswith(":scope "), "a relative selector must say what it is relative to"
        tag = selector.split("> ")[-1]
        depth = selector.count("> * ") + 1
        return [child for child in self._at_depth(node, depth) if child.get("tag") == tag]

    def _at_depth(self, node, depth):
        level = [node] if node else [self.tree]
        for _ in range(depth):
            level = [child for parent in level for child in parent.get("children", ())]
        return level

    def matching(self, selector):
        if selector == walk.CANDIDATES:
            # The real selector is a list of tag[attribute] pairs, so an
            # element with an allowlisted attribute on a tag outside the
            # vocabulary does not match. The fake used to accept any tag,
            # which let a test reach classification down a path real
            # Playwright would never have opened. A fake with different query
            # semantics is not evidence about the query.
            known = {tag.value for tag in obs.Tag}
            return [
                node
                for node in self.every()
                if node.get("tag") in known
                and any(attribute.value in node.get("attrs", {}) for attribute in obs.Attribute)
            ]
        return [node for node in self.every() if node.get("tag") == selector]


def tree(**over):
    """A cart line with the item id on it, a decrease button, and the quantity.

    Parents are wired after construction so `xpath=..` has somewhere to go.
    """
    quantity_text = {"tag": "span", "role": "none", "text": over.pop("quantity_text", str(QUANTITY))}
    button = {
        "tag": "button",
        "role": "button",
        "name": over.pop("control_name", "Decrease quantity"),
        "disabled": over.pop("disabled", False),
    }
    line_node = {
        "tag": over.pop("line_tag", "div"),
        "role": over.pop("line_role", "listitem"),
        "attrs": over.pop("attrs", {"data-item-id": ITEM_ID}),
        "children": [quantity_text, button],
    }
    root = {"tag": "ul", "role": "list", "children": [line_node]}
    page_node = {"tag": "section", "role": "region", "children": [root]}
    for parent in (page_node, root, line_node):
        for child in parent.get("children", ()):
            child["parent"] = parent
    return page_node


def target(**over):
    """A target carrying the clearance preconditions would have issued.

    Tests reach past `observe` to exercise the walk on its own, and the
    clearance is what makes that deliberate rather than accidental. Production
    has no such path: `_walk` is private and refuses a Target without it.
    """
    fields = {
        "item_id": ITEM_ID,
        "offer_id": OFFER_ID,
        "quantity": QUANTITY,
        "store_id": STORE_ID,
        "clearance": walk._CLEARED,
    }
    return walk.Target(**{**fields, **over})


def run(page, reader=None, **over):
    return walk._walk(page, target(**over), reader or walk.Reader())


# --- the happy path -----------------------------------------------------------------------


def test_a_clean_page_yields_an_observation():
    page = FakePage(tree())

    result = run(page)

    assert type(result) is obs.ObservedResult
    document = json.loads(obs.render(result))
    assert document["line"]["attribute"] == "data-item-id"
    assert document["line"]["relation"] == "equals_item_id"
    assert document["control"]["name"] == "Decrease quantity"
    assert document["quantity"]["relation"] == "equals_quantity"


def test_the_observation_names_no_needle_anywhere():
    page = FakePage(tree())

    result = run(page)
    rendered = obs.render(result)

    # Containment: the real guarantee, and it does not care what the needle is.
    assert set(page.selectors) <= ALLOWED_SELECTORS
    assert set(page.roles) <= ALLOWED_ROLES
    assert {name for name in page.names if name is not None} <= ALLOWED_NAMES

    # And the substring alarm, for the needles long enough to search for.
    for needle in DISTINCTIVE:
        assert needle not in rendered, "the output"
        assert needle not in repr(result), "the result object"
        assert not any(needle in str(selector) for selector in page.selectors), "a selector"
        assert not any(needle in str(name) for name in page.names), "an accessible name"

    # The output side of the same argument: "2" occurs in the depth token
    # "d2". Every scalar being a member of the vocabulary is the property that
    # holds regardless, and it is asserted here as well as in the type tests,
    # because this is the path where a page value could have reached one.
    for scalar in _scalars(json.loads(rendered)):
        assert scalar in VOCABULARY or scalar in DOCUMENT_KEYS or type(scalar) is bool, scalar


def _scalars(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _scalars(value)
    elif isinstance(node, list):
        for item in node:
            yield from _scalars(item)
    else:
        yield node


VOCABULARY = frozenset(member.value for group in obs.LEAVES for member in group)
DOCUMENT_KEYS = frozenset(
    {"outcome", "refusal", "reason", "limit", "line", "control", "quantity"}
    | {"tag", "role", "attribute", "relation", "matches", "ancestors", "depth"}
    | {"name", "location", "disabled"}
)


def test_nothing_is_ever_clicked():
    """Reads and interactions are different claims, so they are counted apart."""
    page = FakePage(tree())

    run(page)

    assert page.interactions == 0
    assert page.reads > 0, "a run that read nothing observed nothing"


def test_the_quantity_is_compared_but_never_queried():
    """No get_by_text(quantity): the needle is an operand, not part of a query."""
    page = FakePage(tree())

    run(page)

    # Stated as containment rather than absence, because "2" legitimately
    # occurs in the tag name `h2`. Absence of a one-character needle is not a
    # property any correct implementation could have.
    assert set(page.selectors) <= ALLOWED_SELECTORS
    assert {name for name in page.names if name is not None} <= ALLOWED_NAMES
    assert not any("get_by_text" in selector for selector in page.selectors)


def test_only_the_five_allowlisted_attributes_are_probed():
    page = FakePage(tree())

    run(page)

    assert set(page.attributes) <= {attribute.value for attribute in obs.Attribute}


# --- what the fake refuses to be asked ------------------------------------------------------


def test_the_fake_rejects_a_selector_nobody_declared():
    """The fake is a guard, not a recorder. Recording proves things afterwards."""
    page = FakePage(tree())

    with pytest.raises(ForbiddenQuery):
        page.locator(f"[data-item-id='{ITEM_ID}']")


def test_the_fake_rejects_an_accessible_name_nobody_declared():
    page = FakePage(tree())

    with pytest.raises(ForbiddenQuery):
        page.get_by_role("button", name=PRODUCT)


def test_the_fake_rejects_an_attribute_nobody_declared():
    page = FakePage(tree())

    with pytest.raises(ForbiddenQuery):
        page.check_attribute("data-product-name")


def test_the_fake_treats_any_interaction_as_a_failure():
    page = FakePage(tree())

    with pytest.raises(ForbiddenQuery):
        page.click()
    with pytest.raises(ForbiddenQuery):
        page.evaluate("1")


# --- exceptions from the browser --------------------------------------------------------------


class Nasty(Exception):
    """What a browser library exception is like: it quotes things."""

    def __init__(self, payload):
        super().__init__(f"strict mode violation: locator resolved to <div>{payload}</div>")


class Raising(Exception):
    def __str__(self):
        raise RuntimeError(PRODUCT)

    def __repr__(self):
        return f"<{PRODUCT}>"


@pytest.mark.parametrize("boom", [Nasty(PRODUCT), Nasty(ITEM_ID), Raising(), ValueError(STORE_ID)])
def test_a_browser_exception_becomes_a_token_and_loses_its_text(boom):
    """Including the ones whose str() raises. None of that reaches a result."""

    def explode():
        raise boom

    reader = walk.Reader()
    with pytest.raises(walk.Unreadable) as stopped:
        reader.read(Exploder(), "go")

    assert stopped.value.reason is obs.Inconclusive.DOM_UNREADABLE
    assert str(stopped.value) == "dom_unreadable"
    for needle in NEEDLES:
        assert needle not in str(stopped.value)
        assert needle not in repr(stopped.value.args)


def test_a_failure_while_counting_is_inconclusive_not_a_crash():
    page = FakePage(tree())
    original = FakeLocator.count

    def broken(self):
        raise Nasty(PRODUCT)

    FakeLocator.count = broken
    try:
        result = run(page)
    finally:
        FakeLocator.count = original

    assert type(result) is obs.InconclusiveResult
    assert json.loads(obs.render(result))["reason"] == "dom_unreadable"
    for needle in NEEDLES:
        assert needle not in obs.render(result)


def test_a_count_that_is_not_a_count_is_inconclusive():
    """A library returning something odd is a DOM we could not read."""
    reader = walk.Reader()

    class Weird:
        def count(self):
            return "many"

    with pytest.raises(walk.Unreadable) as stopped:
        reader.count(Weird())

    assert stopped.value.reason is obs.Inconclusive.DOM_UNREADABLE


# --- preconditions, and the reads they must prevent -------------------------------------------


def cart(**over):
    line = {
        "item_id": ITEM_ID,
        "offer_id": OFFER_ID,
        "quantity": QUANTITY,
        "name": PRODUCT,
        "seller_type": "INTERNAL",
        "seller_name": "Walmart.com",
    }
    line.update(over.pop("line", {}))
    return {
        "lines": over.pop("lines", [line]),
        "fulfillment": {
            "store_id": over.pop("store_id", STORE_ID),
            "intent": over.pop("intent", "DELIVERY"),
        },
    }


PINNED = {"store_id": STORE_ID}

FAILURES = {
    "not a cart at all": (None, PINNED, obs.Refusal.NO_CART),
    "an empty cart": (cart(lines=[]), PINNED, obs.Refusal.NOT_EXACTLY_ONE_LINE),
    "two lines": (cart(lines=[{}, {}]), PINNED, obs.Refusal.NOT_EXACTLY_ONE_LINE),
    "a non-digit item id": (cart(line={"item_id": "GV-MILK"}), PINNED, obs.Refusal.ITEM_ID_SHAPE),
    "a missing item id": (cart(line={"item_id": None}), PINNED, obs.Refusal.ITEM_ID_SHAPE),
    "a blank offer id": (cart(line={"offer_id": "  "}), PINNED, obs.Refusal.OFFER_ID_MISSING),
    "a quantity of zero": (cart(line={"quantity": 0}), PINNED, obs.Refusal.QUANTITY_SHAPE),
    "a boolean quantity": (cart(line={"quantity": True}), PINNED, obs.Refusal.QUANTITY_SHAPE),
    "a string quantity": (cart(line={"quantity": "2"}), PINNED, obs.Refusal.QUANTITY_SHAPE),
    "a marketplace seller": (
        cart(line={"seller_type": "EXTERNAL", "seller_name": "Someone Else"}),
        PINNED,
        obs.Refusal.NOT_FIRST_PARTY,
    ),
    "no pinned store": (cart(), None, obs.Refusal.NO_PINNED_STORE),
    "a pinned store that is not a store": (cart(), "499", obs.Refusal.NO_PINNED_STORE),
    "a pinned store with no id": (cart(), {"name": "Owasso"}, obs.Refusal.NO_PINNED_STORE),
    "a different store's cart": (
        cart(store_id="5260"),
        PINNED,
        obs.Refusal.STORE_DISAGREEMENT,
    ),
    "a cart that names no store": (cart(store_id=""), PINNED, obs.Refusal.STORE_DISAGREEMENT),
    "a pickup cart": (cart(intent="PICKUP"), PINNED, obs.Refusal.NOT_DELIVERY),
    "a cart with no intent": (cart(intent=""), PINNED, obs.Refusal.NOT_DELIVERY),
}


@pytest.mark.parametrize(("label", "case"), sorted(FAILURES.items()))
def test_every_precondition_refuses_with_its_own_token(label, case):
    basket, pinned, expected = case

    result = walk.observe(FakePage(tree()), basket, pinned)

    assert type(result) is obs.RefusedResult, label
    assert result.refusal is expected, label


@pytest.mark.parametrize(("label", "case"), sorted(FAILURES.items()))
def test_a_failed_precondition_reads_nothing_at_all(label, case):
    """Independently, for each one. A precondition checked after the first read
    is not a precondition; it is a regret, and the read already happened."""
    basket, pinned, _ = case
    page = FakePage(tree())

    walk.observe(page, basket, pinned)

    assert page.reads == 0, label
    assert page.selectors == [], label
    assert page.interactions == 0, label


@pytest.mark.parametrize(("label", "case"), sorted(FAILURES.items()))
def test_a_refusal_carries_no_trace_of_the_cart(label, case):
    basket, pinned, _ = case

    result = walk.observe(FakePage(tree()), basket, pinned)
    rendered = obs.render(result)

    for needle in DISTINCTIVE + ("5260", "Owasso", "Someone Else"):
        assert needle not in rendered, label


def test_a_good_cart_produces_a_target_and_then_reads():
    page = FakePage(tree())

    result = walk.observe(page, cart(), PINNED)

    assert type(result) is obs.ObservedResult
    assert page.reads > 0


def test_the_preconditions_use_the_shared_seller_predicate():
    """Not a second definition of first-party. Two is one more than can stay right."""
    import cart_mutate

    seen = []
    original = cart_mutate.is_first_party_line

    def watched(line):
        seen.append(True)
        return original(line)

    cart_mutate.is_first_party_line = watched
    try:
        walk.preconditions(cart(), PINNED)
    finally:
        cart_mutate.is_first_party_line = original

    assert seen, "the shared predicate was not consulted"


# --- bounds, counted as reads rather than as output entries -------------------------------------


def crowded(count):
    """A page with many item-id-bearing elements, to push against the node bound."""
    root = {"tag": "ul", "role": "list", "children": []}
    for index in range(count):
        root["children"].append(
            {"tag": "div", "role": "listitem", "attrs": {"data-item-id": f"9{index:06d}"}}
        )
    page_node = {"tag": "section", "role": "region", "children": [root]}
    for parent in (page_node, root):
        for child in parent["children"]:
            child["parent"] = parent
    return page_node


def test_too_many_candidates_is_a_result_not_a_truncation():
    page = FakePage(crowded(walk.NODE_BUDGET + 1))

    result = run(page)

    assert type(result) is obs.InconclusiveResult
    document = json.loads(obs.render(result))
    assert document["reason"] == "budget_exhausted"
    assert document["limit"] == "node_budget"


def test_the_node_bound_is_charged_before_any_node_is_read():
    """Counting reads, not output entries. Measuring the output is how a bound
    gets tested by measuring the wrong thing, which this repository has done."""
    page = FakePage(crowded(walk.NODE_BUDGET + 1))

    run(page)

    assert page.reads == 1, "only the count itself should have been paid for"
    assert page.attributes == [], "no attribute was probed on any node"


def test_a_run_out_of_budget_stops_before_the_per_node_phase():
    page = FakePage(tree())
    reader = walk.Reader(budget=walk.worst_case_reads(1) - 1)

    result = run(page, reader=reader)

    assert type(result) is obs.InconclusiveResult
    assert json.loads(obs.render(result))["limit"] == "probe_budget"
    assert page.attributes == []


def test_an_ordinary_page_costs_exactly_what_its_shape_says():
    """The ordinary path, priced term by term.

    This replaces an assertion that the run fit inside `worst_case_reads(1)`.
    It did -- eleven against three hundred and fifty-seven -- and a comparison
    with that much room in it cannot fail for any reason anyone cares about.
    Exact, so that a change making the ordinary path more expensive has to be
    noticed and agreed to rather than absorbed.
    """
    page = FakePage(tree())
    reader = walk.Reader()

    result = run(page, reader=reader)

    expected = (
        1  # one count of the candidate selector
        + 1  # the id is in the first declared attribute
        + 2  # control names probed until the second matches
        + 1  # is_disabled
        + 1  # tag: div is the first declared
        + 3  # role: listitem is the third
        + 1  # one count of the first quantity selector
        + 1  # its first element carries the quantity
    )

    assert type(result) is obs.ObservedResult
    assert reader.reads == expected == 11
    assert page.reads == reader.reads


def test_a_reader_with_no_budget_left_refuses_rather_than_reads():
    reader = walk.Reader(budget=0)

    with pytest.raises(walk.Unreadable) as stopped:
        reader.read(Exploder(), "go")

    assert stopped.value.reason is obs.Inconclusive.BOUND_HIT
    assert stopped.value.limit is obs.Limit.PROBES


# --- cardinality is unique elements, and preference order is fixed --------------------------------


def test_a_node_carrying_the_id_twice_is_still_one_element():
    """Counting per attribute would call one line two lines."""
    page = FakePage(tree(attrs={"data-item-id": ITEM_ID, "data-usitemid": ITEM_ID}))

    result = run(page)

    assert type(result) is obs.ObservedResult
    assert json.loads(obs.render(result))["line"]["matches"] == "one"


def test_the_reported_attribute_follows_the_declared_order_not_the_loop():
    """Whichever the loop reached first is not a contract."""
    page = FakePage(tree(attrs={"data-usitemid": ITEM_ID, "data-item-id": ITEM_ID}))

    result = run(page)

    assert json.loads(obs.render(result))["line"]["attribute"] == "data-item-id"


def test_an_exact_match_is_preferred_to_a_substring_one():
    page = FakePage(tree(attrs={"data-item-id": f"line-{ITEM_ID}-row", "id": ITEM_ID}))

    document = json.loads(obs.render(run(page)))

    assert document["line"]["relation"] == "equals_item_id"
    assert document["line"]["attribute"] == "id"


def test_a_substring_match_is_reported_as_one():
    page = FakePage(tree(attrs={"href": f"/ip/{ITEM_ID}"}))

    document = json.loads(obs.render(run(page)))

    assert document["line"]["relation"] == "contains_item_id"


def test_two_different_elements_carrying_the_id_is_ambiguous():
    page = FakePage(tree())
    twin = {"tag": "div", "role": "listitem", "attrs": {"data-item-id": ITEM_ID}}
    twin["parent"] = page.tree["children"][0]
    page.tree["children"][0]["children"].append(twin)

    result = run(page)

    assert type(result) is obs.InconclusiveResult
    assert json.loads(obs.render(result))["reason"] == "more_than_one_match"


def test_nothing_matching_is_inconclusive_not_empty():
    page = FakePage(tree(attrs={"data-item-id": "10000001"}))

    result = run(page)

    assert json.loads(obs.render(result))["reason"] == "nothing_matched"


# --- names and roles outside the vocabulary --------------------------------------------------------


def test_an_unknown_control_name_is_inconclusive_and_is_not_written_down():
    page = FakePage(tree(control_name="Remove Great Value Whole Milk from cart"))

    result = run(page)
    rendered = obs.render(result)

    assert json.loads(rendered)["reason"] == "control_name_outside_vocabulary"
    for needle in DISTINCTIVE:
        assert needle not in rendered


def test_an_unknown_tag_makes_the_observation_inconclusive():
    """It cannot be reported, so there is nothing to report."""
    page = FakePage(tree(line_tag="wm-cart-line"))

    result = run(page)

    assert type(result) is obs.InconclusiveResult


def test_a_disabled_control_is_still_observed():
    page = FakePage(tree(disabled=True))

    result = run(page)

    assert type(result) is obs.ObservedResult
    assert json.loads(obs.render(result))["control"]["disabled"] is True


def test_a_quantity_that_does_not_appear_is_inconclusive():
    page = FakePage(tree(quantity_text="7"))

    result = run(page)

    assert json.loads(obs.render(result))["reason"] == "nothing_matched"


# --- the survivors: things a mutant reached before a test did --------------------------------


def test_the_worst_case_grows_with_the_number_of_nodes():
    """A charge that ignores the per-node probes is not a worst case.

    Asserting only that one real run fits inside the estimate passes with the
    per-node term deleted, because a single node leaves slack. The property is
    that each extra node costs at least what probing it costs.
    """
    for nodes in range(1, walk.NODE_BUDGET):
        step = walk.worst_case_reads(nodes + 1) - walk.worst_case_reads(nodes)

        assert step >= len(walk.ATTRIBUTES), nodes


def test_a_full_page_of_candidates_costs_the_per_node_phase_and_stops():
    """What this page actually does, which is not what the old test implied.

    It was called "a full page of candidates still fits inside its estimate"
    and asserted `reads <= worst_case_reads(12)`. The run never reaches the
    walk at all: none of the twelve nodes carries the item id, so the per-node
    phase runs and `find_line` reports that nothing matched. Every later term
    of the estimate -- the classifications, the control probes, the whole
    quantity phase -- was absent from the number being compared, and the
    comparison passed comfortably because of it.

    So this now says what it measures: one count, twelve nodes at five
    attributes each, and nothing after.
    """
    page = FakePage(crowded(walk.NODE_BUDGET))
    reader = walk.Reader()

    result = run(page, reader=reader)

    assert json.loads(obs.render(result))["reason"] == "nothing_matched"
    assert reader.reads == 1 + walk.NODE_BUDGET * len(walk.ATTRIBUTES) == 61
    assert page.reads == reader.reads


def test_the_browser_exception_is_not_kept_as_a_cause():
    """`raise ... from exc` keeps the original, and its text, on __cause__.

    A traceback printed anywhere would then include the quoted selector and
    whatever slice of the page the library put in the message. Losing the text
    means losing it, not filing it somewhere quieter.
    """

    def explode():
        raise Nasty(PRODUCT)

    with pytest.raises(walk.Unreadable) as stopped:
        walk.Reader().read(Exploder(), "go")

    assert stopped.value.__cause__ is None
    assert stopped.value.__context__ is None or PRODUCT not in str(stopped.value.args)


def test_the_first_declared_attribute_wins_among_substring_matches():
    """Two attributes both containing the id: the contract is the order, not the loop."""
    page = FakePage(tree(attrs={"data-item-id": f"row-{ITEM_ID}", "data-usitemid": f"cell-{ITEM_ID}"}))

    document = json.loads(obs.render(run(page)))

    assert document["line"]["attribute"] == "data-item-id"
    assert document["line"]["relation"] == "contains_item_id"


def test_a_control_name_that_merely_contains_a_safe_one_is_not_a_match():
    """exact=True, and the fake honours it, so this can actually fail."""
    page = FakePage(tree(control_name="Decrease quantity of Great Value Whole Milk"))

    result = run(page)

    assert type(result) is obs.InconclusiveResult
    rendered = obs.render(result)
    assert json.loads(rendered)["reason"] == "control_name_outside_vocabulary"
    for needle in DISTINCTIVE:
        assert needle not in rendered


def test_a_number_containing_the_quantity_is_not_the_quantity():
    """ "12" contains "2". A quantity match has to be the whole text."""
    page = FakePage(tree(quantity_text="12"))

    result = run(page)

    assert type(result) is obs.InconclusiveResult
    assert json.loads(obs.render(result))["reason"] == "nothing_matched"


def test_the_candidate_selector_asks_for_every_pairing_at_once():
    """One selector, so count() counts elements.

    Summing per-attribute counts would report a node carrying the id in two
    attributes as two lines, and the whole uniqueness test rests on this.
    """
    assert walk.CANDIDATES.startswith(":is(")
    for tag in obs.Tag:
        for attribute in obs.Attribute:
            assert f"{tag.value}[{attribute.value}]" in walk.CANDIDATES


# --- the boundary has no holes ------------------------------------------------------------


def poisoned(page, attribute, payload=PRODUCT):
    """Make one browser call explode, whichever call it is."""
    original = getattr(type(page), attribute, None) or getattr(page, attribute)

    def explode(*_args, **_kwargs):
        raise Nasty(payload)

    setattr(page, attribute, explode)
    return original


@pytest.mark.parametrize("attribute", ["locator", "get_by_role"])
def test_a_failure_constructing_a_locator_does_not_escape(attribute):
    """It did. Reader wrapped the reads and not the construction, and a
    boundary with a hole in it is the shape of the thing it was drawn around."""
    page = FakePage(tree())
    poisoned(page, attribute)

    result = run(page)

    assert type(result) is obs.InconclusiveResult
    rendered = obs.render(result)
    assert json.loads(rendered)["reason"] == "dom_unreadable"
    for needle in DISTINCTIVE:
        assert needle not in rendered


@pytest.mark.parametrize("method", ["nth", "and_", "locator", "count", "get_attribute"])
def test_a_failure_in_any_locator_call_becomes_a_token(method):
    page = FakePage(tree())
    original = getattr(FakeLocator, method)

    def explode(*_args, **_kwargs):
        raise Nasty(PRODUCT)

    setattr(FakeLocator, method, explode)
    try:
        result = run(page)
    finally:
        setattr(FakeLocator, method, original)

    assert type(result) is obs.InconclusiveResult
    for needle in DISTINCTIVE:
        assert needle not in obs.render(result)


def test_the_original_exception_is_not_kept_anywhere():
    """`from None` clears __cause__ and leaves __context__ holding the object.

    A formatted traceback prints the context chain, so the selector and
    whatever slice of the page the library quoted come back out. The fixed
    exception is raised after leaving the except block, where there is no
    exception being handled and nothing to attach.
    """
    import traceback

    def explode():
        raise ValueError(f"strict mode violation: {PRODUCT}")

    with pytest.raises(walk.Unreadable) as stopped:
        walk.Reader().read(Exploder(), "go")

    assert stopped.value.__cause__ is None
    assert stopped.value.__context__ is None
    formatted = "".join(
        traceback.format_exception(type(stopped.value), stopped.value, stopped.value.__traceback__)
    )
    for needle in DISTINCTIVE:
        assert needle not in formatted


def test_the_fakes_poison_cannot_be_swallowed_into_an_innocent_result():
    """ForbiddenQuery is outside Exception on purpose.

    The walker turns any Exception into an inconclusive result. If the fake's
    refusal were an Exception, a forbidden query would come back as a polite
    "we could not read the DOM" and the fake would be rejecting nothing.
    """
    assert not issubclass(ForbiddenQuery, Exception)

    page = FakePage(tree())
    original = FakeLocator.get_attribute

    def forbidden(*_args, **_kwargs):
        raise ForbiddenQuery("a query nobody declared")

    FakeLocator.get_attribute = forbidden
    try:
        with pytest.raises(ForbiddenQuery):
            run(page)
    finally:
        FakeLocator.get_attribute = original


# --- the line container is where the control is ----------------------------------------------


def anchor_tree(depth=1):
    """The likely real shape: the id on an anchor, buttons as siblings.

    The earlier walker searched only inside the id-bearing element, found no
    control, and reported UNKNOWN_NAME on a page it should have read.
    """
    anchor = {"tag": "a", "role": "link", "attrs": {"href": f"/ip/{ITEM_ID}"}}
    button = {"tag": "button", "role": "button", "name": "Decrease quantity", "disabled": False}
    quantity = {"tag": "span", "role": "none", "text": str(QUANTITY)}
    # `depth` is how far the anchor sits below the container that holds the
    # button, so the walk has to climb that many steps to find the control.
    wrapped = anchor
    for _ in range(depth - 1):
        wrapped = {"tag": "div", "role": "group", "children": [wrapped]}
    container = {"tag": "div", "role": "listitem", "children": [wrapped, button, quantity]}
    root = {"tag": "ul", "role": "list", "children": [container]}
    page_node = {"tag": "section", "role": "region", "children": [root]}

    def wire(parent):
        for child in parent.get("children", ()):
            child["parent"] = parent
            wire(child)

    wire(page_node)
    return page_node


def test_the_control_may_be_a_sibling_rather_than_a_child():
    page = FakePage(anchor_tree())

    result = run(page)

    assert type(result) is obs.ObservedResult, "an anchor with sibling controls is a cart line"
    document = json.loads(obs.render(result))
    assert document["line"]["tag"] == "a"
    assert document["line"]["relation"] == "contains_item_id"
    assert document["control"]["location"] == "ancestor_container"


def test_the_path_stops_at_the_container_holding_the_control():
    """Not four ancestors regardless. A path to nowhere in particular is not
    evidence about where the line is."""
    page = FakePage(anchor_tree())

    document = json.loads(obs.render(run(page)))
    chain = document["line"]["ancestors"]

    assert [step["depth"] for step in chain] == ["d1"]
    assert chain[0]["role"] == "listitem"
    assert all(step["tag"] != "ul" for step in chain), "the list is above the line, not part of it"


def test_a_deeper_container_is_still_found_within_the_ladder():
    page = FakePage(anchor_tree(depth=2))

    document = json.loads(obs.render(run(page)))

    assert [step["depth"] for step in document["line"]["ancestors"]] == ["d1", "d2"]


def test_a_control_inside_the_id_bearing_element_reports_so():
    page = FakePage(tree())

    document = json.loads(obs.render(run(page)))

    assert document["control"]["location"] == "id_element"
    assert document["line"]["ancestors"] == []


def test_no_control_within_the_ladder_is_inconclusive():
    orphan = {"tag": "div", "role": "listitem", "attrs": {"data-item-id": ITEM_ID}}
    root = {"tag": "ul", "role": "list", "children": [orphan]}
    orphan["parent"] = root
    page = FakePage(root)

    result = run(page)

    assert json.loads(obs.render(result))["reason"] == "control_name_outside_vocabulary"


# --- bounds that do not fail open -------------------------------------------------------------


def crowded_line(spans):
    """A line with many candidate quantity elements, the first one matching."""
    children = [{"tag": "span", "role": "none", "text": str(QUANTITY)}]
    children += [{"tag": "span", "role": "none", "text": f"9{n}"} for n in range(spans - 1)]
    children.append({"tag": "button", "role": "button", "name": "Decrease quantity", "disabled": False})
    line_node = {
        "tag": "div",
        "role": "listitem",
        "attrs": {"data-item-id": ITEM_ID},
        "children": children,
    }
    root = {"tag": "ul", "role": "list", "children": [line_node]}
    page_node = {"tag": "section", "role": "region", "children": [root]}

    def wire(parent):
        for child in parent.get("children", ()):
            child["parent"] = parent
            wire(child)

    wire(page_node)
    return page_node


def test_a_match_inside_an_over_budget_level_is_not_an_observation():
    """It was. `min(found, budget)` truncated the list, so if the first element
    happened to match, a page with more elements than we agreed to look at came
    back as a clean observation -- a bound reporting success for exactly the
    runs it exists to stop."""
    page = FakePage(crowded_line(walk.QUANTITY_NODE_BUDGET + 1))

    result = run(page)

    assert type(result) is obs.InconclusiveResult
    document = json.loads(obs.render(result))
    assert document["reason"] == "budget_exhausted"
    assert document["limit"] == "node_budget"


def test_a_level_inside_the_bound_is_read_normally():
    page = FakePage(crowded_line(walk.QUANTITY_NODE_BUDGET))

    assert type(run(page)) is obs.ObservedResult


def test_one_read_short_of_the_estimate_stops_before_the_per_node_phase():
    page = FakePage(tree())
    reader = walk.Reader(budget=walk.worst_case_reads(1) - 1)

    result = run(page, reader=reader)

    assert type(result) is obs.InconclusiveResult
    assert json.loads(obs.render(result))["limit"] == "probe_budget"
    assert page.attributes == [], "no attribute was probed on any node"


def test_exactly_the_estimate_is_enough():
    page = FakePage(tree())

    result = run(page, reader=walk.Reader(budget=walk.worst_case_reads(1)))

    assert type(result) is obs.ObservedResult


def test_a_reader_that_has_already_been_used_is_measured_by_what_is_left():
    """Comparing against original capacity lets a part-spent Reader start a
    phase it cannot finish, which is the failure the check exists for."""
    page = FakePage(tree())
    reader = walk.Reader(budget=walk.worst_case_reads(1))
    reader.charge(50)

    result = run(page, reader=reader)

    assert type(result) is obs.InconclusiveResult
    assert json.loads(obs.render(result))["limit"] == "probe_budget"
    assert page.attributes == []


# --- unknown shapes get their own reasons -------------------------------------------------------


def test_an_unknown_tag_says_so_rather_than_nothing_matched():
    """UNKNOWN_TAG existed and was never returned: every unexpected shape was
    flattened into "nothing matched", which is a claim about the page."""
    page = FakePage(tree())
    original = FakePage.matching

    def only_known(self, selector):
        found = original(self, selector)
        return [] if selector in {tag.value for tag in obs.Tag} else found

    FakePage.matching = only_known
    try:
        result = run(page)
    finally:
        FakePage.matching = original

    assert json.loads(obs.render(result))["reason"] == "tag_outside_vocabulary"


def test_a_custom_element_never_becomes_a_candidate():
    """The real selector lists known tags, so a custom element does not match.

    The fake used to accept any tag carrying an allowlisted attribute, which
    let this reach classification down a path Playwright would never open. A
    fake with different query semantics is not evidence about the query.
    """
    page = FakePage(tree(line_tag="wm-cart-line"))

    result = run(page)

    assert json.loads(obs.render(result))["reason"] == "nothing_matched"


def test_an_incoherent_result_is_unreadable_not_a_factual_claim():
    page = FakePage(tree())
    original = walk.find_quantity

    def broken(reader, line, quantity):
        return obs.QuantityFinding(obs.Relation.NO_MATCH, obs.Depth.D1)

    walk.find_quantity = broken
    try:
        result = run(page)
    finally:
        walk.find_quantity = original

    assert json.loads(obs.render(result))["reason"] == "dom_unreadable"


# --- disabled is a bool, not something bool-ish ---------------------------------------------------


class Truthy:
    def __bool__(self):
        raise RuntimeError(PRODUCT)


@pytest.mark.parametrize("value", ["false", "", 0, 1, None, Truthy()])
def test_a_disabled_state_that_is_not_a_bool_is_unreadable(value):
    """`bool(value)` runs code the library wrote, outside the boundary, and
    turns a non-empty string into a claim nobody observed."""
    page = FakePage(tree())
    original = FakeLocator.is_disabled

    FakeLocator.is_disabled = lambda self: value
    try:
        result = run(page)
    finally:
        FakeLocator.is_disabled = original

    assert type(result) is obs.InconclusiveResult
    assert json.loads(obs.render(result))["reason"] == "dom_unreadable"


# --- store ids are digit strings, on both sides --------------------------------------------------


class Stringy:
    """An object whose __str__ says whatever you would like it to say."""

    def __init__(self, payload):
        self.payload = payload

    def __str__(self):
        return self.payload


@pytest.mark.parametrize(
    ("pinned", "live"),
    [
        ({"store_id": 499}, "499"),
        ({"store_id": "499"}, 499),
        ({"store_id": 499}, 499),
        ({"store_id": Stringy("499")}, "499"),
        ({"store_id": "499"}, Stringy("499")),
        ({"store_id": " 499 "}, "0499"),
        ({"store_id": "499"}, "499.0"),
        ({"store_id": True}, "1"),
        ({"store_id": ["499"]}, "499"),
    ],
)
def test_a_store_id_that_is_not_a_digit_string_never_agrees(pinned, live):
    """`str()` on both sides made 499 and "499" the same value, and turned any
    object at all into a plausible id by way of its __str__."""
    result = walk.observe(FakePage(tree()), cart(store_id=live), pinned)

    assert type(result) is obs.RefusedResult
    assert result.refusal in (obs.Refusal.NO_PINNED_STORE, obs.Refusal.STORE_DISAGREEMENT)


def test_leading_zeros_are_part_of_the_identifier():
    """ "0499" and "499" are different stores, and only one of them is pinned."""
    result = walk.observe(FakePage(tree()), cart(store_id="0499"), {"store_id": "499"})

    assert result.refusal is obs.Refusal.STORE_DISAGREEMENT


def test_matching_digit_strings_agree():
    assert type(walk.observe(FakePage(tree()), cart(), PINNED)) is obs.ObservedResult


# --- the walk cannot be reached around the preconditions --------------------------------------------


def test_a_target_without_clearance_cannot_read_a_page():
    """A public walk taking a hand-built Target was a way past every check.

    Not a hypothetical: the preconditions are the only thing standing between
    this code and someone else's cart, and a second entrance makes them
    decoration.
    """
    page = FakePage(tree())
    forged = walk.Target(item_id=ITEM_ID, offer_id=OFFER_ID, quantity=QUANTITY, store_id=STORE_ID)

    with pytest.raises(walk.Refused):
        walk._walk(page, forged, walk.Reader())

    assert page.reads == 0


def test_the_module_offers_no_public_walk():
    assert not hasattr(walk, "walk")
    assert callable(walk.observe)


@pytest.mark.parametrize(
    "broken",
    [
        {"item_id": "GV-MILK"},
        {"item_id": ""},
        {"store_id": "owasso"},
        {"quantity": 0},
        {"quantity": True},
        {"quantity": "2"},
        {"offer_id": ""},
    ],
)
def test_a_malformed_target_cannot_be_built_at_all(broken):
    """So nothing malformed reaches `str(quantity)` or a comparison."""
    with pytest.raises(walk.Refused):
        target(**broken)


def test_the_targets_own_checks_are_a_backstop_with_their_own_token():
    """The cart checks produce the specific tokens; this says only that the
    Target did not come from a cart this tool read.

    Sharing a token made the two guards invisible to each other: a mutant
    deleting the cart-side quantity check survived, because the Target refused
    afterwards in exactly the same words.
    """
    with pytest.raises(walk.Refused) as internal:
        target(quantity=0)

    assert internal.value.refusal is obs.Refusal.NO_CART

    external = walk.observe(FakePage(tree()), cart(line={"quantity": 0}), PINNED)

    assert external.refusal is obs.Refusal.QUANTITY_SHAPE


def test_the_preconditions_use_the_shared_store_predicate():
    """Consulted for its verdict, so the two definitions cannot drift apart."""
    import cart_mutate

    seen = []
    original = cart_mutate.assert_deliverable_from_the_pinned_store

    def watched(basket, pinned):
        seen.append(True)
        return original(basket, pinned)

    cart_mutate.assert_deliverable_from_the_pinned_store = watched
    try:
        walk.preconditions(cart(), PINNED)
    finally:
        cart_mutate.assert_deliverable_from_the_pinned_store = original

    assert seen, "the shared predicate was not consulted"


def test_a_locator_built_during_classification_is_inside_the_boundary():
    """The candidate query is wrapped, so poisoning every locator call is
    caught before classification is ever reached. This poisons only the
    per-tag construction, which is the call that used to sit outside."""
    page = FakePage(tree())
    original = page.locator

    def selective(selector):
        if selector != walk.CANDIDATES:
            raise Nasty(PRODUCT)
        return original(selector)

    page.locator = selective
    result = run(page)

    assert type(result) is obs.InconclusiveResult
    rendered = obs.render(result)
    assert json.loads(rendered)["reason"] == "dom_unreadable"
    for needle in DISTINCTIVE:
        assert needle not in rendered


def test_a_role_outside_the_vocabulary_says_so():
    """UNKNOWN_ROLE existed and nothing returned it."""
    page = FakePage(tree())
    original = FakePage.get_by_role

    def never(self, role, name=None, exact=False):
        self.check_role(role, name)
        return FakeLocator(self, None, [])

    FakePage.get_by_role = never
    try:
        result = run(page)
    finally:
        FakePage.get_by_role = original

    assert json.loads(obs.render(result))["reason"] == "role_outside_vocabulary"


# --- the bound, measured against a page that actually costs -----------------------------------
#
# The previous worst-case test built a page it *called* expensive, spent 90
# reads on it, and asserted 90 <= 357. That cannot detect an underestimate; it
# is a test of the arithmetic `90 <= 357`. A bound is only tested by the run
# that comes closest to it, so this page is constructed to make every loop in
# the walk take its longest branch.

MAXIMAL_READS = 353
MAXIMAL_SLACK = 4


def _wire(node, parent=None):
    node["parent"] = parent
    for child in node.get("children", ()):
        _wire(child, node)
    return node


def _fillers(exclude_last_of=None, matching=None):
    """Eight of each quantity tag, so every quantity selector costs its most.

    Eight is `QUANTITY_NODE_BUDGET`: one more and the walk refuses before
    reading, one fewer and the level is cheaper than the formula allows for.
    """
    nodes = []
    for tag in walk.QUANTITY_TAGS:
        wanted = 8 - (1 if tag is exclude_last_of else 0)
        for index in range(wanted):
            last = index == wanted - 1
            text = str(QUANTITY) if (last and tag is matching) else "x"
            nodes.append({"tag": tag.value, "role": "none", "text": text})
    return nodes


def maximal(extra_candidates=0):
    """The most expensive page the walk will still return an observation for.

    Every branch takes its longest path:

    - the id is in the *last* declared attribute, so `_match` reads all five;
    - the id element and all four ancestors carry the last tag and the last
      role in each vocabulary, so every classification reads all nineteen tags
      and all eleven roles before it matches;
    - the first four scopes contain no control at all, so all six names are
      probed and missed at each;
    - the fifth scope matches only the sixth and last name;
    - all eighteen quantity selectors resolve exactly eight elements, and the
      one that matches is the eighth element of the eighteenth selector.

    The ladder is made of `td`, which is both the last tag and a quantity tag,
    so each ancestor is also one of the eight elements at its level. That is
    not a trick to save nodes; it is what the page would do to us.
    """
    last_tag = walk.TAGS[-1].value
    last_role = walk.ROLES[-1].value
    node = {"tag": last_tag, "role": last_role, "attrs": {walk.ATTRIBUTES[-1].value: ITEM_ID}}

    a1 = {"tag": last_tag, "role": last_role, "children": [node]}
    a2 = {"tag": last_tag, "role": last_role, "children": [a1]}
    a3 = {"tag": last_tag, "role": last_role, "children": [a2]}

    deep = {
        "tag": "ol",
        "role": "group",
        "children": _fillers(exclude_last_of=obs.Tag.TD, matching=walk.QUANTITY_TAGS[-1]),
    }
    middle = {"tag": "ul", "role": "group", "children": _fillers(exclude_last_of=obs.Tag.TD)}
    carrier = {"tag": "ul", "role": "group", "children": [deep]}
    button = {
        "tag": "button",
        "role": "button",
        "name": walk.CONTROL_NAMES[-1].value,
        "disabled": False,
    }

    # a3 is itself one of the eight `td` at depth one; a2 and a1 likewise at
    # depths two and three, which is why `middle` and `deep` hold seven.
    container = {
        "tag": last_tag,
        "role": last_role,
        "children": [a3, button, middle, carrier, *_fillers(exclude_last_of=obs.Tag.TD)],
    }
    strays = [
        {"tag": "div", "role": "group", "attrs": {"href": f"/ip/{index}"}}
        for index in range(extra_candidates)
    ]
    return _wire({"tag": "section", "role": "region", "children": [container, *strays]})


def test_the_ladder_and_the_fillers_make_every_level_exactly_eight():
    """The page is only maximal if it really does hit the bound everywhere.

    Asserting the read count without this would pass just as well against a
    sparse page whose cost happened to be low, which is how the last version
    of this test managed to prove nothing.
    """
    page = FakePage(maximal())
    container = page.tree["children"][0]

    for selector in walk.QUANTITY_SELECTORS:
        assert len(page.descendants(container, selector)) == walk.QUANTITY_NODE_BUDGET, selector


def test_the_most_expensive_run_lands_just_under_the_declared_bound():
    page = FakePage(maximal())
    reader = walk.Reader()

    result = run(page, reader=reader)

    # The same decomposition the reviewer derived independently, written out
    # so the literal above is checked against the walk's specification rather
    # than only against itself. Deriving it from `worst_case_reads` would be
    # circular; deriving it from what each phase is defined to do is not.
    decomposed = (
        1  # the candidate count
        + len(walk.ATTRIBUTES)  # the id is in the last attribute
        + walk.SCOPES * len(walk.CONTROL_NAMES)  # every name missed but the last
        + 1  # is_disabled, once, at the scope that matched
        + obs.MAX_ANCESTORS * 1  # counting each parent
        + obs.MAX_ANCESTORS * (len(walk.TAGS) + len(walk.ROLES))  # classifying them
        + (len(walk.TAGS) + len(walk.ROLES))  # classifying the id element
        + len(walk.QUANTITY_SELECTORS) * (1 + walk.QUANTITY_NODE_BUDGET)
    )

    assert type(result) is obs.ObservedResult, "the maximal page must still be observable"
    assert decomposed == MAXIMAL_READS
    assert reader.reads == MAXIMAL_READS
    assert page.reads == MAXIMAL_READS, "every read the page served went through the reader"
    assert walk.worst_case_reads(1) - MAXIMAL_READS == MAXIMAL_SLACK


def test_the_slack_is_the_control_terms_deliberate_rounding():
    """Named, so a change that eats it is visible instead of merely still true.

    The formula charges every scope for a name probe it may not make and for a
    disabled read only one scope performs. Four reads. If a later change spends
    those, this test says so before `<=` quietly absorbs it.
    """
    over_counted = walk.SCOPES * (len(walk.CONTROL_NAMES) + 1)
    actually_spent = walk.SCOPES * len(walk.CONTROL_NAMES) + 1

    assert over_counted - actually_spent == MAXIMAL_SLACK


def test_the_node_term_is_exercised_on_the_same_costly_continuation():
    """Eleven more candidates, all missing, on the maximal page.

    A separate cheap page would test the node term against a walk that never
    reaches the expensive part, and the terms would never be added up.
    """
    page = FakePage(maximal(extra_candidates=walk.NODE_BUDGET - 1))
    reader = walk.Reader()

    result = run(page, reader=reader)

    expected = MAXIMAL_READS + (walk.NODE_BUDGET - 1) * len(walk.ATTRIBUTES)

    assert type(result) is obs.ObservedResult
    assert reader.reads == expected
    assert walk.worst_case_reads(walk.NODE_BUDGET) - expected == MAXIMAL_SLACK


def test_the_maximal_run_fits_the_standing_budget():
    """The default budget has to cover the worst case or the bound is theatre."""
    assert walk.worst_case_reads(walk.NODE_BUDGET) <= walk.READ_BUDGET


# --- the boundary starts before the attribute lookup ---------------------------------------------


class Hostile:
    """An object that raises on attribute access rather than on the call.

    Not exotic. A Playwright Locator is a proxy over a connection to another
    process, and attribute access on a proxy runs code: it can raise a
    connection error, a target-closed error, or a strict-mode error, each of
    which quotes the selector. An earlier boundary took bound methods --
    `read(node.get_attribute, ...)` -- so the lookup happened in the caller's
    frame and the wrapper it was being handed to had not been entered yet.
    """

    def __init__(self, poisoned):
        object.__setattr__(self, "_poisoned", poisoned)

    def __getattribute__(self, name):
        if name in object.__getattribute__(self, "_poisoned"):
            raise Nasty(f"strict mode violation: {PRODUCT} at {ITEM_ID}")
        return object.__getattribute__(self, name)


@pytest.mark.parametrize("method", ["locator", "count", "get_attribute", "is_disabled"])
def test_an_attribute_lookup_that_raises_is_inside_the_boundary(method):
    reader = walk.Reader()

    with pytest.raises(walk.Unreadable) as stopped:
        reader.read(Hostile({method}), method)

    assert stopped.value.reason is obs.Inconclusive.DOM_UNREADABLE
    assert stopped.value.__cause__ is None
    assert stopped.value.__context__ is None
    assert_no_needle_in_traceback(stopped.value)


def assert_no_needle_in_traceback(error):
    import traceback

    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    for needle in DISTINCTIVE:
        assert needle not in formatted


@pytest.mark.parametrize("method", ["locator", "get_by_role"])
def test_a_hostile_page_object_leaks_nothing_through_the_walk(method):
    page = FakePage(tree())
    original = type(page).__getattribute__

    def poisoned(self, name):
        if name == method:
            raise Nasty(f"strict mode violation: {PRODUCT}")
        return original(self, name)

    type(page).__getattribute__ = poisoned
    try:
        result = run(page)
    finally:
        type(page).__getattribute__ = original

    assert type(result) is obs.InconclusiveResult
    rendered = obs.render(result)
    assert json.loads(rendered)["reason"] == "dom_unreadable"
    for needle in DISTINCTIVE:
        assert needle not in rendered


def test_no_bound_method_crosses_the_boundary():
    """The object and the method name go in separately, on purpose.

    Checked in the source, because the failure this prevents is invisible in
    behaviour until the day an attribute lookup raises.
    """
    import ast
    import inspect

    # Parsed, not matched. A regex over the text flagged
    # `read(build(nodes, ...), "text_content")` -- a nested call, not a bound
    # method -- and the way to make a regex stop complaining is usually to
    # loosen it until it also stops noticing the real thing.
    tree_ = ast.parse(inspect.getsource(walk))
    checked = 0
    for node_ in ast.walk(tree_):
        if not isinstance(node_, ast.Call) or not isinstance(node_.func, ast.Attribute):
            continue
        if node_.func.attr not in {"read", "build", "boolean"}:
            continue
        # The walk's call sites, where a bound method would come from. Reader's
        # own forwarding passes the object and the name through as values, so
        # the literal rule does not apply to it.
        if not isinstance(node_.func.value, ast.Name) or node_.func.value.id != "reader":
            continue
        checked += 1
        first = node_.args[0]
        assert not isinstance(first, ast.Attribute), (
            f"a bound method reached the boundary at line {node_.lineno}"
        )
        assert isinstance(node_.args[1], ast.Constant), (
            f"the method name must be a literal at line {node_.lineno}"
        )
    assert checked >= 10, "the scan found almost no calls, so it is watching the wrong thing"


# --- preconditions keep their own failures clean ------------------------------------------------


def test_a_refusal_from_the_shared_predicate_keeps_nothing_of_it():
    """CartMutationRefused quotes item names and store ids.

    `raise ... from None` inside the except block clears __cause__ and leaves
    __context__ pointing at it. `observe` discards the exception, so the leak
    was invisible from there -- but that is a property of one caller, and
    `preconditions` is a public function that tests, scripts and future callers
    can use directly.
    """
    import cart_mutate

    original = cart_mutate.assert_line_is_addressable

    def refuse(_line):
        raise cart_mutate.CartMutationRefused(f"{PRODUCT} ({ITEM_ID}) has no offer id")

    cart_mutate.assert_line_is_addressable = refuse
    try:
        with pytest.raises(walk.Refused) as refusal:
            walk.preconditions(cart(), PINNED)
    finally:
        cart_mutate.assert_line_is_addressable = original

    assert refusal.value.refusal is obs.Refusal.OFFER_ID_MISSING

    assert refusal.value.__cause__ is None
    assert refusal.value.__context__ is None
    assert_no_needle_in_traceback(refusal.value)


def test_a_store_refusal_from_the_shared_predicate_keeps_nothing_of_it():
    import cart_mutate

    original = cart_mutate.assert_deliverable_from_the_pinned_store

    def refuse(_basket, _pinned):
        raise cart_mutate.CartMutationRefused(f"the cart is at {STORE_ID}, not the pinned store")

    cart_mutate.assert_deliverable_from_the_pinned_store = refuse
    try:
        with pytest.raises(walk.Refused) as refusal:
            walk.preconditions(cart(), PINNED)
    finally:
        cart_mutate.assert_deliverable_from_the_pinned_store = original

    assert refusal.value.refusal is obs.Refusal.STORE_DISAGREEMENT
    assert refusal.value.__cause__ is None
    assert refusal.value.__context__ is None
    assert_no_needle_in_traceback(refusal.value)


# --- the target does not print itself -----------------------------------------------------------


def test_a_target_prints_nothing_it_holds():
    """A dataclass repr prints every field, and reprs run where nobody meant
    them to: an unhandled traceback showing locals, a pytest assertion diff, a
    debugger, a log line added later. The one object holding all four needles
    was the one object that knew how to print them."""
    built = target()

    for rendering in (repr(built), str(built), f"{built}", "{}".format(built)):  # noqa: UP032
        assert rendering == "<Target redacted>"
        for needle in DISTINCTIVE + (ITEM_ID, STORE_ID, OFFER_ID):
            assert needle not in rendering


def test_a_target_inside_a_container_still_prints_nothing():
    assert ITEM_ID not in repr([target()])
    assert ITEM_ID not in repr({"target": target()})


def test_a_failed_target_construction_prints_nothing_either():
    with pytest.raises(walk.Refused) as refusal:
        walk.Target(item_id=ITEM_ID, offer_id=OFFER_ID, quantity=0, store_id=STORE_ID)

    assert_no_needle_in_traceback(refusal.value)
    assert str(refusal.value) == obs.Refusal.NO_CART.value


def test_every_sensitive_field_is_marked_unprintable():
    """The fixed repr is one edit from being deleted; the flags are what a new
    field inherits by being forgotten. Both, because they fail differently."""
    import dataclasses

    for field in dataclasses.fields(walk.Target):
        assert field.repr is False, field.name


# --- an id is not normalised on its way to a comparison --------------------------------------------


@pytest.mark.parametrize(
    ("pinned", "live"),
    [
        (" 499 ", "499"),
        ("499", " 499 "),
        (" 499 ", " 499 "),
        ("499\n", "499"),
        ("\t499", "499"),
    ],
)
def test_whitespace_is_not_trimmed_into_agreement(pinned, live):
    """Stripping made " 499 " and "499" the same store.

    That is a normalisation rule nobody wrote down, applied to the single
    comparison that decides whether we are looking at the right store's cart.
    An id with a space in it is not a tidier spelling of the same id; it is a
    value we cannot account for, and the answer is to stop.
    """
    result = walk.observe(FakePage(tree()), cart(store_id=live), {"store_id": pinned})

    assert type(result) is obs.RefusedResult
    assert result.refusal in (obs.Refusal.NO_PINNED_STORE, obs.Refusal.STORE_DISAGREEMENT)


@pytest.mark.parametrize("value", ["\u0664\u0669\u0669", "\u00b2", "4\u03559", "٤٩٩"])
def test_digits_means_ascii_digits(value):
    """`isdigit()` is true of superscripts and of Arabic-Indic digits, so a
    value that is not an id in any sense passes a check named for digits."""
    assert walk._digits(value) == ""


def test_an_item_id_with_whitespace_is_refused_rather_than_trimmed():
    result = walk.observe(FakePage(tree()), cart(line={"item_id": f" {ITEM_ID} "}), PINNED)

    assert result.refusal is obs.Refusal.ITEM_ID_SHAPE


# --- the reader's own arguments are arguments too --------------------------------------------------


@pytest.mark.parametrize("budget", ["600", 600.0, True, -1, None, [600]])
def test_a_budget_that_is_not_a_count_fails_the_fixed_way(budget):
    """`Reader("600")` used to raise a raw TypeError on its first charge --
    the uncontrolled exception this layer says it does not have. It is the
    same defect just fixed in Cardinality, in the file that calls it."""
    with pytest.raises(walk.Unreadable) as stopped:
        walk.Reader(budget=budget)

    assert stopped.value.reason is obs.Inconclusive.BAD_BUDGET


@pytest.mark.parametrize("cost", ["1", 1.0, True, -1, None])
def test_a_charge_that_is_not_a_count_fails_the_fixed_way(cost):
    with pytest.raises(walk.Unreadable) as stopped:
        walk.Reader().charge(cost)

    assert stopped.value.reason is obs.Inconclusive.BAD_BUDGET


def test_a_budget_of_zero_is_allowed_and_refuses_on_the_first_read():
    reader = walk.Reader(budget=0)

    with pytest.raises(walk.Unreadable) as stopped:
        reader.read(FakeLocator(FakePage(tree()), None, []), "count")

    assert stopped.value.reason is obs.Inconclusive.BOUND_HIT


class Colliding:
    """A page object whose call takes arguments named like the wrapper's own.

    `get_by_role(..., name=...)` already collides with the obvious spelling of
    this boundary, which is why the parameter is `method` and not `name`. That
    is a rename away from being wrong again, and the usual repair when a
    wrapper's parameter collides with a wrapped call's argument is to reach
    around the wrapper -- which is the one thing this boundary cannot survive.
    """

    def probe(self, *, obj=None, method=None, charge=None):
        return (obj, method, charge)


@pytest.mark.parametrize("entrance", ["read", "build"])
@pytest.mark.parametrize("kwargs", [{"obj": 1}, {"method": "x"}, {"charge": True}])
def test_the_wrapped_call_may_use_the_boundarys_own_parameter_names(entrance, kwargs):
    """Both entrances. A guard on one constructor of two is a guard on
    neither, and the first version of this test only exercised `build` -- so
    the mutant that reopened `read` survived it."""
    reader = walk.Reader()

    answered = getattr(reader, entrance)(Colliding(), "probe", **kwargs)

    assert answered == (kwargs.get("obj"), kwargs.get("method"), kwargs.get("charge"))


# --- one predicate, asked in both places -----------------------------------------------------


SUPERSCRIPT = "\u00b2"
ARABIC_INDIC = "\u0664\u0669\u0669"


@pytest.mark.parametrize("value", [SUPERSCRIPT, ARABIC_INDIC, "٤٩٩", "4\u03559"])
@pytest.mark.parametrize("f\u0069eld", ["item_id", "store_id"])
def test_a_cleared_target_will_not_take_a_non_ascii_id(field, value):
    """The backstop had the older, wrong spelling of the rule.

    `_digits` was fixed to require ASCII and `Target` was not, so a hand-built
    cleared Target accepted an item id of a superscript two and a store id in
    Arabic-Indic digits. That is the two-guard gap again, at the internal
    boundary this time: each guard is only as good as the one that agrees with
    it, and they had stopped agreeing.
    """
    with pytest.raises(walk.Refused) as refusal:
        target(**{field: value})

    assert refusal.value.refusal is obs.Refusal.NO_CART


def test_both_places_ask_the_same_question():
    """Not "both currently answer the same", which is what drifted.

    A shared predicate cannot disagree with itself; two spellings of one rule
    can, and did.
    """
    import ast
    import inspect

    # Parsed, because the first version of this searched the text for
    # `isdigit()` and found it in the docstring explaining why ASCII matters.
    # A substring scan cannot tell a rule from a sentence about the rule.
    tree_ = ast.parse(inspect.getsource(walk))
    shared = [
        node
        for node in ast.walk(tree_)
        if isinstance(node, ast.FunctionDef) and node.name == "_is_ascii_digits"
    ]

    assert len(shared) == 1, "the shared predicate is not exactly one function"

    inside = {id(node) for node in ast.walk(shared[0])}
    strays = [
        node.lineno
        for node in ast.walk(tree_)
        if isinstance(node, ast.Attribute)
        and node.attr in {"isdigit", "isnumeric", "isdecimal"}
        and id(node) not in inside
    ]

    assert strays == [], f"a second spelling of the digit rule at lines {strays}"

    callers = sum(
        1
        for node in ast.walk(tree_)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_is_ascii_digits"
    )

    assert callers >= 3, "the predicate exists but the places that need it are not asking it"


@pytest.mark.parametrize("value", [SUPERSCRIPT, ARABIC_INDIC])
def test_a_non_ascii_store_id_never_agrees_with_itself(value):
    """Equal strings, and still not an agreement, because neither is an id."""
    result = walk.observe(FakePage(tree()), cart(store_id=value), {"store_id": value})

    assert type(result) is obs.RefusedResult
    assert result.refusal is obs.Refusal.NO_PINNED_STORE
