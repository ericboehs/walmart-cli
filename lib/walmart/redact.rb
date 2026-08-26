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

    # Field names that must never reach the archive, mirroring DROP_KEYS in
    # pyworker/orders_parse.py and the required set in docs/CONTRACTS.md.
    #
    # Deliberately a second, independent copy rather than a value imported from
    # the worker. The worker's list is what does the removing; this is what
    # notices if the removing stopped happening. A shared constant would fail
    # both at once, which is the case this exists to survive -- a parser bug, a
    # shortened denylist, or a response shape we have not seen.
    #
    # Normalized the way Python normalizes, so delivery_address,
    # DeliveryAddress and deliveryAddress are one name.
    FORBIDDEN_KEYS = %w[
      paymentMethods refundPaymentMethods chargeHistory authorizationAmount
      monthlyPayment orderInvoiceDetails digitalInvoiceDetail idBarcodeImageUrl
      customer driver shopper pickedBy alternatePickupPerson
      alternateGroupRecipients callDriverDetails checkInDetails vehicleInfo
      tippingPaymentMethod deliveryAddress deliveryInstructions
      deliveryPreferences accessPointId address postalCode phone phoneNumber
      email
    ].map { |key| key.downcase.gsub(/[^a-z0-9]/, "") }.freeze

    def self.normalize_key(key)
      key.to_s.downcase.gsub(/[^a-z0-9]/, "")
    end

    # Every forbidden field name present anywhere in +data+.
    #
    # Names only, never values. This feeds an error message, and an error that
    # quoted the card number it had just caught would be the leak it prevented.
    def self.forbidden_keys(data, found = [])
      case data
      when Hash
        data.each do |key, value|
          found << key.to_s if FORBIDDEN_KEYS.include?(normalize_key(key))
          forbidden_keys(value, found)
        end
      when Array then data.each { |entry| forbidden_keys(entry, found) }
      end
      found.uniq
    end
  end
end
