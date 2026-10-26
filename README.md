# walmart-cli

Build a Walmart grocery delivery cart from the command line.

**This tool never checks out.** It can read and change Walmart's shared online
cart, but there is no command, no flag, and no worker action that submits an
order. Substitutions, the delivery slot, tip, payment, and the final review all
happen in Walmart's app, where they belong.

> **Status: early, and read-only.** Search, item lookup, cart reading, order
> archiving, and the local archive queries work. Nothing here changes a cart
> yet: cart mutation is blocked on live reconnaissance, and checkout is not a
> planned feature at all. See [DESIGN.md](DESIGN.md) for the full
> specification.

## What works today

```text
walmart version
walmart doctor            check the local environment, never the network
walmart credentials       print the commands that store a login
walmart config show|path|init
walmart login             sign in with a visible browser
walmart logout

walmart search QUERY      search Walmart's catalog
walmart item ID_OR_URL    one item, including who actually sells it
walmart cart show         read the shared cart, never change it

walmart order sync        archive orders and in-store receipts locally
walmart order list        list what has been archived
walmart order show ID     one archived order
walmart order search TERM all offline: the archive answers these
```

Flags are not listed here on purpose. `walmart help <command>` prints them, and
that table is compared against the argument parser by a test, so it cannot drift.
This file has no such check, and it was already wrong -- it omitted
`search --marketplace` and `order list --all` while listing flags for two other
commands, which reads as a complete list rather than a summary.

The one flag worth stating in prose, because it is a policy rather than a
reference: **`order sync` archives the last two years by default.** `--all` asks
for everything Walmart still shows, which is a much longer unattended session
against their site.

The `order` reads never touch the network. Once a sync has run, "when did we
last buy oat milk" is answerable on a bad connection. When the archive is
incomplete -- because a sync was throttled or challenged partway through -- every
one of those commands says so, because "we never bought that" and "we have not
looked that far back" are different answers.

## Planned

`store show|pin`, `cart add|increment|decrement|remove|add-from-order`, and
`my-items list|search`. Each is declared in `walmart help` so the CLI never
pretends to do something it cannot yet do.

Cart mutation waits on an observed contract: the update runs in a Web Worker,
so it has not yet been seen. Nothing here will guess at it.

## Testing

```sh
rake            # rubocop, ruff, minitest, pytest, smoke
rake smoke      # the built CLI end to end against a stub worker
rake mutants    # break each guard on purpose, require some suite to fail
```

`script/smoke` runs the real binary with `WALMART_PYTHON` pointed at a stub that
speaks the worker protocol, so the CLI, the transport, the parsers and the
formatters are genuine and only Walmart is not. It reads the bytes that reach
stdout: that every `--json` path emits exactly one document including when the
result is empty, that text mode says something useful, and that exit codes match
the four documented meanings. Several defects reached review because the unit
suite asserts what the code should do and nothing asserted what it printed.

`rake mutants` answers the other question: not "do the tests pass" but "would
anything notice if a guard stopped working". It breaks each entry in
`script/mutants.json` — the redaction denylist, the first-party check, the page
ceiling, the list deciding whether a truncated sync reports success — and fails
if the suite stays green. It refuses to run at all unless the edit actually
changed the file, because an edit that silently matched nothing looks exactly
like a guard that is working.

[docs/TESTING.md](docs/TESTING.md) explains why the suite is shaped this way:
thirteen defects, eleven of them found sitting inside checks that were passing,
and the habits that caught them. Worth reading before adding a test here.

## Safety invariants

These outrank convenience, and they are enforced by tests rather than by good
intentions:

1. No command or worker action places an order. `test/walmart/checkout_surface_test.rb`
   scans every source file and fails the build if the vocabulary of order
   submission appears outside the two audited guard files.
2. Browser automation refuses to navigate to, or click, anything whose purpose
   is order submission (`lib/walmart/safety.rb`, `pyworker/safety.py`).
3. Cart mutations will verify the active store against the pinned store first,
   and a mismatch will be a hard failure.
4. A mutation is not a success until re-reading the cart agrees. Outcomes are
   distinguished: verified (0), rejected (1), unsafe or declined (2), and
   *uncertain* (3).
5. Credentials never appear in argv, logs, config, or exceptions. They travel
   on the worker's stdin and nowhere else.
6. Captured fixtures are scrubbed before they are committed, and a hygiene test
   enforces it.

## Requirements

