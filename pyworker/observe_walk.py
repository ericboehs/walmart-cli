"""Reading the shape of a cart line without reading the cart.

Offline. There is no entrypoint here, nothing constructs a browser, and no
command runs this. The only public way in is `observe`, which establishes its
preconditions before a locator exists. See docs/DOM-OBSERVATION.md.

The single rule that drives the design: **a needle is never part of a query.**
Every selector is assembled from tokens declared in `observation`, so the item
id, the quantity, the offer id and the store id cannot appear in a locator, in
a browser exception quoting that locator, in a traceback, or in anything
written down. Page values are compared against needles in local variables and
then dropped.

That constraint is what makes tag and role resolution awkward, and the
awkwardness is the point. A Locator has no safe generic "what tag are you"
read: the easy answers are evaluate, page HTML, and reading the role attribute
by name, and this file may use none of them. Identity is established by
intersection instead -- the candidate is intersected with a locator for each
tag in the fixed vocabulary and each role, and if nothing matches the run is
inconclusive rather than better informed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import observation as obs

# --- budgets ---------------------------------------------------------------------------

NODE_BUDGET = 12
READ_BUDGET = 600
QUANTITY_NODE_BUDGET = 8
QUANTITY_DEPTH = 3

TAGS = tuple(obs.Tag)
ROLES = tuple(obs.Role)
ATTRIBUTES = tuple(obs.Attribute)
CONTROL_NAMES = tuple(obs.ControlName)
QUANTITY_TAGS = (obs.Tag.SPAN, obs.Tag.DIV, obs.Tag.P, obs.Tag.LABEL, obs.Tag.TD, obs.Tag.INPUT)

# Every candidate element as one selector, so `count()` counts unique DOM
# elements. Counting per attribute would report a node carrying the item id in
# two allowlisted attributes as two lines.
CANDIDATES = ":is({})".format(
    ", ".join(f"{tag.value}[{attribute.value}]" for tag in TAGS for attribute in ATTRIBUTES)
)

# `:scope` is explicit about what the combinator is relative to. A bare leading
# `> span` is accepted by some engines and not others, and a selector whose
# meaning depends on the engine is not a fixed selector.
QUANTITY_SELECTORS = tuple(
    f":scope {'> * ' * (depth - 1)}> {tag.value}"
    for depth in range(1, QUANTITY_DEPTH + 1)
    for tag in QUANTITY_TAGS
)

CLASSIFY_READS = len(TAGS) + len(ROLES)
# Scopes searched for a control: the id-bearing element itself, then each
# ancestor up the ladder.
SCOPES = obs.MAX_ANCESTORS + 1


def worst_case_reads(nodes: int) -> int:
    """What this run could cost if every probe misses.

    Charged before the per-node phase begins. Discovering exhaustion halfway
    through means the private reads have already happened, and a budget that
    stops you after the fact is an accounting exercise rather than a bound.

    Every term here is what the code actually spends, not what it looked like
    it spent. An earlier version budgeted one count plus a classification per
    ancestor step while the code also charged manually before counting, so the
    declared worst case was below the real one -- an estimate that the code can
    exceed is not a bound in the direction that matters.
    """
    per_node = len(ATTRIBUTES)
    ancestors = obs.MAX_ANCESTORS * (1 + CLASSIFY_READS)
    controls = SCOPES * (len(CONTROL_NAMES) + 1)
    quantity = len(QUANTITY_SELECTORS) * (1 + QUANTITY_NODE_BUDGET)
    return 1 + (nodes * per_node) + CLASSIFY_READS + ancestors + controls + quantity


# --- failures ----------------------------------------------------------------------------


class Unreadable(Exception):
    """A browser call failed. Carries no text, and keeps no original.

    Playwright exceptions quote the selector and often a slice of the page.
    `raise ... from None` clears `__cause__` but leaves `__context__` holding
    the original object, which a formatted traceback will print. So the fixed
    exception is raised *after* leaving the except block, where there is no
    exception being handled and nothing to attach.
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

# Held by a Target that preconditions built, and by no other. A page-reading
# walk needs one, so there is no way to reach the DOM without having passed
# every check -- the checks are not a doorway you can walk around.
_CLEARED = object()


@dataclass(frozen=True)
class Target:
    """What we knew before looking. Never written, never put in a query."""

    item_id: str
    offer_id: str
    quantity: int
    store_id: str
    clearance: object = field(default=None, repr=False)

    def __post_init__(self):
        # A backstop, not the diagnosis. Everything here is also checked
        # against the cart, where the specific token comes from; a Target that
        # fails these did not come from a cart this tool read, so it says so
        # with one token rather than impersonating a cart-shaped complaint.
        # Two guards reporting the same thing also makes each invisible to the
        # other's mutants, which is how three of them survived.
        for value in (self.item_id, self.offer_id, self.store_id):
            if type(value) is not str or not value:
                raise Refused(obs.Refusal.NO_CART)
        if not self.item_id.isdigit() or not self.store_id.isdigit():
            raise Refused(obs.Refusal.NO_CART)
        if type(self.quantity) is not int or type(self.quantity) is bool or self.quantity < 1:
            raise Refused(obs.Refusal.NO_CART)


