# frozen_string_literal: true

module Walmart
  # Last-resort redaction for text we did not author.
  #
  # Worker stderr is the main consumer: a Python traceback can contain a URL, a
  # header, or a chunk of page text, and we surface a tail of it on failure so a
  # crash can explain itself. Surfacing it without this would trade a debugging
  # convenience for a privacy leak.
  #
  # This is a safety net, not a sanitizer. Nothing here excuses logging
  # something sensitive on purpose. The categories mirror pyworker/scrub.py so
  # the two ends of the pipe protect the same things.
  module Redact
    PATTERNS = [
      [/[\w.+-]+@[\w-]+\.[a-z]{2,}/i, "[REDACTED-EMAIL]"],
      [/\b(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b/, "[REDACTED-PHONE]"],
      [/\b(?:\d[ -]?){13,16}\b/, "[REDACTED-CARD]"],
      [%r{\bBearer\s+[A-Za-z0-9._~+/=-]{8,}}i, "Bearer [REDACTED]"],
      [/\beyJ[A-Za-z0-9._-]{20,}/, "[REDACTED-JWT]"],
      # A traceback that quotes an environment dump is the realistic way an
      # access key or a key file reaches the terminal.
      [/\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/, "[REDACTED-AWS-KEY]"],
      [/-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/, "[REDACTED-PRIVATE-KEY]"],
      [/(?<=password)([=:]\s*["']?)[^\s"',}]{3,}/i, "\\1[REDACTED]"],
      [/\b(?:cookie|set-cookie|authorization)\s*[:=]\s*\S+/i, "[REDACTED-HEADER]"]
    ].freeze

    def self.text(value)
      PATTERNS.reduce(value.to_s) { |acc, (pattern, replacement)| acc.gsub(pattern, replacement) }
    end

    # Redacts each line and keeps at most +limit+ of them, from the end.
    def self.tail(lines, limit: 3)
      Array(lines).last(limit).map { |line| text(line) }
    end
  end
end
