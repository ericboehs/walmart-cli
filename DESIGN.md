# walmart-cli design

A public, Linux-compatible command-line client for building a Walmart grocery
delivery cart, browsing Walmart's personalized **My Items** catalog, and keeping
a local purchase-history archive.

This tool deliberately stops before checkout. It can mutate the shared online
cart, but it must never place an order. The user reviews substitutions,
delivery slot, tip, payment, and the final order in Walmart's iPhone app.

## Who this is for, and the position on automated access

This is a personal tool for one Walmart customer to reach **his own account and
his own data** from a terminal: his order history, his cart, his items. There is
no third party's data in scope, nothing is resold, and nothing is done at a
volume or speed a person could not do by hand in the same session.

Walmart serves that account through a website protected by bot detection
(PerimeterX/HUMAN — the "Press & Hold" widget). A browser driven by
Playwright/CDP is detectable as automated, and the challenge fails on repeat
regardless of how genuinely a human holds the button. So the question is not
whether the user is a human; he is. The question is whether the *client* he uses
to reach his own account is one Walmart recognises.

**The project's position, decided by the owner:** this tool will work around bot
detection where it must, in order to give a real customer terminal access to his
own account. That is a change from the original stance and it is written down
rather than quietly adopted, because it carries real costs and someone reading
this later deserves the actual argument.

### What that does and does not license

In scope:

- Presenting a browser Walmart accepts, for the owner's own authenticated
  session.
- Reusing a real, human-established session rather than manufacturing one.
- Human-rate traffic: one person's shopping, not a crawler.

Out of scope, permanently, and not up for a later "while we're here":

- Any other person's account or data.
- Scraping at volume, price-monitoring fleets, inventory sniping, scalping,
  resale, or anything whose value comes from doing it faster or more often than
  a person could.
- Credential stuffing, account enumeration, or anything touching an account the
  owner does not own.
- Defeating a payment control, a fraud check, or a spending limit. Bot detection
  and payment authorisation are different things, and only the first is in
  scope.

### The costs, stated plainly

- **walmart.com's Terms of Use apply to the account holder** regardless of this
  repo having no terms of its own. Automated access is very likely a breach of
  them. This is a contract question, not a criminal one — but it is not nothing.
- **The realistic penalty is the account, not a lawsuit.** A flagged account can
  lose order history and stored payment methods, and it is the same account the
  household actually buys groceries with. That risk is the owner's to take and
  he has taken it knowingly.
- **It is an arms race on their schedule.** Anything that works today can stop
  working after a deploy on their side, at an hour we do not choose.

### Preferred approach: be a real browser, not a fake human

Ranked by how well each survives the above, best first. Prefer the earliest one
that works.

1. **Attach to the user's own everyday Chrome** over CDP
   (`connect_over_cdp`) — a browser he launched, with his profile, where he is
   already signed in and has cleared challenges himself, as a person. Nothing is
   forged, because nothing is fake: the session is genuinely human. This is the
   most honest reading of "I am not a bot" and it is the direction to try first.
2. **A long-lived persistent profile** that accumulates real history from
   sessions the user drove himself, used for later headless reads.
3. **Reduce the automation fingerprint** of a browser we launch, so an
   ordinary-looking Chrome is not rejected for being scripted.
4. **Forging sensor/telemetry payloads** — last resort. It is the most
   brittle, the most clearly adversarial, and the least defensible if anyone
   ever asks what this tool does. Do not start here.

Whichever is used, the rate limits stay: this tool never retries a block
automatically, never runs faster than a person shopping, and stops when told to
stop. Those exist to keep the traffic honest, and they matter more once the
detection layer is no longer doing that job for us.

### Known limitation: history deeper than the page ceiling is unreachable

`order sync` walks purchase history from page one, clicking Next, and stops at a
200-page ceiling. It does not replay a pagination cursor: the worker clicks
Next deliberately, because replaying a cursor earned a 429.

The consequence is a liveness limit, not a data-loss one. A second sync does not
continue from where the first stopped -- it restarts at page one. Already
archived ids are sent so their detail pages are skipped, which makes a re-sync
cheap, but skipping a detail page does not advance the pagination budget. So for
a history longer than the ceiling, every run traverses the same pages and stops
at the same frontier, and `--all` can never complete no matter how often it is
re-run.

At five orders per page the ceiling is roughly a thousand orders, so this is
latent for most accounts. The messaging no longer suggests re-running as a
remedy for `page_limit`, because it is not one.

