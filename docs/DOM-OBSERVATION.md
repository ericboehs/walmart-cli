# Observing the cart DOM: the minimisation contract

Status: **design under review, revised once. No tool exists. Nothing has been
observed.**

This document is the thing to argue with. It exists because the observation it
describes is the most privacy-dangerous operation this project has proposed,
and the last tool aimed at this problem was written first and reviewed second.

## Why an observation is needed at all

`walmart cart add` and `cart remove` are shut, and precondition 1 in DESIGN.md
("Driving a cart mutation: what must exist first") is an observed cart DOM. The
deleted driver treated a guessed selector matching exactly one element as
licence to click it. Cardinality is not identity. Before any tool can change a
quantity, somebody has to establish, from evidence, which element is a cart
line, which control changes its quantity, what those controls are named, and
where the current quantity is written.

## Why this is the dangerous one

Every other capture in this project records metadata and shapes. A cart DOM is
different in kind: it is a rendering of Eric's groceries, his store, his
totals, and whatever else Walmart chose to put on the page, including things
neither reviewer has thought to anticipate. A naive `page.content()` would be
the single worst artefact this repository could produce, and it would be
produced by one line.

So the design starts from what must never leave, and only then asks what can.

## Two claims from the first draft, retracted

The first draft was wrong twice, and both errors pointed the same way -- toward
a tool that reads more than it needs and cleans up afterwards.

**Retracted: "anything else is `<unrecognised>` and counted."** The first draft
said the observation would report the count of attribute names it did not
recognise. That is not implementable under the other rules and was never true:
`locator.get_attribute` must be given a name, and enumerating the names present
on a node requires page HTML, `evaluate`, or a CDP DOM dump -- each of which is
exactly what this document forbids. A claim that can only be honoured by
breaking the contract is not a claim. The tool probes the allowlisted names and
reports `no known attribute matched`; it does not know what else was there, and
saying so is the accurate answer. The remedy is not a broader read.

**Retracted: "the whole-file canaries are the guarantee."** The first draft
treated the serialiser as the privacy boundary. It is one egress of many.

## Every egress, not only the file

A needle can leave through more doors than the one being watched:

- a selector string with the value interpolated into it,
- a Playwright exception, whose text quotes the selector,
- a traceback,
- a progress message, a refusal detail, a summary line,
- a filename,
- a test's own failure diagnostic.

So: **needles are never interpolated into anything.** Not selectors, not
messages, not filenames. Comparison happens in memory, against values read into
variables and never formatted. Every browser and locator exception is caught
and mapped to a fixed error code; `str(exc)` never reaches output. There is no
verbose or debug mode that prints page values, because a debug flag is a
second, less careful serialiser.

The canaries cover all of it: the written file, stdout, stderr, raised
exception text, and generated filenames, asserted at each stage including the
failure paths. A canary only proves what it was given, so it is given the item
id, the quantity, the store id and a synthetic product name.

## A closed output type, not a filtered one

The writer does not take a dict and clean it. It takes a typed structure whose
keys are fixed in code and whose leaves can only be:

- an enum token from a frozen vocabulary,
- a boolean,
- a `Count` or `Depth`, bounded wrappers the walker constructs,
- a relation enum: `equals_item_id`, `contains_item_id`, `equals_quantity`,
- an outcome or sentinel enum.

No arbitrary `str` reaches `serialize()`, and no arbitrary `int` either. The
integer rule is not fussiness: the quantity is an int needle, so a serialiser
that accepts bare integers cannot distinguish a cardinality from Eric's
quantity. Cardinalities arrive wrapped by the walker or not at all.

Unknown types and unknown keys are **rejected**, not redacted. Redaction is a
filter, and a filter is a denylist wearing a hat.

The rejection itself is a fixed error code. Not the value, not its `repr`, not
its type name: a rejection that reports what it rejected is an egress with an
excuse, and it is the egress that fires precisely when something unexpected --
which is to say, something nobody wrote a canary for -- has reached the writer.

Property tests feed arbitrary page strings, arbitrary needle values and
arbitrary exception messages through every entry point and assert that no
egress contains any of them.

## Tags and roles are page values too

The first draft allowlisted attribute names and control names, then emitted tag
names and ARIA roles directly. That was inconsistent: a custom element name or
a custom role is identifier-shaped content chosen by the page, exactly like a
dynamic JSON key. Both come from standard vocabularies now -- the HTML element
list and the ARIA role list -- and anything outside them is redacted and makes
the observation inconclusive.

## Choosing the target without naming it

The item id does not go on `argv`, where it lands in shell history and in the
process list, and the tool prints no numbered product list to choose from.

Version one proceeds only when the parsed cart contains **exactly one eligible
line**. Anything else is inconclusive. If human selection is needed later it
arrives on non-echoed stdin and is never restated back to the terminal.

Before any DOM probing, established in memory from the parsed cart and
refused if any of it is missing, malformed or disagreeing:

