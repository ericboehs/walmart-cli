# frozen_string_literal: true

require_relative "base"

module Walmart
  module Commands
    # `walmart cart show|add|remove`
    #
    # The cart belongs to a household, not to this tool. Someone else may have
    # added to it a minute ago, so what is printed is a snapshot with a
    # timestamp implied by "now", never a cached view -- and the same fact is
    # why a change is confirmed by reading the cart back rather than by
    # trusting the answer to the write.
    #
    # Quantities are always relative here. The wire protocol sets an absolute
    # number, but exposing that would let a stale count overwrite someone
    # else's edit; see pyworker/cart_mutate.py.
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

      # Outcomes the worker can report, and what each one means to a shell.
      # A cart we could not verify is exit 3 and never exit 0: the difference
      # between "done" and "possibly done" is the whole point of the command.
      EXIT_FOR = {
        "applied" => ExitCodes::OK,
        "rejected" => ExitCodes::FAILED,
        "contract_stale" => ExitCodes::FAILED,
        "unverified" => ExitCodes::UNVERIFIED
      }.freeze

      SUBCOMMANDS = %w[show add remove].freeze

      def run(path, args)
        subcommand = path[1] || "show"
        raise UsageError, "unknown cart subcommand: #{subcommand}" unless SUBCOMMANDS.include?(subcommand)

        return show(args) if subcommand == "show"

        change(subcommand, args)
      end

      private

      def show(args)
        positional, = parse_flags(args, max_positional: 0)
        raise UsageError, "unexpected argument: #{positional.first}" unless positional.empty?

        render(worker.run("cart.show"))
      end

      # `cart add <item> [n]` and `cart remove <item>`.
      #
      # Both are one worker action, because they differ only in the number
      # they ask for. Giving each its own path would be two chances for one of
      # them to skip the confirmation or the verification.
      def change(subcommand, args)
        positional, = parse_flags(args, max_positional: 2)
        item_id = positional[0] or raise UsageError, "which item? give the item id from `walmart cart show`"
        remove = subcommand == "remove"
        delta = remove ? nil : quantity_argument(positional[1])

        confirm!(question_for(subcommand, item_id, delta))
        params = { "item_id" => item_id, "delta" => delta, "remove" => remove }
        report_change(worker.run("cart.#{remove ? "remove" : "add"}", params: params, mutation: true))
      end

      def quantity_argument(raw)
        return 1 if raw.nil?

        value = Integer(raw, exception: false)
        raise UsageError, "how many? #{raw.inspect} is not a whole number" if value.nil?
        raise UsageError, "a change of zero is not a change" if value.zero?

        value
      end

      def question_for(subcommand, item_id, delta)
        return "Remove #{item_id} from the cart?" if subcommand == "remove"
        return "Add #{delta} of #{item_id} to the cart?" if delta.positive?

        "Take #{delta.abs} of #{item_id} out of the cart?"
      end

      # What the person needs to know is what the cart holds now, so that is
      # what is printed first -- not whether the request succeeded, which is a
      # fact about a network call.
      def report_change(data)
        if json?
          fmt.emit_json(data)
          return EXIT_FOR.fetch(data["outcome"], ExitCodes::UNVERIFIED)
        end

        say_change(data)
        EXIT_FOR.fetch(data["outcome"], ExitCodes::UNVERIFIED)
      end

      def say_change(data)
        before = data["quantity_before"]
        after = data["quantity_after"]
        if data["outcome"] == "applied"
          fmt.say("#{data["item_id"]}: #{before} -> #{after}")
        else
          fmt.say("#{data["item_id"]}: the cart shows #{after.nil? ? "an unknown quantity" : after}")
        end
        fmt.hint(data["message"]) unless data["message"].to_s.empty?
      end

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