Fixing it properly needs a design, not a guess. One shape is a total traversal
ceiling plus a separate budget counted only after the walk passes the
already-known frontier -- but that needs real traffic accounting, since simply
not counting known pages weakens the unattended ceiling that exists to stop a
bug becoming abuse. Actual cursor resume is blocked on observing a mechanism
that does not earn a 429, and must not be invented.

### "Known" means returnable, not indexed

`index.json` and the record files can disagree: a file goes missing, is
truncated, or holds another order. The index entry survives either way, so any
predicate built on index membership is answering a different question from the
one its callers ask.

- `Archive#record` raises `ArchiveError` when the index claims an order and the
  file cannot be returned, and returns nil only for an id we never archived.
  Damage and never-bought-it need different responses, so they get different
  answers.
- `Archive#readable_ids` is what may travel to the worker as `known_ids`.
- `Archive#write`'s unchanged shortcut asks the disk, so an ordinary sync
  repairs damage rather than discarding the replacement.
- The reader confines index paths to the archive and refuses symlinks out of
  it. The writer never creates such an entry; the reader asserts it anyway,
  because the index is a file on disk and the invariant should be executable
  rather than merely true of today's writer.

### A newer format is preserved, never repaired

`_format` on a record says which version of this CLI wrote it.

- Greater than `FORMAT`: refused for reading *and* for writing, including under
  `--full`. An older process must not overwrite a record it has already
  admitted it cannot read; that drops every field the newer version added.
  These ids are reported before the network and skipped rather than fetched,
  because the only safe thing to do with the answer would be to discard it.
- Absent, zero, or not an integer: repairable damage. A missing marker is not
  evidence of a newer writer, and every record this CLI has written carries
  one, so a re-fetch fixes it and the result is our own format.

`force` means "rewrite a record we understand", never "discard one we do not".

The format check runs before every other content test. Once a file claims a
format this CLI does not understand, it is in no position to decide that
something *else* about the contents makes the file disposable.

The worker takes two id lists, and they are not interchangeable:

- `known_ids` — records we hold and can return. Skipped on an incremental sync,
  deliberately ignored under `--full`, which is what "full" means.
- `protected_ids` — records we refuse to overwrite. Skipped always, `--full`
  included, because the page load would be spent only to have the answer thrown
  away at write time.

### A finished walk is not a complete archive

The worker's `complete` means "no next cursor". It proves the walk reached
Walmart's oldest page and says nothing about the rows skipped on the way down,
so it cannot on its own support a global claim:

- `--year 2024` skips every newer row all the way down and still ends with no
  cursor.
- A lower-bounded window has the same hole. A final page holding one in-window
  row and one older row is not a frontier — the in-window row clears it — so
  the walk ends `complete` having skipped the old one. The default rolling
  window is lower-bounded, so this is the ordinary case.

`Archive#complete?` therefore requires a settled walk **and** no window at all.
`window_complete?` stays true for both, because reaching the edge of what was
asked for is still a success.

### A cart change is not done until the cart says so

`updateItems` sets an absolute quantity. Exposing that would let a count read
minutes ago overwrite an edit someone made from their phone in between, so the
CLI speaks only in deltas and the absolute number is derived inside a single
worker action from a read taken moments before. The window between that read
and the write is the lost-update window: narrow, documented, and not zero.
`lineItemId` was considered as a compare-and-set token and rejected on the
evidence -- a product-page add sends one for a line that does not exist yet, so
it cannot encode a server-side revision.

**The feature is gated.** No successful `updateItems` response has ever been
observed, so there is no shape against which to define acceptance. Rather than
guess one, the module ships `ACCEPTED_RESPONSE_SHAPE = None`, refuses before
opening a browser, and says why. Lifting the gate requires a captured response.

Success requires two independent things: Walmart acknowledged the request, and
a fresh read of the cart shows the intended quantity. Neither alone is enough.
A 200 carrying GraphQL errors is a failure wearing a success's status code, and
a cart that happens to match the target proves nothing, because an edit from
another device looks identical. When those two disagree the answer is
UNVERIFIED, and it stays UNVERIFIED -- a corrective second write is how one lost
update becomes two.

Four outcomes, not two. `applied`, `rejected`, `contract_stale` (Walmart no
longer recognises the pinned persisted query) and `unverified`. Only the last is
exit 3, and anything this version does not recognise is treated as unverified
rather than as success.

