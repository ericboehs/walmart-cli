# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # Safety invariant 9: nothing enters the repository as a fixture until it has
  # been scrubbed. This test is the gate. It runs even while the fixture
  # directories are empty so it cannot be quietly forgotten later.
  class FixtureHygieneTest < TestCase
    FIXTURE_GLOBS = [
      "pyworker/fixtures/**/*",
      "test/fixtures/**/*"
    ].freeze

    # Patterns that must never appear in a committed fixture, each paired with
    # the regex used to detect it. scripts/promote-fixture.py mirrors this list
    # label for label so a fixture is checked before it is even written.
    FORBIDDEN_PATTERNS = {
      "email address" => /[\w.+-]+@[\w-]+\.[a-z]{2,}/i,
      "US phone number" => /\b(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b/,
      "payment card digits" => /\b(?:\d[ -]?){13,16}\b/,
      "card last four" => /\bending\s+in\s+\d{4}\b/i,
      "street address" => /
        \b\d{1,6}\s+[A-Z][a-z]+\s+
        (?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Ct|Court|Way|Hwy)\b
      /x,
      "authorization header" => /authorization\s*[:=]\s*["']?(?:bearer|basic)\s/i,
      "cookie header" => /\bset-cookie\s*:/i,
      "session token" => /\b(?:auth[_-]?token|access[_-]?token|refresh[_-]?token|session[_-]?id)\b\s*[:=]/i,
      "AWS key" => /\bAKIA[0-9A-Z]{16}\b/,
      "private key block" => /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/
    }.freeze

    # Placeholder values fixtures are expected to use instead of real data.
    ALLOWED_PLACEHOLDERS = %w[
      redacted@example.com
      user@example.com
      555-555-5555
      [REDACTED]
    ].freeze

    def test_committed_fixtures_are_scrubbed
      offenders = []

      fixture_files.each do |file|
        content = scrubbed_of_placeholders(File.read(file))
        FORBIDDEN_PATTERNS.each do |label, pattern|
          next unless content.match?(pattern)

          offenders << "#{file.delete_prefix("#{ROOT}/")}: contains a #{label}"
        end
      end

      assert_empty offenders, <<~MSG
        Fixtures must be scrubbed before they are committed:
        #{offenders.join("\n")}
      MSG
    end

    def test_recon_captures_are_not_committed
      tracked = `cd #{ROOT} && git ls-files pyworker/recon recon 2>/dev/null`.split("\n")

      assert_empty tracked,
                   "raw recon captures must stay untracked; they are unscrubbed by definition"
    end

    def test_the_scanner_actually_catches_a_planted_secret
      # A hygiene test that cannot fail is not a hygiene test.
      samples = {
        "email address" => "contact eric.real.person@gmail.com for details",
        "US phone number" => "call 918-555-0134 now",
        "street address" => "ship to 1234 Country Road",
        "cookie header" => "Set-Cookie: CID=abc123"
      }

      samples.each do |label, text|
        assert FORBIDDEN_PATTERNS.fetch(label).match?(text),
               "the #{label} pattern failed to match #{text.inspect}"
      end
    end

    def test_placeholders_are_allowed
      text = "email: redacted@example.com phone: 555-555-5555"
      cleaned = scrubbed_of_placeholders(text)

      refute cleaned.match?(FORBIDDEN_PATTERNS.fetch("email address"))
      refute cleaned.match?(FORBIDDEN_PATTERNS.fetch("US phone number"))
    end

    # scripts/promote-fixture.py checks a fixture *before* it is written, so a
    # gap there means a secret lands in the working tree and is only caught
    # later, if the test is run at all. Same labels, both sides.
    def test_the_promotion_script_checks_the_same_categories
      source = File.read(File.join(ROOT, "scripts/promote-fixture.py"))
      body = source[/^HYGIENE_PATTERNS = \{(.*?)^\}/m, 1]

      refute_nil body, "could not find HYGIENE_PATTERNS in scripts/promote-fixture.py"
      assert_equal FORBIDDEN_PATTERNS.keys.sort, body.scan(/^\s*"([^"]+)":/).flatten.sort,
                   "promote-fixture.py and this test check different categories"
    end

    private

    def fixture_files
      FIXTURE_GLOBS.flat_map { |glob| Dir.glob(File.join(ROOT, glob)) }
                   .select { |path| File.file?(path) && !File.basename(path).start_with?(".") }
    end

    def scrubbed_of_placeholders(content)
      ALLOWED_PLACEHOLDERS.reduce(content) { |text, placeholder| text.gsub(placeholder, "") }
    end
  end
end
