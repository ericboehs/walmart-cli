"""Reading the shape of a cart line without reading the cart.

Offline. There is no entrypoint here, nothing constructs a browser, and no
command runs this. It takes an already-open page object and a target already
established in memory, and it returns one of the three result types in
`observation`. See docs/DOM-OBSERVATION.md for why it is shaped this way.

The single rule that drives the design: **a needle is never part of a query.**
Every selector in this module is assembled from tokens declared in
`observation`, so the item id, the quantity, the offer id and the store id
cannot appear in a locator, in a browser exception quoting that locator, in a
traceback, or in anything written down. Values from the page are compared
against needles in local variables and then dropped.

That constraint is what makes tag and role resolution awkward, and the
awkwardness is the point. A Locator has no safe generic "what tag are you"
read: the obvious answers are `evaluate`, page HTML, or asking for the `role`
attribute, and this file may use none of them. So identity is established by
intersection instead -- the candidate is intersected with a locator for each
tag in the fixed vocabulary, and with `get_by_role` for each role, and the one
that matches is the answer. If none matches, the run is inconclusive. Widening
the read to find out is exactly the trade this document exists to refuse.
"""

from __future__ import annotations

from dataclasses import dataclass

import observation as obs

# --- budgets ---------------------------------------------------------------------------

# Candidate elements we will look at at all.
NODE_BUDGET = 12

# Browser reads of every kind: counts, nth traversals, attribute probes, text
# reads, tag and role classification, control probes. Reads are not
# interactions, and the two are counted separately and reported separately --
# "zero interactions" is a claim about clicking, not about looking.
READ_BUDGET = 400

# Descendant elements searched for the quantity text, per level.
QUANTITY_NODE_BUDGET = 8
QUANTITY_DEPTH = 3

TAGS = tuple(obs.Tag)
ROLES = tuple(obs.Role)
ATTRIBUTES = tuple(obs.Attribute)
CONTROL_NAMES = tuple(obs.ControlName)

# Elements a quantity is plausibly written in. Fixed, like everything else.
QUANTITY_TAGS = (obs.Tag.SPAN, obs.Tag.DIV, obs.Tag.P, obs.Tag.LABEL, obs.Tag.TD, obs.Tag.INPUT)

# Every candidate element, as one selector, so that `count()` counts unique DOM
# elements. Counting per attribute would count a node twice for carrying the
# item id in two allowlisted attributes, and a cardinality that is really a
# number of matching attributes cannot answer "is this line unique".
CANDIDATES = ":is({})".format(
    ", ".join(f"{tag.value}[{attribute.value}]" for tag in TAGS for attribute in ATTRIBUTES)
)


def worst_case_reads(nodes: int) -> int:
    """What this run could cost if every probe misses.

    Charged before the per-node phase begins. Discovering exhaustion halfway
    through means the private reads have already happened, and a budget that
    stops you after the fact is an accounting exercise rather than a bound.
    """
    classify = len(TAGS) + len(ROLES)
    per_node = len(ATTRIBUTES)
    ancestors = obs.MAX_ANCESTORS * (classify + 1)
    control = len(CONTROL_NAMES) + 1
    quantity = QUANTITY_DEPTH * len(QUANTITY_TAGS) * (1 + QUANTITY_NODE_BUDGET)
    return 1 + (nodes * per_node) + classify + ancestors + control + quantity


# --- failures ----------------------------------------------------------------------------


class Unreadable(Exception):
    """A browser call failed. Carries no text, by construction.

    Playwright exceptions quote the selector and often a slice of the page.
    Ours are built from vocabulary tokens so a leak through the selector is
    impossible, but `str(exc)` would still be an uncontrolled egress, and the
    library is free to put whatever it likes in there.
    """

    def __init__(self, reason: obs.Inconclusive, limit: obs.Limit = obs.Limit.NONE):
        super().__init__(reason.value)
        self.reason = reason
        self.limit = limit


class Refused(Exception):
    def __init__(self, refusal: obs.Refusal):
        super().__init__(refusal.value)
        self.refusal = refusal