### The order flow is refused by name, and recording is an allowlist

Walmart's order flow rides the same `/orchestra/<app>/graphql/<Operation>/<hash>`
shape as an ordinary product read, so a URL carrying an entire order submission
contains no `/checkout` and no `/payment`. The URL guard was never wrong; it was
answering a different question, and nothing had asked this one until a capture
recorded three order-flow operations.

`operation_of` therefore inherits every discipline the URL guard already had —
bounded percent-decoding, path parsing rather than pattern matching, a
case-insensitive structural marker, total over junk — because the first version
had none of them and failed in exactly those four ways. `assert_navigable` asks
it, which is the part that matters: a guard that is not on the path that
executes is a guard nobody is behind.

The denylist is the alarm, not the lock. It cannot name an operation Walmart has
not shipped yet, so nothing depends on it alone. The capture persists only
`updateItems`, the mutation path will construct only `updateItems`, and neither
accepts a caller-chosen operation. Where a body carries `operationName`, it must
agree with the path: a request the guard reads as one thing and the server reads
as another is one we have no reason to send.

### Settlement is a property of the note, not of the reason

`SyncNote` answers "did this run finish what it set out to do", and both the
archive and the sync exit code ask it — those two disagreeing is a
contradiction this project has already shipped once.

- `complete` settles.
- `cutoff` settles **only with a lower bound**. It means "we reached the bottom
  of the window we were given"; with no window the same word describes a walk
  that stopped somewhere arbitrary, and reason-only membership called that a
  success, exited 0, and printed nothing.
- An incoherent note never settles: an upper bound with no lower bound, an
  empty or inverted interval, a cutoff with no window. Today's worker cannot
  produce these, but the protocol is directly invocable and a future worker bug
  would otherwise be waved through. Not settled means `UNVERIFIED`, which is
  the honest answer when the note contradicts itself.

`SyncNote.from` is total over any input. `index.json` is a file on disk and
`sync` can hold an array, a number or a string; each of those crashed a
different reader or, worse, read as never-synced. A malformed note is its own
state (`invalid_sync_note`): recorded, incoherent, never settled. Corruption is
not absence — telling someone nothing has been synced when the note is damaged
sends them to create something they already have, and the records themselves are
untouched.

One note is built per run and answers for both the exit code and the words. The
status went state-aware first and the prose stayed reason-only, so the exact
contradictions `SyncNote` exists to catch exited 3 in silence. An incoherent
note is named without a guessed diagnosis: we cannot tell whether the reason or
the window is the mistaken half, and claiming either would be inventing one.

### Integrity is reported separately from coverage

How much history was asked for and how much of what we hold can be produced are
different questions, and the answer to one must never stand in for the other. A
scoped window sentence used to satisfy "did we say something" and leave
unreadable records unmentioned. They are separate branches now, and JSON carries
`damaged` and `incompatible` as distinct counts, because their remedies differ:
sync repairs one, only an upgrade reads the other. A read that omits records
never advises a sync for the ones a sync cannot fix.

One integrity scan is taken per render and reused. Two scans could answer from
two views of the disk and emit a single document saying `complete: true` and
`healthy: false`.

### Windows are intervals, and `--year` means the calendar year

`order sync --year 2024` sends `since=2024-01-01` *and* `before=2025-01-01`.
The upper bound is exclusive, and only `--year` has one: the rolling default
window and `--all` both mean "up to now".

In the worker the two ends are not symmetric. The lower bound is a frontier —
a page whose dated rows are all below it ends the walk. The upper bound never
stops anything, because history is read newest first and rows above the window
are exactly what we page through to reach it; they are skipped without a detail
fetch and they clear the frontier.

### Planned: accumulated coverage, not a single stop note

Not implemented. Recorded before writing it because the migration needs a
conservative rule and getting that wrong quietly overstates what the archive
holds.

The shape:

- Separate `last_attempt` from accumulated `coverage`. A failed or narrow run
  must never erase an earlier, stronger claim.
- `coverage` is a set of verified date intervals. `--year` contributes a
  bounded interval; the default window a lower-bounded one; `--all` an
  unbounded one.