def _digits(value) -> str:
    """A store or item id as the producer writes it: a string of digits.

    `str()` on both sides made 499 and "499" the same value, and turned any
    object at all into a plausible id by way of its `__str__`. Leading zeros
    survive here because they are part of the identifier, not decoration.
    """
    return value.strip() if type(value) is str and value.strip().isdigit() else ""


# --- preconditions -------------------------------------------------------------------------


def preconditions(cart, pinned_store) -> Target:
    """Everything that must be true before a single locator is built.

    Runs to completion first. A precondition checked after the first read is
    not a precondition, it is a regret, and the reads it failed to prevent have
    already happened.

    The authority for seller, store and intent is the predicates cart_mutate
    already uses, called here rather than restated. Two definitions of
    first-party is one more than can stay correct. Their messages quote item
    names and store ids, so they are called for their verdict and never their
    text: which token comes back is decided by which call raised.

    Eligibility is the strict reading: the cart holds exactly one line. Not
    "one line that qualifies", because choosing among lines is selection, and
    selection is how a target ends up named on a terminal or in a filename.
    """
    import cart_mutate

    if type(cart) is not dict:
        raise Refused(obs.Refusal.NO_CART)
    lines = cart.get("lines")
    if type(lines) is not list or len(lines) != 1:
        raise Refused(obs.Refusal.NOT_EXACTLY_ONE_LINE)
    line = lines[0]
    if type(line) is not dict:
        raise Refused(obs.Refusal.NO_CART)

    item_id = _digits(line.get("item_id"))
    if not item_id:
        raise Refused(obs.Refusal.ITEM_ID_SHAPE)

    offer_id = line.get("offer_id")
    if type(offer_id) is not str or not offer_id.strip():
        raise Refused(obs.Refusal.OFFER_ID_MISSING)

    quantity = line.get("quantity")
    if type(quantity) is not int or type(quantity) is bool or quantity < 1:
        raise Refused(obs.Refusal.QUANTITY_SHAPE)

    try:
        cart_mutate.assert_line_is_addressable(line)
    except cart_mutate.CartMutationRefused:
        raise Refused(obs.Refusal.OFFER_ID_MISSING) from None

    if not cart_mutate.is_first_party_line(line):
        raise Refused(obs.Refusal.NOT_FIRST_PARTY)

    return Target(
        item_id=item_id,
        offer_id=offer_id.strip(),
        quantity=quantity,
        store_id=_agreed_store(cart, pinned_store, cart_mutate),
        clearance=_CLEARED,
    )


def _agreed_store(cart, pinned_store, cart_mutate) -> str:
    """Store and intent, decided by the shared predicate, reported as a token.

    Both ids must be digit strings and must be equal as strings. An earlier
    version stringified both sides, so a configuration holding the integer 499
    agreed with a live cart string "499" -- and any object at all agreed with
    anything, by way of whatever its `__str__` returned.
    """
    if type(pinned_store) is not dict:
        raise Refused(obs.Refusal.NO_PINNED_STORE)
    pinned = _digits(pinned_store.get("store_id"))
    if not pinned:
        raise Refused(obs.Refusal.NO_PINNED_STORE)

    fulfillment = cart.get("fulfillment")
    if type(fulfillment) is not dict:
        raise Refused(obs.Refusal.STORE_DISAGREEMENT)

    live = _digits(fulfillment.get("store_id"))
    intent = fulfillment.get("intent")
    intent = intent.strip().lower() if type(intent) is str else ""

    if not intent or intent not in cart_mutate.DELIVERY_INTENTS:
        raise Refused(obs.Refusal.NOT_DELIVERY)
    if not live or live != pinned:
        raise Refused(obs.Refusal.STORE_DISAGREEMENT)

    # The shared predicate has the last word, so the two cannot drift apart
    # without this refusing. It is asked after the specific questions so that
    # the token is chosen by what we checked, not by parsing what it said.
    try:
        cart_mutate.assert_deliverable_from_the_pinned_store(cart, pinned_store)
    except cart_mutate.CartMutationRefused:
        raise Refused(obs.Refusal.STORE_DISAGREEMENT) from None

    return live


# --- the reader ------------------------------------------------------------------------------


