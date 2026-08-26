# walmart-cli design

A public, Linux-compatible command-line client for building a Walmart grocery
delivery cart, browsing Walmart's personalized **My Items** catalog, and keeping
a local purchase-history archive.

This tool deliberately stops before checkout. It can mutate the shared online
cart, but it must never place an order. The user reviews substitutions,
delivery slot, tip, payment, and the final order in Walmart's iPhone app.

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

The formatter prints the verified pinned store name and ZIP above mutations.
JSON includes a structured `store` object rather than relying on prose.

Mutation result states follow the proven amazon-cli pattern:

| Exit | Meaning |
|---:|---|
| 0 | Mutation completed and verified by re-reading the cart |
| 1 | Walmart rejected it or verification showed it did not happen |
| 2 | Usage error, declined prompt, missing `--confirm`, or unsafe/ineligible request |
| 3 | Mutation may have happened but verification could not be completed |

### Store pinning

`store pin` reads Walmart's active grocery store from the authenticated
session and saves only:

```json
{
  "store_id": "...",
  "name": "...",
  "zip": "..."
}
```

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

1. Walmart bot protection and whether headless system Chrome is accepted.
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

Use a dedicated persistent profile under the XDG data directory, not the
user's everyday browser profile. Prefer installed Chrome/Chromium and fall back
to Playwright Chromium where supported. Login is visible. Normal commands are
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