- A settled run (`complete` or `cutoff`) adds its requested interval. A failed
  run adds **zero** intervals and leaves prior coverage untouched; it may still
  add records. This needs no protocol change, which is the reason to take the
  conservative reading in v1.

  Enlarging coverage from a *failed* run would need one. It must not be
  inferred from `seen` or `oldest_date`: the walk extends `seen` with a whole
  index page before reading any detail, so a block on the first detail leaves
  every later row on that page counted as seen. That would require an explicit
  `completed_through`, advanced only once every in-window row on a page was
  read, known, or protected. Not in v1.
- Every claim carries `verified_at`. Orders placed after that timestamp are not
  implied by it.
- **Merging is not scalar, because the timestamps differ.** Two intervals that
  overlap were verified at different moments, and a merged one has to pick.
  Taking the max overstates the older segment. The rule: intervals are kept
  split by `verified_at` and merged only when the timestamps are equal.
  Adjacent segments are reported as a list, not collapsed. If that proves
  unwieldy, the fallback is merging with `verified_at` = min, accepting the
  loss of freshness — but the choice is made explicitly, not left to whichever
  merge was written first.
- Coverage and archive health are separate axes, and both are published raw:
  `history_complete` and the coverage intervals on one, `damaged` and
  `incompatible` counts on the other. `complete` is *derived* —
  `history_complete && healthy` — and never stored. A single stored flag is how
  the old ambiguity comes back under a new name. This part is already
  implemented; the intervals are not.
- Migration: an existing note knows `since` but, unless it was written after
  the bounded-window change, has no upper bound. Treat those as lower-bounded.
  Do not reinterpret an old `cutoff` as calendar-year coverage.

### Known limitation: coverage describes the last run, not the archive

`index.json` holds one sync note, and it describes the run that wrote it. Two
consequences, both real and neither fixed:

- A narrow `--year` sync after a complete `--all` overwrites `complete` with
  `cutoff`, so an archive that genuinely holds everything will describe itself
  as covering only the narrow window. Pessimistic rather than dangerous, but
  wrong.
- `oldest_date` is the minimum date seen *by that run*, not the oldest record on
  disk. Any sentence that sounds like an archive floor must not be derived from
  it.

The fix is accumulated coverage -- tracking which windows have been read, rather
than what the last walk did -- and it needs a design. Until then, the coverage
sentence talks about the last sync and says so.

### Still unresolved: does "make orders" include placing them?

The owner's framing is "how I as a user of walmart.com want to make orders from
a CLI". Today's design stops hard before checkout (see Safety invariants), and
that has not been changed here — removing it means a command that spends money
without a human looking at a final total, which is a different decision with a
different failure mode, and it needs to be made explicitly rather than inherited
from a change about bot detection. **Until the owner says otherwise, no checkout,
no place-order, no payment, no slot reservation.**

## Product decisions

These are settled requirements, not open design questions.

| Concern | Decision |
|---|---|
| Primary use | Grocery delivery from the user's pinned Walmart store |
| Cart | Walmart's shared online cart is authoritative; edits made in the app must appear in the CLI and vice versa |
| Checkout | Out of scope; never submit or place an order |
| Seller | V1 permits only items sold by Walmart, not marketplace sellers |
| Fulfillment | Cart mutations require delivery availability at the active, pinned store |
| Variants | Never guess size, flavor, pack, or other variants; the user selects the exact listing |
| Quantity | Adding always increments; default `1`, or `--increment N`. There is no set-quantity operation |
| Removal | Prompt unless `--confirm` is supplied |
| Weighted goods | Show and preserve Walmart's actual selectable unit/count and estimated price |
| Search picker | Use `fzf` when detected; portable numbered fallback. Use multi-select fzf where appropriate |
| Search ordering | Previously purchased / My Items matches before the rest of the catalog |
| Store safety | Pin an expected store id; print name and ZIP and refuse cart mutations when Walmart's active store differs |
| Orders | Archive all available account orders and associated in-store receipts |
| Default sync | Two years; `--all` requests all available history |
| Bought-before | Count, latest date, latest paid price, and delta from current price |
| Reorder | Interactively choose items from an archived order; unavailable lines remain visible but disabled |
| My Items | List and search Walmart's personalized My Items, then add selected items to cart |
| Sensitive archive data | Do not store delivery addresses, names, payment details, or card fragments |
| Auth | Dedicated browser session; credentials may come from macOS Keychain, with manual login fallback |
| Browser visibility | Headless normal commands; visible login. If challenged headlessly, fail with login instructions rather than opening a window unexpectedly |
| Output | Human-readable by default; `--json` everywhere |
| Portability | Public release; macOS and Linux |
| Runtime shape | Ruby CLI plus Python Playwright worker |