- Ruby 3.1+
- Python 3.10+ with [uv](https://docs.astral.sh/uv/)
- Google Chrome or Chromium (Playwright's Chromium works as a fallback)
- `fzf` optional; there is a portable numbered picker without it

## Setup

```sh
git clone <this repo> && cd walmart-cli
rake setup                     # installs the Python worker's dependencies
cd pyworker && uv run playwright install chromium && cd -
./bin/walmart doctor
```

### Credentials (optional)

macOS Keychain, service `walmart-cli`:

```sh
security add-generic-password -s walmart-cli -a email -w
security add-generic-password -s walmart-cli -a password -w
```

An item that already exists cannot be added again. `walmart credentials`
prints that command with `-U` when it can see the item, and both forms when
the Keychain will not say.

Both records are optional. Without them, `walmart login` simply opens a visible
browser and you sign in by hand.

Linux uses Secret Service through `secret-tool` (package `libsecret-tools`),
with the same record names:

```sh
secret-tool store --label='walmart-cli email' service walmart-cli account email
secret-tool store --label='walmart-cli password' service walmart-cli account password
```

If `secret-tool` is not installed, Linux falls back to manual login.

Sign-in is gated by human-verification challenges. Observed live: a
press-and-hold challenge on the first page load and another on pressing Sign In,
which a Playwright-driven Chrome fails repeatedly even when a human performs the
hold — it is rejecting the client, not the person.

This is a personal tool for one customer reaching his own account, and it works
around that detection where it must. What it does not do is defined in
[DESIGN.md](DESIGN.md): no other person's account or data, no volume scraping or
sniping or resale, nothing whose value comes from being faster than a human, and
nothing touching payment authorisation. The traffic stays at human rate, and the
tool still never retries a block automatically.

The account holder carries the risk that automated access breaches walmart.com's
Terms of Use, and that the penalty lands on the account he buys groceries with.

### Attaching to a browser you signed into yourself

The approach that needs no forgery: run a Chrome, sign in as yourself, and let
the CLI use that session.

```sh
walmart browser start                        # a Chrome with a debugging port
# sign in to Walmart in that window, by hand, and clear any press-and-hold
walmart config set cdp_url http://127.0.0.1:9222
walmart doctor
```

`browser start` uses a **separate profile**, not your everyday one. Chrome 136+
refuses a debugging port on the default profile, and for good reason: the port
is unauthenticated, so anything that can reach it can drive the browser and read
its cookies. Keeping this profile to one site keeps that blast radius to one
site. `cdp_url` is rejected unless it points at loopback.

While attached, the CLI opens its own tab and closes only that tab. It never
closes your browser and never navigates a tab you were using.

There is no plaintext-config fallback, by design.

**After a challenge, do not re-run the command.** Clear it with `walmart login`
in the visible window first. Re-running a blocked request unattended is how a
soft challenge becomes a hard block, and the tool never retries one for you --
`order list` on a blocked archive says so in as many words. This is a rule about
the prose as much as the code: the CLI must not suggest, to a human, a retry it
would refuse to perform itself.

## Files

```text
$XDG_CONFIG_HOME/walmart/config.json     non-secret settings
$XDG_DATA_HOME/walmart/browser-profile/  dedicated session, mode 0700
$XDG_DATA_HOME/walmart/orders/<year>/    archived orders and receipts
$XDG_CACHE_HOME/walmart/live/            short-lived read cache (never the cart)
$XDG_STATE_HOME/walmart/sync.log
```

The browser profile is dedicated to this tool; your everyday browser profile is
never touched. Only one `walmart` process may hold it at a time, and contention
is reported rather than allowed to corrupt the session.

## Development

```sh
rake            # rubocop + ruff + minitest + pytest
rake test       # Ruby only
rake pytest     # Python only
rake gitleaks   # secret scan
```

No test touches Walmart, the Keychain, or the real browser profile. Ruby tests
run against hermetic XDG directories and injected doubles; Python tests run
against page doubles and scrubbed fixtures.

### Reconnaissance

Selectors and endpoints are not guessed. `scripts/recon.py` records what the
real web app does against read-only surfaces, scrubs as it writes, and produces
a summary of the endpoints it observed. It refuses to run without `--live` and
a typed confirmation, and it never mutates anything.

```sh
scripts/recon.py --list
scripts/recon.py --live search
scripts/promote-fixture.py pyworker/recon/<stamp>/bodies/003-... search_milk.json
```

It needs Playwright, which lives in `pyworker/.venv`. If you start it with a
Python that does not have it, the script re-runs itself under the project venv
rather than failing on an import. Either of these also works and is explicit:

```sh
cd pyworker && uv run python ../scripts/recon.py --live search
pyworker/.venv/bin/python scripts/recon.py --live search
```

Raw captures live in `pyworker/recon/`, which is gitignored.

## License

MIT. See [LICENSE](LICENSE).

Not affiliated with, endorsed by, or supported by Walmart.
