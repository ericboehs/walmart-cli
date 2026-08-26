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
        [:seller, "seller", :left],
        [:name, "item", :left]
      ].freeze

      # In the order a receipt reads. fees and taxes were parsed and then
      # dropped by the renderer, so a cart whose total included them showed a
      # subtotal, a total, and no account of the difference -- while --json
      # carried the rows all along. A renderer that silently discards parsed
      # data is worse than one that never parsed it, because the gap looks
      # like Walmart's.
      #
      # Tax is not in this list. It sits below the unattributed line, because
      # tax is charged on the surcharges above it -- and the known example of
      # such a surcharge is the below-order-minimum fee on a cart under $35.
      # Printing tax first would imply it was computed on a smaller base.
      BEFORE_TAX_ROWS = %w[subtotal savings fees].freeze

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
          seller: line["seller_name"],
          name: line["name"]
        }
      end

      # Totals come from Walmart or not at all. Adding the lines up ourselves
      # would produce a confident number that is wrong about tax and fees, and
      # a wrong total is worse than a missing one.
      def report_totals(totals)
        parts = BEFORE_TAX_ROWS.map { |name| [name, amount_of(totals, name)] }
        parts << unexplained_row(totals)
        parts << ["taxes", amount_of(totals, "taxes")]
        parts << ["total", amount_of(totals, "total")]
        parts = parts.compact.select(&:last)
        return if parts.empty?

        fmt.say("")
        parts.each { |label, value| fmt.say(format("%-11<label>s %<value>s", label: label, value: value)) }
        note_unexplained(totals)
      end

      def amount_of(totals, name) = totals["#{name}_display"] || money(totals[name])

      # Deliberately not called a fee, a tax, or "other charges". It is a
      # subtraction, and naming it would be inventing a receipt line we have
      # never seen Walmart send.
      def unexplained_row(totals)
        value = totals["unexplained"]
        return nil if value.nil?

        ["unexplained", money(value)]
      end

      def note_unexplained(totals)
        return if totals["unexplained"].nil?

        fmt.hint("#{money(totals["unexplained"])} of the total is not accounted for by the rows " \
                 "Walmart named. This tool will not guess what it covers.")
      end

      # Walmart's own complaints about the cart. A sold-out line still renders,
      # and staying silent about it would make a broken cart look fine until
      # delivery day.
      def report_problems(data)
        Array(data["problems"]).each { |problem| fmt.hint(problem) }
      end

      def money(value)
        return nil if value.nil?
        # A credit reads as -$2.00, never as $-2.00.
        return format("-$%.2f", value.abs) if value.negative?

        format("$%.2f", value)
      end
    end
  end
end