Substitutions are intentionally not represented. Walmart applies them during
checkout/order review, which this CLI never enters.

## Safety invariants

These outrank convenience and implementation details.

1. **There is no checkout or place-order command.** Do not implement a hidden
   worker action for either operation.
2. Browser automation must not navigate into checkout as part of a cart
   mutation. It must never click controls whose purpose is checkout, payment,
   reservation, or order submission.
3. Every cart mutation verifies the active Walmart store against the locally
   pinned store immediately before mutation. A mismatch is a hard failure.
4. Every add verifies all of the following from live data:
   - exact selected item/variant;
   - sold by Walmart;
   - eligible for delivery from the pinned store;
   - requested increment is a positive integer.
5. A successful mutation is confirmed by re-reading the cart. A click or HTTP
   response alone is not proof. Distinguish rejected, unverified, and verified
   outcomes.
6. Removal and decrement-to-zero require an interactive confirmation unless
   `--confirm` is present.
7. `--json` is non-interactive. Commands needing a selection must receive an
   exact stable identifier. Destructive commands still require `--confirm`.
8. Credentials and browser session material are never printed, logged, placed
   in argv, committed, or saved in normal configuration.
9. Recorded HTML/JSON fixtures must be scrubbed and covered by a fixture
   hygiene test before entering the repository.
10. Empty results must identify what was searched and distinguish a healthy
    empty set from a blocked, signed-out, or selector-drift failure.

## Command surface

Names may change only when live reconnaissance demonstrates that Walmart's
model makes one misleading.

```text
walmart login [--manual] [--force]
walmart logout
walmart doctor
walmart config show|path|init
walmart version

walmart store show
walmart store pin

walmart search <query> [--fresh]
walmart item <item-id-or-url> [--fresh]

walmart cart show
walmart cart add <query|item-id|url> [--increment N]
walmart cart increment <cart-item-id|query> [N]
walmart cart decrement <cart-item-id|query> [N] [--confirm]
walmart cart remove <cart-item-id|query> [--confirm]
walmart cart add-from-order <order-id>

walmart my-items list [--all]
walmart my-items search <query>

walmart order sync [--year YYYY|--all] [--full]
walmart order list [--year YYYY] [--type TYPE] [--limit N]
walmart order show <order-id>
walmart order search <query> [--type TYPE]
```

Global flags:

```text
--json          one complete machine-readable JSON document
-q, --quiet     suppress non-essential status output
-v, --verbose   diagnostic logs, never secrets or page bodies containing PII
--no-color      disable terminal color
--fresh         bypass an eligible read cache
--confirm       approve removal/decrement-to-zero; never means approve checkout
```

### Search and item selection

`walmart search "whole milk"` displays exact offers, including at least:

```text
   price   unit price  delivery  bought                     item
1  $3.12   $0.02/fl oz yes       4x · Jan 12 · $2.98 (+.14) Great Value Whole Milk, 1 gal
2  $4.18   $0.03/fl oz yes                                  Hiland Whole Milk, 1 gal
```

Interactive add/search selection uses:

1. `fzf` when it exists on `PATH` and stdin/stdout are TTYs;
2. a numbered prompt otherwise;
3. no prompt under `--json`, piped input, or non-interactive execution.

The picker must expose variant-defining text, price, unit price, seller,
delivery eligibility, and bought-before context. It must not preselect an
ambiguous result.

Search ordering is:

1. exact/relevant My Items or bought-before matches;
2. other Walmart-sold, delivery-eligible results;
3. ineligible or marketplace results only when useful diagnostically, clearly
   disabled and never selectable for add.

### Cart semantics

`cart add` increments by one unless `--increment N` is given. If the exact item
is already present, its existing quantity increases by that amount. There is
no `--qty` and no absolute setter.

`cart increment ITEM [N]` and `cart decrement ITEM [N]` default to one.
Decrementing below one is a removal and follows removal confirmation rules.

`cart show` always reads Walmart's live shared cart. It includes edits made in
the mobile app. Each line should include a stable cart-line identifier, exact
item/variant, quantity or weighted unit, current price/estimate, seller,
fulfillment, availability, and any warning Walmart currently attaches.

The formatter prints the verified pinned store above mutations. The name and
ZIP described here have never been observed, so what it can print is the store
id; see the note under "Store pinning".
JSON includes a structured `store` object rather than relying on prose.

