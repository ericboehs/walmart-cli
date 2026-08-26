#!/usr/bin/env python3
"""Promote a recon capture into a committed fixture.

A fixture is a claim that "Walmart's response looks like this". Before that
claim can enter the repository it must be scrubbed and it must survive the same
patterns the Ruby hygiene test enforces. This script does both, and refuses to
write a file that would fail the gate.

Usage:
    scripts/promote-fixture.py pyworker/recon/<stamp>/bodies/003-...json search_milk.json
    scripts/promote-fixture.py --scrub-literal "Some Name" <src> <name>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pyworker"))

import scrub  # noqa: E402

FIXTURES = REPO_ROOT / "pyworker" / "fixtures"

# Mirrors test/walmart/fixture_hygiene_test.rb, label for label. A Ruby test
# asserts the two label sets are identical; if they ever disagree, the Ruby
# gate wins and this script is wrong.
HYGIENE_PATTERNS = {
    "email address": re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.IGNORECASE),
    "US phone number": re.compile(r"\b(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"),
    "payment card digits": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "card last four": re.compile(r"\bending\s+in\s+\d{4}\b", re.IGNORECASE),
    "street address": re.compile(
        r"\b\d{1,6}\s+[A-Z][a-z]+\s+"
        r"(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Ct|Court|Way|Hwy)\b"
    ),
    "authorization header": re.compile(r"authorization\s*[:=]\s*[\"']?(?:bearer|basic)\s", re.IGNORECASE),
    "cookie header": re.compile(r"\bset-cookie\s*:", re.IGNORECASE),
    "session token": re.compile(
        r"\b(?:auth[_-]?token|access[_-]?token|refresh[_-]?token|session[_-]?id)\b\s*[:=]",
        re.IGNORECASE,
    ),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}

PLACEHOLDERS = (scrub.EMAIL_PLACEHOLDER, scrub.PHONE_PLACEHOLDER, scrub.REDACTED)


def findings(text: str) -> list[str]:
    for placeholder in PLACEHOLDERS:
        text = text.replace(placeholder, "")
    return [label for label, pattern in HYGIENE_PATTERNS.items() if pattern.search(text)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", help="captured body under pyworker/recon/")
    parser.add_argument("name", help="fixture filename, e.g. search_whole_milk.json")
    parser.add_argument("--scrub-literal", action="append", help="extra literal to redact (repeatable)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing fixture")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_file():
        print(f"no such capture: {source}", file=sys.stderr)
        return 2

    destination = FIXTURES / args.name
    if destination.exists() and not args.force:
        print(f"{destination} exists; pass --force to replace it", file=sys.stderr)
        return 2

    literals = [value for value in (args.scrub_literal or []) if value.strip()]
    cleaned = scrub.scrub_document(source.read_text(), literals)

    problems = findings(cleaned)
    if problems:
        print("refusing to promote; scrubbing did not remove:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("add --scrub-literal for values the generic rules cannot know about", file=sys.stderr)
        return 1

    FIXTURES.mkdir(parents=True, exist_ok=True)
    destination.write_text(cleaned if cleaned.endswith("\n") else cleaned + "\n")
    print(f"wrote {destination.relative_to(REPO_ROOT)}")
    print("next: rake test  (the hygiene gate runs there too)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
