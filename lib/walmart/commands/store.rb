# frozen_string_literal: true

require_relative "base"

module Walmart
  module Commands
    # `walmart store show|pin`
    #
    # The pinned store is what the cart mutation path checks itself against:
    # a change is only safe if the cart it is changing belongs to the store the
    # user meant. Until this existed, that guard had nothing to compare with,
    # so it refused every request -- correct, and useless.
    #
    # What gets saved is narrower than DESIGN.md originally described. The
    # design asks for store_id, name and zip. Only storeId has ever been
    # observed in a Walmart response -- in the cart's fulfillment node, which
    # is where this reads it. A name and a ZIP would have to come from a field
    # nobody here has seen, and inventing the mapping is the failure this
    # project keeps writing tests against. They are absent rather than guessed,
    # and the absence is visible in `store show`.
    class Store < Base
      SUBCOMMANDS = %w[show pin].freeze

      def run(path, args)
        subcommand = path[1] || "show"
        raise UsageError, "unknown store subcommand: #{subcommand}" unless SUBCOMMANDS.include?(subcommand)

        positional, = parse_flags(args, max_positional: 0)
        raise UsageError, "unexpected argument: #{positional.first}" unless positional.empty?

        subcommand == "pin" ? pin : show
      end

      private

      # Reads the live cart, because that is the only place the active store
      # has been observed. A read that fails is reported as a read that
      # failed: an unreadable cart is not a cart at store nil, and pinning
      # nothing would silently unpin whatever was there.
      def pin
        active = active_store_id(worker.run("cart.show"))
        raise Error, "Walmart's cart did not name a store, so there is nothing to pin" if active.nil?

        config.store = { "store_id" => active }
        config.save
        report(active, pinned: active)
      end

      def show
        pinned = config.store&.dig("store_id")
        return report(nil, pinned: pinned) if json?

        if pinned.to_s.empty?
          fmt.say "no store pinned - run: walmart store pin"
          return ExitCodes::OK
        end

        fmt.say "pinned store: #{pinned}"
        fmt.say "name and zip: not stored (never observed in a Walmart response)"
        ExitCodes::OK
      end

      def active_store_id(data)
        raise Error, error_message(data) if data.is_a?(Hash) && data["error"]

        id = data.is_a?(Hash) ? data.dig("fulfillment", "store_id") : nil
        id.to_s.empty? ? nil : id.to_s
      end

      def error_message(data)
        message = data.dig("error", "message")
        message.to_s.empty? ? "could not read the cart, so the active store is unknown" : message
      end

      def report(active, pinned:)
        if json?
          fmt.emit_json({ "pinned" => pinned, "active" => active,
                          "name" => nil, "zip" => nil })
        else
          fmt.say "pinned store: #{pinned}"
        end
        ExitCodes::OK
      end
    end
  end
end