Mutation result states follow the proven amazon-cli pattern:

| Exit | Meaning |
|---:|---|
| 0 | Mutation completed and verified by re-reading the cart |
| 1 | Walmart rejected it or verification showed it did not happen |
| 2 | Usage error, declined prompt, missing `--confirm`, or unsafe/ineligible request |
| 3 | Mutation may have happened but verification could not be completed |

### Driving a cart mutation: what must exist first

The `updateItems` request has been observed going out. Nothing has ever been
recorded coming back, which is why `cart add` and `cart remove` refuse before
opening a browser: with no known response shape there is no way to distinguish
a change that worked from one that silently did nothing.

A script to obtain that evidence -- one quantity change, driven, while the
recorder watched -- was written, reviewed, found unsafe, gated shut, and then
deleted. It is recorded here rather than in a comment because a comment lives
in the file it is warning you about.

Why it was deleted rather than left disabled: its gate was one boolean, and its
own comment claimed to be "not openable by editing this line alone". That was
false. `run()` checked that constant and nothing else, so a single edit
restored the whole unsafe path. A shut door whose key is one source edit is a
TODO wearing a lock. Deleting the acting code is the only version of "off" that
does not depend on the next person reading the docstring.

What it got wrong, all of which the replacement must get right:

1. **The DOM was never observed.** It treated "exactly one element matched a
   guessed selector" as licence to click. Cardinality is not identity. A
   read-only, minimised, reviewed observation of the cart DOM comes first:
   which element is a line, which control changes quantity, what the controls
   are named, and where the current quantity is written.
2. **No pre-read.** `--delta -1` on a line whose quantity is 1 does not
   decrement it, it removes it, and nothing knew which case it was in.
3. **Restore was unconditional.** It restored in a `finally` because a click
   had been *attempted*, even when waiting for the first answer had raised --
   a second absolute write possibly issued while the first was still in flight,
   with no ordering guarantee about which Walmart applies last. A restore must
   be conditional on knowing what the first write did.
4. **Nothing was verified.** No read-back, so "restored" meant "clicked
   something". That is a 200 meaning success wearing a different hat.
5. **It was a second path around the first path's guards.** It checked none of
   what `pyworker/cart_mutate.py` checks: first-party seller, exact offer and
   item variant, DELIVERY intent, pinned-store agreement, per-line delivery
   eligibility. Calling a tool recon does not make the write it performs less
   real, and a second route that skips the checks is how the checks stop
   meaning anything.
6. **Two outcomes where there are five.** The replacement is a state machine --
   PRECHECKED, FIRST_UNVERIFIED, FIRST_VERIFIED, RESTORE_UNVERIFIED, RESTORED
   -- with tests asserting both the number of writes and the final quantity.
   Either unverified state exits 3 and says the cart's condition loudly on
   stderr. No cart state may be lost to a traceback or buried in a notes array.

Two more requirements that do not depend on the DOM and must be satisfied by
whatever comes back:

- **The item id is validated before it reaches a selector.** Every `usItemId`
  ever observed is a non-blank string of digits. The deleted version
  interpolated an arbitrary argument straight into a CSS selector, which is an
  injection sink kept as a default.
- **Persisted notes carry categories, not contents.** The deleted version wrote
  the item id, the delta and the control's label into a file whose stated
  contract is value-free, and the whole-file canary did not cover the notes.

Until all of this exists, the way to observe a cart mutation is
`scripts/capture-cart-mutation.py`, where a person clicks and the tool only
watches. `test/walmart/recon_tools_test.rb` enforces the boundary in the
meantime: no recon script clicks, and navigation goes through the gated entry
point.

### Store pinning

`store pin` reads Walmart's active grocery store from the authenticated
session and saves only:

```json
{
  "store_id": "..."
}
```

The name and ZIP in the original design are not written, because neither has
ever been observed in a Walmart response. The only store field seen so far is
`storeId`, in the cart's fulfillment node, which is where `store pin` reads it.
Adding the other two would mean choosing field names nobody here has seen, and
a plausible mapping is still a guess. `store show` says they are absent rather
than leaving a blank that looks like an empty answer from Walmart.

It does not save a street address or delivery address. `store show` compares
pinned and active values. Read commands may show a loud mismatch warning; cart
mutations refuse it.

### My Items

`my-items` reflects Walmart's account-personalized collection, not a locally
invented shopping list.

