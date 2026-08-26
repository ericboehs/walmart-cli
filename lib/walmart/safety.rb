# frozen_string_literal: true

module Walmart
  # The single Ruby file permitted to name checkout-related concepts.
  #
  # Safety invariant 1 of DESIGN.md: this CLI has no checkout or place-order
  # command, and no hidden worker action for either. The source hygiene test
  # scans every other Ruby and Python source file in lib/, pyworker/, bin/, and
  # scripts/ for the terms listed here and fails the build if one appears.
  # Concentrating the vocabulary in one audited file is what makes that scan
  # possible.
  #
  # Nothing in this module performs a checkout. It exists to recognize and
  # refuse one.
  module Safety
    # Substrings that must not appear anywhere else in lib/, pyworker/, bin/, or
    # scripts/. Case-insensitive; compared against source text with separators
    # removed so `place_order`, `place-order`, and `placeOrder` all match.
    # pyworker/safety.py carries an identical list, and a test compares them.
    FORBIDDEN_TERMS = %w[
      checkout
      placeorder
      submitorder
      purchaseorder
      buynow
      onestepcheckout
      startcheckout
      completeorder
      payorder
    ].freeze

    # URL path fragments a navigation guard must refuse.
    FORBIDDEN_URL_FRAGMENTS = %w[
      /checkout
      /cart/checkout
      /place-order
      /placeorder
      /buynow
      /payment
      /wallet
      /order-confirmation
    ].freeze

    # Normalizes source text so separator style cannot smuggle a term past the
    # scan. Keeps only lowercase alphanumerics.
    def self.normalize(text)
      text.downcase.gsub(/[^a-z0-9]/, "")
    end

    # Returns the forbidden terms present in +text+.
    def self.violations(text)
      haystack = normalize(text)
      FORBIDDEN_TERMS.select { |term| haystack.include?(term) }
    end

    # Percent-decodes repeatedly, so an encoded destination is compared as the
    # browser would resolve it. Mirrors safety.py's decode_url; see the note
    # there for why one pass is not enough.
    MAX_DECODE_PASSES = 5

    def self.decode_url(url)
      text = url.to_s
      MAX_DECODE_PASSES.times do
        decoded = text.gsub(/%[0-9A-Fa-f]{2}/) { |escape| escape[1..].to_i(16).chr }
        break if decoded == text

        text = decoded
      end
      text.tr("\\", "/")
    end

    # True when +url+ points anywhere this CLI must never go.
    #
    # Checks the raw and the decoded forms. Over-blocking costs a refused
    # navigation; under-blocking costs an order nobody authorised.
    #
    # No host allowlist here, unlike safety.py: the Ruby side never navigates,
    # and a guard with no caller is untested surface that ages. Navigation is
    # gated in the worker, where navigation happens.
    def self.forbidden_url?(url)
      return false if url.nil?

      [url.to_s.downcase, decode_url(url).downcase].any? do |candidate|
        FORBIDDEN_URL_FRAGMENTS.any? { |fragment| candidate.include?(fragment) }
      end
    end
  end
end
