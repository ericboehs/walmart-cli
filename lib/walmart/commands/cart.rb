# frozen_string_literal: true

require_relative "base"

module Walmart
  module Commands
    # `walmart cart show`
    #
    # Reads the live shared cart. Read-only: nothing here changes a quantity,
    # and the command deliberately has no path that could.
    #
    # The cart belongs to a household, not to this tool. Someone else may have
    # added to it a minute ago, so what is printed is a snapshot with a
    # timestamp implied by "now", never a cached view.
    class Cart < Base
      COLUMNS = [
        [:quantity, "qty", :right],
        [:line_price, "line", :right],
        [:each, "each", :right],
        [:fulfillment, "how", :left],
        [:seller, "seller", :left],
        [:name, "item", :left]
      ].freeze

      def run(path, args)
        subcommand = path[1] || "show"
        raise UsageError, "unknown cart subcommand: #{subcommand}" unless subcommand == "show"

        positional, = parse_flags(args, max_positional: 0)
        raise UsageError, "unexpected argument: #{positional.first}" unless positional.empty?

        render(worker.run("cart.show"))
      end

      private

      def render(data)
        lines = Array(data["lines"])
        # Not `emit_json(data) && ExitCodes::OK`: emit_json returns nil, so
        # that idiom returned nil, and nil reached Kernel#exit as a TypeError
        # after the document had already been printed.
        if json?
          fmt.emit_json(data)
          return ExitCodes::OK
        end

        if lines.empty?
          fmt.empty_result(what: "your cart")
        else
          fmt.table(lines.map { |line| row(line) }, COLUMNS)
          report_totals(data["totals"] || {})
        end

        report_problems(data)
        ExitCodes::OK
      end

      def row(line)
        {
          quantity: line["quantity_label"] || line["quantity"],
          line_price: money(line["line_price"]),
          each: money(line["item_price"]),
          fulfillment: line["fulfillment_label"] || line["fulfillment_type"],
          seller: line["seller_name"],
          name: line["name"]
        }
      end

      # Totals come from Walmart or not at all. Adding the lines up ourselves
      # would produce a confident number that is wrong about tax and fees, and
      # a wrong total is worse than a missing one.
      def report_totals(totals)
        parts = [
          ["subtotal", totals["subtotal_display"] || money(totals["subtotal"])],
          ["savings", totals["savings_display"] || money(totals["savings"])],
          ["total", totals["total_display"] || money(totals["total"])]
        ].select(&:last)
        return if parts.empty?

        fmt.say("")
        parts.each { |label, value| fmt.say(format("%-9<label>s %<value>s", label: label, value: value)) }
      end

      # Walmart's own complaints about the cart. A sold-out line still renders,
      # and staying silent about it would make a broken cart look fine until
      # delivery day.
      def report_problems(data)
        Array(data["problems"]).each { |problem| fmt.hint(problem) }
      end

      def money(value)
        return nil if value.nil?

        format("$%.2f", value)
      end
    end
  end
end