- `my-items list` browses it, paginating with `--all`.
- `my-items search QUERY` searches/filter it.
- Interactive output permits selecting one or several items to add.
- Use `fzf --multi` when available and a portable comma/range picker otherwise.
- Exact selected variants still undergo live seller, store, and delivery
  checks before each add.

The implementation must preserve Walmart's source grouping if the site exposes
meaningful groups such as purchased before, favorites, or saved items; do not
invent those categories in advance.

### Orders, receipts, and reordering

`order sync` defaults to a rolling two-year window. `--year` narrows it and
`--all` requests all history Walmart exposes. `--full` re-fetches known order
records; otherwise sync is incremental.

Online orders and associated in-store receipts share one archive and one list,
with a normalized type such as `delivery`, `pickup`, `shipping`, or `in-store`.
Preserve Walmart's raw label when normalization is uncertain.

Archive records contain only what supports history and reordering:

- order/receipt id and date;
- fulfillment type, status, and store id/name when present;
- totals and item-level paid prices;
- item id, offer id when needed, UPC when available, title, variant, quantity,
  unit, image URL, and seller;
- `_synced_at` and source metadata needed to diagnose parser drift.

Explicitly omit recipient names, addresses, phone numbers, payment methods,
card fragments, and unneeded account identifiers.

`cart add-from-order ID` presents every archived line. Unavailable, marketplace,
or no-longer-deliverable items remain visible but disabled with a reason. Use
`fzf --multi` when available, or accept portable selections such as `1,3-5` or
`all`. Nothing is preselected. Each chosen line is re-resolved live because old
offer ids, variants, price, seller, and fulfillment can change.

### Bought-before annotation

Build a local item history index from archived orders. Prefer stable Walmart
item ids; use UPC as a secondary join only when observed data proves it stable.
Do not join primarily on title.

For each live result report:

```json
{
  "bought": {
    "count": 4,
    "last_date": "2026-01-12",
    "last_price": 2.98,
    "current_price": 3.12,
    "change": 0.14
  }
}
```

`change` is null unless both prices are real and comparable for the same unit
or package. A changed package size must not be presented as a price increase.
Archive failure costs the annotation, not the live search; warn once.

## Architecture

```text
bin/walmart                         Ruby entrypoint
lib/walmart/
  cli.rb                            parsing and dispatch
  config.rb                         XDG paths and non-secret settings
  secrets.rb                        credential-provider boundary
  worker.rb                         one-shot Python process, NDJSON protocol
  store.rb                          order files and history index
  cache.rb                          short-lived read cache
  formatter.rb                      tables, JSON, bought-before annotations
  picker.rb                         fzf and numbered/multi fallback
  commands/
    login.rb, doctor.rb, config.rb
    store.rb, search.rb, item.rb
    cart.rb, my_items.rb, order.rb
pyworker/
  browser.py                        persistent authenticated browser context
  login.py                          visible sign-in and session persistence
  fetch.py                          action dispatch and NDJSON events
  extractors.py                     pure parsing/normalization helpers
  fixtures/                         scrubbed captured responses/markup
  test_*.py                         parser and workflow tests
```

Ruby owns UX, prompts, safety confirmation, storage, formatting, and stable
public JSON. Python owns Playwright and Walmart page/API interaction. Workers
are one-shot subprocesses communicating over NDJSON. Credentials, when used,
travel only over the worker's stdin and are never emitted back.

### Transport strategy

Do not decide UI scraping versus internal JSON/GraphQL APIs by taste. Observe
what Walmart's real web app does through Playwright/CDP:

- Prefer stable read APIs for search, cart reads, My Items, and order history
  when their request contracts can be reproduced safely.
- Prefer the real browser session for authentication and bot-sensitive calls.
- Cart writes may use observed internal requests only after their anti-CSRF,
  store, fulfillment, and verification semantics are understood and covered by
  fixtures/tests. Browser UI automation is an acceptable first implementation.
- Never call an inferred endpoint. Capture the production request first.
- Keep every network operation behind one Python client/browser boundary so
  retries, auth expiry, blocks, and diagnostics are consistent.

Initial reconnaissance must determine, rather than assume:

1. Walmart bot protection: which vendor, which signals, and which of the ranked
   approaches above is sufficient. Observed so far: PerimeterX "Press & Hold",
   which fails repeatedly against a Playwright-driven Chrome even when a human
   performs the hold.