- a non-blank digit-string item id;
- a non-blank offer id, because the offer is what identifies the exact variant
  and a line without one is not addressable;
- a positive integer quantity that is not a bool;
- a strictly first-party seller;
- cart intent exactly `DELIVERY`;
- a live cart store id exactly equal to the store id in a valid pinned store
  object. Having *some* store id is not agreement with the pinned store, and
  the difference is the whole point of pinning one.

These reuse the existing predicates and contracts rather than restating the
same rules in slightly different words, which is how two checks drift into
disagreeing. Any failure is `refused`, with no DOM probe attempted.

None of these values is written.

Per-line delivery eligibility is not declared proven here. The reason is
narrow and worth stating exactly: **this** observer neither collects nor
retains any per-line fulfillment signal, so nothing it produces can open
`PER_LINE_DELIVERY_PROVEN`. A separately reviewed read-only observation could
contribute evidence toward that gate later -- read-only evidence can prove a
precondition for a write, and the first draft's flat claim that it cannot was
wrong.

## What may be said

Relations and structure, never contents:

- the element carrying the item id: its tag and role, from the standard
  vocabularies, and the **name** of the allowlisted attribute that matched.
  The list for version one is fixed here, so that implementation cannot add an
  attribute after seeing what live output happened to contain:
  `data-item-id`, `data-usitemid`, `data-testid`, `id`, `href`. The
  observation may report which of them worked. It may not grow the list;
  widening after a look is how a guess becomes a finding.
- whether that attribute's value **equals** the known item id or merely
  contains it;
- the ancestor chain to the nearest element also containing a quantity control:
  tags, roles and depth;
- how many elements matched, as a `Count`;
- the quantity control: its role, whether it lies inside the line's subtree,
  and whether it is disabled;
- whether some element in that subtree has a text value **equal to** the
  quantity the parser already read. The number is never written, and nothing
  is learned by writing it, because the parser already knew it.

Accessible names are probed by exact match against a small safe vocabulary --
`Increase quantity`, `Decrease quantity`, `Increase`, `Decrease`, `Add one`,
`Remove one`. The tool does not take an accessibility snapshot and does not
read every button's name to redact later; asking the page for a name it did not
expect is reading the thing it promised not to read. If no exact match is
found, the outcome is inconclusive. If it must record that an unknown button
exists, it records a role and a count, never a name.

## The walk, spelled out so a needle cannot get into a selector

The rule "never interpolate a needle" is easy to agree with and easy to break
by writing the obvious selector, so the feasible alternative is written down
rather than left to the implementer:

1. Count a static, needle-free locator over all candidate elements.
2. If that count exceeds the node budget, stop: `inconclusive`. The bound is
   checked before any per-node read, not while looping.
3. Iterate the bounded nodes by index, and on each probe only the fixed
   attribute names above.
4. Compare each returned value against the needle **in a local variable**.
   The needle is an operand, never part of a query.
5. Reach ancestors by fixed traversal, not by a constructed selector.
6. Compare quantity text the same way, after the bounded reads.

No selector, no locator description and no Playwright call contains a needle at
any point. That is a property the implementation can be read against, and the
fakes assert it by recording every selector string the tool constructs.

## Read-only by construction

- `session.goto` only, which runs `safety.assert_navigable`. Never `page.goto`.
- No interaction method at all: the list in `test/walmart/recon_tools_test.rb`,
  including both `evaluate` forms.
- No `content()`, no screenshot, no accessibility dump, no CDP DOM snapshot.
- No POST. No challenge handling beyond stopping and reporting one was seen.
- Our own tab, attached browser, loopback-validated CDP.
- Fakes must prove the interaction count is zero.

## Bounds are part of the result

One shared budget covers candidate nodes, ancestors and attribute probes, and
the total is bounded **before** any per-node read rather than while looping.
The output has a byte cap checked before the atomic 0600 write. Hitting any
bound makes the run inconclusive and non-zero; there is no partial `observed`.

Tests count actual locator reads, not output entries. Counting the output is
how a bound gets tested by measuring the wrong thing, which this repository has
already done once.

## Outcomes

- `observed`, exit 0: every required relation established, inside the
  vocabulary and inside the bounds.
- `refused`, exit 2: a safety gate, a challenge, or an unmet precondition.
- `inconclusive`, exit 3: no match, ambiguity, a name outside the vocabulary, a
  bound hit, or a DOM that could not be read.

Zero evidence is not success. Any future `--json` mode emits exactly one
document and never a raw error string.

## When the tool arrives

The same commit updates the status line at the top of this file -- a test
enforces that -- and adds a source-surface test proving the tool contains no
interaction method, no raw navigation, no HTML, screenshot or accessibility
dump, and no path by which arbitrary input reaches the serialiser.

It opens no gate by itself. It produces evidence for a review, and the acting
implementation arrives in a different commit again.