# --- the target ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """What we knew before looking, and what the page will be compared against.

    Held in memory, never written, never interpolated into a query.
    """

    item_id: str
    offer_id: str
    quantity: int
    store_id: str


def preconditions(cart, pinned_store) -> Target:
    """Everything that must be true before a single locator is built.

    Runs to completion first. A precondition checked after the first read is
    not a precondition, it is a regret, and the reads it failed to prevent have
    already happened.

    The authority for seller, store and intent is the predicates cart_mutate
    already uses, called here rather than restated. Two definitions of
    first-party is one more than can stay correct. Their messages quote item
    names and store ids, so they are called for their verdict and never for
    their text: which fixed token comes back is decided by *which call* raised,
    not by reading what it said.

    Eligibility is the strict reading: the cart holds exactly one line. Not
    "one line that qualifies", because choosing among lines is selection, and
    selection is how a target ends up named on a terminal or in a filename.
    """
    import cart_mutate

    if not isinstance(cart, dict):
        raise Refused(obs.Refusal.NO_CART)
    lines = cart.get("lines")
    if not isinstance(lines, list) or len(lines) != 1:
        raise Refused(obs.Refusal.NOT_EXACTLY_ONE_LINE)
    line = lines[0]
    if not isinstance(line, dict):
        raise Refused(obs.Refusal.NO_CART)

    item_id = line.get("item_id")
    if not isinstance(item_id, str) or not item_id.strip().isdigit():
        raise Refused(obs.Refusal.ITEM_ID_SHAPE)

    offer_id = line.get("offer_id")
    if not isinstance(offer_id, str) or not offer_id.strip():
        raise Refused(obs.Refusal.OFFER_ID_MISSING)

    quantity = line.get("quantity")
    if type(quantity) is not int or type(quantity) is bool or quantity < 1:
        raise Refused(obs.Refusal.QUANTITY_SHAPE)

    # The shared predicate, for the same question, so the two cannot drift.
    try:
        cart_mutate.assert_line_is_addressable(line)
    except cart_mutate.CartMutationRefused:
        raise Refused(obs.Refusal.OFFER_ID_MISSING) from None

    if not cart_mutate.is_first_party_line(line):
        raise Refused(obs.Refusal.NOT_FIRST_PARTY)

    store_id = _agreed_store(cart, pinned_store, cart_mutate)

    return Target(
        item_id=item_id.strip(),
        offer_id=offer_id.strip(),
        quantity=quantity,
        store_id=store_id,
    )


def _agreed_store(cart, pinned_store, cart_mutate) -> str:
    """Store and intent, decided by the shared predicate, reported as a token.

    Which token depends on which of the three questions is unanswerable, and
    those are distinguished here by asking them again in memory -- not by
    parsing the shared predicate's sentence, which quotes the store id.
    """
    if not isinstance(pinned_store, dict) or not str(pinned_store.get("store_id") or "").strip():
        raise Refused(obs.Refusal.NO_PINNED_STORE)

    fulfillment = cart.get("fulfillment") or {}
    intent = str(fulfillment.get("intent") or "").strip().lower()

    try:
        cart_mutate.assert_deliverable_from_the_pinned_store(cart, pinned_store)
    except cart_mutate.CartMutationRefused:
        if intent and intent not in cart_mutate.DELIVERY_INTENTS:
            raise Refused(obs.Refusal.NOT_DELIVERY) from None
        if not intent:
            raise Refused(obs.Refusal.NOT_DELIVERY) from None
        raise Refused(obs.Refusal.STORE_DISAGREEMENT) from None

    return str(fulfillment.get("store_id") or "").strip()


# --- the reader ------------------------------------------------------------------------------


class Reader:
    """Every browser read goes through here, and every one is charged.

    It also converts the browser library's exceptions into this module's fixed
    tokens at the boundary, so no caller ever holds one. A library exception
    that reaches a caller is a library exception that reaches a log.
    """

    def __init__(self, budget: int = READ_BUDGET):
        self.remaining = budget
        self.reads = 0
        self.interactions = 0

    def charge(self, cost: int = 1, limit: obs.Limit = obs.Limit.PROBES) -> None:
        if self.remaining < cost:
            raise Unreadable(obs.Inconclusive.BOUND_HIT, limit)
        self.remaining -= cost
        self.reads += cost

    def read(self, call, *args, **kwargs):
        self.charge()
        try:
            return call(*args, **kwargs)
        except Unreadable:
            raise
        except Exception:
            # Deliberately not `except PlaywrightError`: anything at all that
            # goes wrong becomes the same token. Naming the library's classes
            # here would leave every other failure to escape with its text.
            raise Unreadable(obs.Inconclusive.DOM_UNREADABLE) from None

    def count(self, locator) -> int:
        value = self.read(locator.count)
        if type(value) is not int or type(value) is bool or value < 0:
            raise Unreadable(obs.Inconclusive.DOM_UNREADABLE)
        return value


# --- identity by intersection -------------------------------------------------------------


def classify_tag(reader: Reader, page, node) -> obs.Tag | obs.Sentinel:
    """Which tag, asked by intersecting with each tag in the vocabulary.

    Not evaluate, not page HTML, and not reading the role attribute by name.
    Those are the easy answers, all three are banned by the same page of the
    same document, and none of them is spelled out here in a form the source
    alarm would have to make an exception for.
    """
    for tag in TAGS:
        if reader.count(node.and_(page.locator(tag.value))) == 1:
            return tag
    return obs.Sentinel.REDACTED


def classify_role(reader: Reader, page, node) -> obs.Role | obs.Sentinel:
    for role in ROLES:
        if reader.count(node.and_(page.get_by_role(role.value))) == 1:
            return role
    return obs.Sentinel.REDACTED


# --- finding the line ------------------------------------------------------------------------

# Fixed before any live output was seen. A node can carry the item id in more
# than one allowlisted attribute, and without a declared order the reported
# contract would be whichever the loop happened to reach first. An equality is
# always preferred to a substring, and within each the attribute order is the
# order they are declared in.
RELATION_ORDER = (obs.Relation.EQUALS_ITEM_ID, obs.Relation.CONTAINS_ITEM_ID)


def _match(reader: Reader, node, item_id: str):
    """How this node carries the item id, if it does.

    The comparison happens here, against a local variable. The needle is an
    operand; it was never in the query that found this node.
    """
    best = None
    for attribute in ATTRIBUTES:
        value = reader.read(node.get_attribute, attribute.value)
        if type(value) is not str:
            continue
        if value == item_id:
            return (attribute, obs.Relation.EQUALS_ITEM_ID)
        if best is None and item_id in value:
            best = (attribute, obs.Relation.CONTAINS_ITEM_ID)
    return best


def find_line(reader: Reader, page, target: Target):
    """The one element carrying the item id, or a reason there is not one."""
    candidates = page.locator(CANDIDATES)
    found = reader.count(candidates)
    matches = obs.Cardinality.of(found, NODE_BUDGET)
    if matches is obs.Cardinality.OVER_BUDGET:
        raise Unreadable(obs.Inconclusive.BOUND_HIT, obs.Limit.NODES)
    if matches is obs.Cardinality.ZERO:
        raise Unreadable(obs.Inconclusive.NO_MATCH)

    reader.charge(0)
    if worst_case_reads(found) > reader.remaining + reader.reads:
        raise Unreadable(obs.Inconclusive.BOUND_HIT, obs.Limit.PROBES)

    hits = []
    for index in range(found):
        node = candidates.nth(index)
        how = _match(reader, node, target.item_id)
        if how is not None:
            hits.append((node, *how))

    if not hits:
        raise Unreadable(obs.Inconclusive.NO_MATCH)
    if len(hits) > 1:
        raise Unreadable(obs.Inconclusive.AMBIGUOUS)
    return hits[0]


# --- the rest of the shape ------------------------------------------------------------------


def ancestors_of(reader: Reader, page, node) -> tuple[obs.AncestorFinding, ...]:
    """A contiguous path upward, by fixed traversal, stopping at the ladder.

    `xpath=..` is a constant. There is no constructed selector here and nothing
    the page said goes into one.
    """
    chain = []
    current = node
    for step in range(1, obs.MAX_ANCESTORS + 1):
        current = current.locator("xpath=..")
        reader.charge()
        if reader.count(current) != 1:
            break
        chain.append(
            obs.AncestorFinding(
                tag=classify_tag(reader, page, current),
                role=classify_role(reader, page, current),
                depth=obs.Depth.of(step),
            )
        )
    return tuple(chain)


def find_control(reader: Reader, line):
    """A quantity control inside the line, probed by exact name.

    Exact match against the safe vocabulary. There is no whole-tree dump of
    names to sift through afterwards: asking the page for a name we did not
    expect is reading the thing this file promised not to read, and redacting
    it afterwards does not unread it.
    """
    for name in CONTROL_NAMES:
        control = line.get_by_role(obs.Role.BUTTON.value, name=name.value, exact=True)
        if reader.count(control) == 1:
            disabled = reader.read(control.is_disabled)
            return obs.ControlFinding(
                role=obs.Role.BUTTON,
                name=name,
                inside_line=True,
                disabled=bool(disabled),
            )
    raise Unreadable(obs.Inconclusive.UNKNOWN_NAME)


def find_quantity(reader: Reader, line, quantity: int) -> obs.QuantityFinding:
    """Whether some element in the line says what the parser already knew.

    The needle is never in the selector -- there is no `get_by_text(quantity)`
    here, because that would put it there. Text is read into a local, compared,
    and dropped. Nothing is learned by writing the number down: we knew it
    before we looked, which is the whole basis of the comparison.
    """
    wanted = str(quantity)
    prefix = ""
    for depth in range(1, QUANTITY_DEPTH + 1):
        prefix = "> * " * (depth - 1)
        for tag in QUANTITY_TAGS:
            nodes = line.locator(f"{prefix}> {tag.value}")
            found = reader.count(nodes)
            for index in range(min(found, QUANTITY_NODE_BUDGET)):
                text = reader.read(nodes.nth(index).text_content)
                if type(text) is str and text.strip() == wanted:
                    return obs.QuantityFinding(
                        relation=obs.Relation.EQUALS_QUANTITY,
                        depth=obs.Depth.of(depth),
                    )
    raise Unreadable(obs.Inconclusive.NO_MATCH)


# --- the walk ------------------------------------------------------------------------------


def walk(page, target: Target, reader: Reader | None = None):
    """One observation. Returns a result; raises nothing a caller must decode.

    The caller supplies an open page. This module does not make one, does not
    know how to attach to a browser, and has no command that runs it.
    """
    reader = reader or Reader()
    try:
        node, attribute, relation = find_line(reader, page, target)
        line = obs.LineFinding(
            tag=classify_tag(reader, page, node),
            role=classify_role(reader, page, node),
            attribute=attribute,
            relation=relation,
            matches=obs.Cardinality.ONE,
            ancestors=ancestors_of(reader, page, node),
        )
        control = find_control(reader, node)
        quantity = find_quantity(reader, node, target.quantity)
        return obs.ObservedResult(line=line, control=control, quantity=quantity)
    except Unreadable as stopped:
        return obs.InconclusiveResult(reason=stopped.reason, limit=stopped.limit)
    except obs.Unserialisable:
        # The result layer refused to call this an observation -- a redacted
        # tag, a broken chain, a control that is not a button. That is a real
        # answer and it is not a crash: we looked, and what we found does not
        # establish the thing we came to establish.
        return obs.InconclusiveResult(reason=obs.Inconclusive.NO_MATCH)


def observe(page, cart, pinned_store, reader: Reader | None = None):
    """Preconditions, then the walk. Never the other way round."""
    reader = reader or Reader()
    try:
        target = preconditions(cart, pinned_store)
    except Refused as refusal:
        return obs.RefusedResult(refusal=refusal.refusal)
    return walk(page, target, reader)
