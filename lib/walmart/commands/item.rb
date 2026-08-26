# frozen_string_literal: true

require_relative "base"

module Walmart
  module Commands
    # `walmart item ID_OR_URL`
    #
    # One item, read live. This is also the command that answers the question a
    # search result cannot: whether Walmart itself sells the offer. sellerType
    # is populated only on the item page, and DESIGN.md requires that proof
    # before anything is added to a cart.
    class Item < Base
      FIELDS = [
        ["item", :item_id],
        ["name", :name],
        ["brand", :brand],
        ["price", :price],
        ["was", :was_price],
        ["unit", :unit_price_display],
        ["seller", :seller],
        ["stock", :availability],
        ["delivery", :delivery],
        ["limits", :limits],
        ["snap", :snap]
      ].freeze

      def run(_path, args)
        positional, = parse_flags(args, max_positional: 1)
        raise UsageError, "walmart item needs an item id or URL" if positional.empty?

        render(worker.run("item", params: { "item" => positional.first }))
      end

      private

      def render(data)
        # Not `emit_json(data) && ExitCodes::OK`: emit_json returns nil, so
        # that idiom returned nil, and nil reached Kernel#exit as a TypeError
        # after the document had already been printed.
        if json?
          fmt.emit_json(data)
          return ExitCodes::OK
        end

        rows = FIELDS.filter_map do |label, key|
          value = send(key, data)
          { field: label, value: value } unless value.nil?
        end
        fmt.table(rows, [[:field, "", :left], [:value, "", :left]])
        warn_about_variants(data)
        ExitCodes::OK
      end

      def item_id(data) = data["item_id"]
      def name(data) = data["name"]
      def brand(data) = data["brand"]
      def was_price(data) = money(data["was_price"])
      def unit_price_display(data) = data["unit_price_display"]
      def availability(data) = data["availability"]
      def price(data) = data["price_display"] || money(data["price"])

      # The seller line is the first-party answer, so it says which it is
      # rather than making the reader infer it from a name.
      def seller(data)
        name = data["seller_name"] || "unknown"
        data["first_party"] ? "#{name} (Walmart)" : "#{name} (marketplace)"
      end

      def delivery(data)
        options = Array(data["fulfillment_options"]).select { |option| option["available"] }
        return "no" if options.empty?

        options.filter_map { |option| option["label"] || option["type"] }.join(", ")
      end

      # Only shown when Walmart actually stated a limit. A blank line is better
      # than "1-unknown", which reads like a fact.
      def limits(data)
        low = data["min_quantity"]
        high = data["max_quantity"]
        return nil if low.nil? && high.nil?

        [low, high].compact.map { |value| format_quantity(value) }.join("-")
      end

      def snap(data)
        return nil if data["snap_eligible"].nil?

        data["snap_eligible"] ? "eligible" : "not eligible"
      end

      # We have never seen a populated variant family, so we do not pretend to
      # read one. Saying so is what lets the user go look rather than trusting
      # a listing that might be for the wrong size.
      def warn_about_variants(data)
        variants = data["variants"] || {}
        return unless variants["shape_unobserved"]

        fmt.hint("this item has #{variants["count"]} variants that this tool cannot read yet; " \
                 "check the size or count on walmart.com before ordering")
      end

      # Walmart sends 12 and 1.0 for the same kind of fact. Printing "1.0 per
      # order" reads like a weight rather than a count.
      def format_quantity(value)
        whole = value.to_i
        (value.to_f - whole).abs < Float::EPSILON ? whole.to_s : value.to_s
      end

      def money(value)
        return nil if value.nil?

        format("$%.2f", value)
      end
    end
  end
end