class Reader:
    """Every browser call goes through here: reads *and* locator construction.

    Constructing a locator can raise, and the exception quotes the selector.
    An earlier version wrapped only the reads, so `page.locator(...)` failing
    escaped with the library's text intact -- a boundary with a hole in it is
    the shape of the thing it was drawn around.
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

    def afford(self, cost: int, limit: obs.Limit = obs.Limit.PROBES) -> None:
        """Refuse now if the remaining budget cannot cover what is coming.

        Against `remaining`, not against the original capacity. Comparing to
        capacity means a Reader that has already been used passes the check and
        runs out in the middle, which is the failure the check exists for.
        """
        if self.remaining < cost:
            raise Unreadable(obs.Inconclusive.BOUND_HIT, limit)

    def read(self, call, *args, **kwargs):
        self.charge()
        return self.build(call, *args, **kwargs)

    def build(self, call, *args, **kwargs):
        """A call that is not itself a read: narrowing, scoping, intersecting.

        Not charged, because it does not query the page, but wrapped, because
        it can still raise with a selector in the message.
        """
        try:
            return call(*args, **kwargs)
        except Unreadable:
            raise
        except Exception:
            # Anything at all becomes the same token. Naming the library's
            # classes would leave every other failure to escape with its text.
            # BaseException is deliberately not caught: a test's poison must
            # not be convertible into an innocent-looking inconclusive result.
            failed = True
        if failed:
            # Outside the except block on purpose. In here there is no
            # exception being handled, so the new one gets neither __cause__
            # nor __context__, and a formatted traceback has nothing to print.
            raise Unreadable(obs.Inconclusive.DOM_UNREADABLE)
        return None

    def count(self, locator) -> int:
        value = self.read(locator.count)
        if type(value) is not int or type(value) is bool or value < 0:
            raise Unreadable(obs.Inconclusive.DOM_UNREADABLE)
        return value

    def boolean(self, call) -> bool:
        """A bool, and nothing that merely behaves like one.

        `bool(value)` runs `__bool__`, which is code the page's library wrote,
        outside this boundary and after the read was charged. It also turns a
        non-empty string into True, which would report a disabled state nobody
        observed.
        """
        value = self.read(call)
        if type(value) is not bool:
            raise Unreadable(obs.Inconclusive.DOM_UNREADABLE)
        return value


# --- identity by intersection -------------------------------------------------------------


def classify_tag(reader: Reader, page, node) -> obs.Tag:
    """Which tag, by intersecting with each tag in the vocabulary.

    A tag outside the vocabulary is its own answer -- UNKNOWN_TAG -- and not
    "nothing matched". Flattening every unexpected shape into one reason
    reports a factual claim about the page that nobody established.
    """
    for tag in TAGS:
        # Two calls, both wrapped. Writing `build(node.and_, page.locator(t))`
        # evaluates the inner construction as an argument, outside the
        # boundary, which is how a locator failure escaped with its text while
        # looking wrapped.
        other = reader.build(page.locator, tag.value)
        if reader.count(reader.build(node.and_, other)) == 1:
            return tag
    raise Unreadable(obs.Inconclusive.UNKNOWN_TAG)


def classify_role(reader: Reader, page, node) -> obs.Role:
    for role in ROLES:
        other = reader.build(page.get_by_role, role.value)
        if reader.count(reader.build(node.and_, other)) == 1:
            return role
    raise Unreadable(obs.Inconclusive.UNKNOWN_ROLE)


# --- finding the line ------------------------------------------------------------------------

# Fixed before any live output was seen. A node can carry the item id in more
# than one allowlisted attribute, and without a declared order the reported
# contract would be whichever the loop happened to reach first. Equality is
# always preferred to a substring; within each, declaration order decides.


def _match(reader: Reader, node, item_id: str):
    """How this node carries the item id, if it does.

    The comparison happens against a local variable. The needle is an operand;
    it was never in the query that found this node.
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
    candidates = reader.build(page.locator, CANDIDATES)
    found = reader.count(candidates)
    matches = obs.Cardinality.of(found, NODE_BUDGET)
    if matches is obs.Cardinality.OVER_BUDGET:
        raise Unreadable(obs.Inconclusive.BOUND_HIT, obs.Limit.NODES)
    if matches is obs.Cardinality.ZERO:
        raise Unreadable(obs.Inconclusive.NO_MATCH)

    # What is still to be spent, against what is still available. Subtracting
    # `reader.reads` here would let charges from earlier work reduce the
    # estimate of the work ahead, so a part-spent Reader would clear a check it
    # cannot afford. Only the count just paid for comes off.
    reader.afford(worst_case_reads(found) - 1)

    hits = []
    for index in range(found):
        node = reader.build(candidates.nth, index)
        how = _match(reader, node, target.item_id)
        if how is not None:
            hits.append((node, *how))

    if not hits:
        raise Unreadable(obs.Inconclusive.NO_MATCH)
    if len(hits) > 1:
        raise Unreadable(obs.Inconclusive.AMBIGUOUS)
    return hits[0]


