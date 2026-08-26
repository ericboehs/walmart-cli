# fixtures

Scrubbed captures of real Walmart responses, used by the parser tests.

Rules:

1. Nothing lands here by hand. Capture with `scripts/recon.py`, then promote
   with `scripts/promote-fixture.py`, which scrubs and then refuses to write a
   file that would fail the hygiene gate.
2. `test/walmart/fixture_hygiene_test.rb` scans every file in this directory on
   every test run. Email addresses, phone numbers, street addresses, payment
   fragments, cookies, and tokens are build failures, not review comments.
3. Placeholders are `redacted@example.com`, `555-555-5555`, and `[REDACTED]`.
4. A fixture should be the smallest response that still proves the parser
   handles the real shape, including the awkward cases: weighted items,
   marketplace sellers, unavailable lines, and empty results.
5. Raw captures live in `pyworker/recon/`, which is gitignored. They are
   unscrubbed by definition and must never be committed.