2. Login flow, MFA/captcha behavior, session lifetime, and profile reuse.
3. Stable item, offer, cart-line, store, and order identifiers.
4. Store selection and delivery fulfillment context.
5. Seller and delivery-eligibility fields.
6. Search/My Items ranking and pagination contracts.
7. Cart read/add/increment/decrement/remove request contracts.
8. Weighted-item quantity semantics.
9. Online order and in-store receipt history surfaces and pagination limits.
10. Reliable success verification after every cart mutation.

### Browser/session model

**Superseded in part** by "Preferred approach: be a real browser, not a fake
human" above. A dedicated profile that has never been driven by a human is
exactly the profile bot detection rejects, so the ranked options there — the
user's own Chrome over CDP first — take precedence over the isolation-first
design below. The rest of this section still holds.

Use a dedicated persistent profile under the XDG data directory when a dedicated
profile is used at all. Prefer installed Chrome/Chromium and fall back to
Playwright Chromium where supported. Login is visible. Normal commands are
headless if reconnaissance proves that works reliably.

If a headless command sees captcha, bot denial, sign-in, MFA, or an unsupported
challenge, it fails safely with `run: walmart login`; it does not silently open
a window.

Only one process may own the dedicated profile at once. Detect/profile-lock
contention and report it rather than corrupting session state.

### Credentials

V1 supports macOS Keychain plus manual browser login. The fixed generic
password records are:

| Service | Account | Value |
|---|---|---|
| `walmart-cli` | `email` | Walmart login email |
| `walmart-cli` | `password` | Walmart password |

The code reads them with argv-array subprocess calls equivalent to:

```sh
security find-generic-password -s walmart-cli -a email -w
security find-generic-password -s walmart-cli -a password -w
```

Missing credentials are not an error when a visible/manual login is possible.
Linux initially uses manual login; the `Secrets` boundary must permit a future
Secret Service/libsecret provider without changing commands or worker code.
Never fall back to plaintext config.

### XDG storage

```text
$XDG_CONFIG_HOME/walmart/config.json
$XDG_DATA_HOME/walmart/browser-profile/
$XDG_DATA_HOME/walmart/orders/<year>/<id>.json
$XDG_DATA_HOME/walmart/index.json
$XDG_CACHE_HOME/walmart/live/<kind>/...
$XDG_STATE_HOME/walmart/sync.log
```

Browser profile and any exported storage state are mode-restricted. Cart reads
must not be cached. Search/item/My Items reads may use a short cache only if the
final add revalidates current eligibility and price. Empty/blocked results are
never cached.

## Testing and quality gates

- Ruby: Minitest, hermetic XDG directories, injected worker/process doubles.
- Python: parser tests against scrubbed live fixtures and workflow tests against
  browser/page doubles.
- No test may touch Walmart, Keychain, or the real browser profile unless
  explicitly marked live.
- CI runs supported Ruby and Python versions on macOS and Linux where practical.
- Format/lint both languages.
- Test the safety invariants directly, especially:
  - no checkout worker action or navigation;
  - store mismatch blocks every cart mutation;
  - marketplace and non-delivery offers cannot be added;
  - ambiguous variants require selection;
  - JSON mode never prompts;
  - remove/decrement-to-zero needs `--confirm`;
  - mutation result is not success until cart re-read agrees;
  - archive serialization drops all forbidden PII fields;
  - fixtures contain no credentials, tokens, addresses, or payment fragments.

Live tests are opt-in and should be split into read-only and cart-mutating sets.
A mutating live test uses a cheap, known Walmart-sold grocery item, records the
starting cart quantity, increments once, verifies, restores the starting state,
and verifies restoration. Cleanup runs in `ensure` and uncertain cleanup is
reported loudly. No live test enters checkout.

## Implementation sequence

1. Repository skeleton, config, formatter, worker protocol, doctor, and tests.
2. Recon tooling and visible login with persistent session.
3. Store read/pin and hard mutation guard.
4. Cart show plus exact item lookup.
5. Search and interactive picker, then verified cart add.
6. Increment/decrement/remove with confirmation and uncertain-outcome handling.
7. Order/receipt sync and local bought-before index.
8. My Items and reorder picker.
9. Linux validation, packaging, CI, fixture hygiene, and public documentation.

A feature is not complete because a selector worked once. It is complete when
its contract is captured, scrubbed, tested, failure modes are distinct, and the
human/JSON output says what was actually verified.