# --- the line container ---------------------------------------------------------------------


def control_in(reader: Reader, scope):
    """A quantity control inside this scope, probed by exact name.

    Exact match against the safe vocabulary. There is no whole-tree dump of
    names to sift afterwards: asking the page for a name we did not expect is
    reading the thing this file promised not to read, and redacting it later
    does not unread it.
    """
    for name in CONTROL_NAMES:
        control = reader.build(scope.get_by_role, obs.Role.BUTTON.value, name=name.value, exact=True)
        if reader.count(control) == 1:
            return name, control
    return None


def resolve_line(reader: Reader, page, node):
    """Climb until a scope contains a quantity control, and stop there.

    The id is not necessarily on the element holding the buttons. A plausible
    shape is the id on an anchor with the plus and minus as siblings in the
    surrounding line, and an earlier version searched only inside the
    id-bearing element and reported that no control existed. It also then
    walked four ancestors regardless, listing whatever containers happened to
    be above -- a path to nowhere in particular.

    So the path ends at the container that holds the control, and the ancestors
    reported are exactly the contiguous steps taken to reach it.
    """
    chain = []
    scope = node
    for depth in range(SCOPES):
        found = control_in(reader, scope)
        if found is not None:
            name, control = found
            disabled = reader.boolean(control.is_disabled)
            return (
                scope,
                tuple(chain),
                obs.ControlFinding(
                    role=obs.Role.BUTTON,
                    name=name,
                    inside_line=depth == 0,
                    disabled=disabled,
                ),
            )
        if depth == obs.MAX_ANCESTORS:
            break
        scope = reader.build(scope.locator, "xpath=..")
        if reader.count(scope) != 1:
            break
        chain.append(
            obs.AncestorFinding(
                tag=classify_tag(reader, page, scope),
                role=classify_role(reader, page, scope),
                depth=obs.Depth.of(depth + 1),
            )
        )
    raise Unreadable(obs.Inconclusive.UNKNOWN_NAME)


def find_quantity(reader: Reader, line, quantity: int) -> obs.QuantityFinding:
    """Whether some element in the resolved line says what the parser knew.

    The needle is never in the selector. Text is read into a local, compared,
    and dropped; nothing is learned by writing the number down, because we knew
    it before we looked, which is the whole basis of the comparison.
    """
    wanted = str(quantity)
    for index, selector in enumerate(QUANTITY_SELECTORS):
        nodes = reader.build(line.locator, selector)
        found = reader.count(nodes)
        if found > QUANTITY_NODE_BUDGET:
            # Not min(). Truncating turns "there were more than we agreed to
            # look at" into a clean answer if the first one happens to match,
            # which is a bound that reports success for the runs it was meant
            # to stop.
            raise Unreadable(obs.Inconclusive.BOUND_HIT, obs.Limit.NODES)
        for position in range(found):
            text = reader.read(reader.build(nodes.nth, position).text_content)
            if type(text) is str and text.strip() == wanted:
                depth = index // len(QUANTITY_TAGS) + 1
                return obs.QuantityFinding(
                    relation=obs.Relation.EQUALS_QUANTITY,
                    depth=obs.Depth.of(depth),
                )
    raise Unreadable(obs.Inconclusive.NO_MATCH)


# --- the walk ------------------------------------------------------------------------------


def _walk(page, target: Target, reader: Reader):
    """Private. A page read needs a Target that preconditions issued."""
    if getattr(target, "clearance", None) is not _CLEARED:
        raise Refused(obs.Refusal.NO_CART)
    try:
        node, attribute, relation = find_line(reader, page, target)
        scope, chain, control = resolve_line(reader, page, node)
        line = obs.LineFinding(
            tag=classify_tag(reader, page, node),
            role=classify_role(reader, page, node),
            attribute=attribute,
            relation=relation,
            matches=obs.Cardinality.ONE,
            ancestors=chain,
        )
        quantity = find_quantity(reader, scope, target.quantity)
        return obs.ObservedResult(line=line, control=control, quantity=quantity)
    except Unreadable as stopped:
        return obs.InconclusiveResult(reason=stopped.reason, limit=stopped.limit)
    except obs.Unserialisable:
        # The result layer refused to call this an observation. That is not
        # "nothing matched" -- claiming so would be a factual statement about
        # the page that nobody established. It is a shape we could not make
        # sense of, which is what DOM_UNREADABLE means.
        return obs.InconclusiveResult(reason=obs.Inconclusive.DOM_UNREADABLE)


def observe(page, cart, pinned_store, reader: Reader | None = None):
    """The only way in. Preconditions, then the walk, never the other way."""
    reader = reader or Reader()
    try:
        target = preconditions(cart, pinned_store)
    except Refused as refusal:
        return obs.RefusedResult(refusal=refusal.refusal)
    return _walk(page, target, reader)
