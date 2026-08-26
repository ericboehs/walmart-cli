# walmart-cli

Build a Walmart grocery delivery cart from the command line.

**This tool never checks out.** It can read and change Walmart's shared online
cart, but there is no command, no flag, and no worker action that submits an
order. Substitutions, the delivery slot, tip, payment, and the final review all
happen in Walmart's app, where they belong.

> **Status: early.** The foundation, the safety gates, the worker protocol, and
> the visible login path exist. Everything that reads or changes a cart is
> blocked on live reconnaissance and is listed as *planned* in `walmart help`.
> See [DESIGN.md](DESIGN.md) for the full specification.

## What works today

```text
walmart version
walmart doctor                 check the local environment, never the network
walmart config show|path|init
walmart login [--manual] [--force] [--wait SECONDS]
walmart logout
```

## Planned

`store show|pin`, `search`, `item`, `cart show|add|increment|decrement|remove|
add-from-order`, `my-items list|search`, and `order sync|list|show|search`.
Each is declared in `walmart help` so the CLI never pretends to do something it
cannot yet do.

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

Both records are optional. Without them, `walmart login` simply opens a visible
browser and you sign in by hand. On Linux, manual login is the only supported
path today; the `Secrets` boundary is shaped so a libsecret provider can be
added without touching any command.

Sign-in is gated by human-verification challenges. Observed live: a
press-and-hold challenge on the first page load and another on pressing Sign In.
**You clear those, in the visible window.** This tool never solves, simulates,
or evades a challenge — no press-and-hold automation, no captcha services, no
stealth or fingerprint-spoofing plugins. When one appears, the CLI names it and
waits. A one-time code by text may also appear; you type that in the window too.

There is no plaintext-config fallback, by design.

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
