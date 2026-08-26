# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    # Reading the shared cart. The worker results here are shaped from the
    # observed getCart response.
    class CartTest < TestCase
      MILK = {
        "item_id" => "10450114",
        "name" => "Great Value Whole Milk, 1 Gallon",
        "quantity" => 2,
        "quantity_label" => "2 ct",
        "line_price" => 6.24,
        "item_price" => 3.12,
        "unit_price_display" => "2.4 ¢/fl oz",
        "seller_name" => "Walmart",
        "fulfillment_label" => "Delivery"
      }.freeze

      BANANAS = {
        "item_id" => "44390948",
        "name" => "Fresh Banana Fruit, each",
        "quantity" => 2.5,
        "quantity_label" => "2.5 lb",
        "line_price" => 1.45,
        "item_price" => 0.58,
        "seller_name" => "Walmart",
        "fulfillment_label" => "Delivery"
      }.freeze

      TOTALS = {
        "subtotal" => 7.69, "subtotal_display" => "$7.69",
        "total" => 8.34, "total_display" => "$8.34",
        "savings" => 0.72, "savings_display" => "$0.72"
      }.freeze

      def test_lines_are_listed_with_quantities_and_prices
        run_cart(lines: [MILK, BANANAS])

        assert_includes out.string, "Great Value Whole Milk"
        assert_includes out.string, "$6.24"
        assert_includes out.string, "Delivery"
      end

      # Walmart renders "2.5 lb" itself. Rebuilding it from the number and the
      # unit is how a display ends up saying "2.5 EACH".
      def test_walmarts_own_quantity_label_is_shown
        run_cart(lines: [BANANAS])

        assert_includes out.string, "2.5 lb"
      end

      def test_totals_are_printed_beneath_the_lines
        run_cart(lines: [MILK], totals: TOTALS)

        assert_includes out.string, "subtotal"
        assert_includes out.string, "$8.34"
      end

      # A total we computed is not the total Walmart will charge. Better to
      # print no total than a confident wrong one.
      def test_a_missing_total_is_omitted_rather_than_calculated
        run_cart(lines: [MILK, BANANAS], totals: { "subtotal" => 7.69, "subtotal_display" => "$7.69" })

        assert_includes out.string, "$7.69"
        refute_includes out.string, "total     $"
      end

      def test_an_empty_cart_says_so
        code = run_cart(lines: [])

        assert_equal ExitCodes::OK, code
        assert_includes out.string + err.string, "cart"
      end

      # A sold-out line still renders. Staying silent would make a broken cart
      # look fine until delivery day.
      def test_walmarts_complaints_are_shown
        run_cart(lines: [MILK], problems: ["Sold out: Great Value Whole Milk"])

        assert_includes err.string, "Sold out"
      end

      def test_json_mode_returns_an_exit_code_and_not_nil
        assert_equal ExitCodes::OK, run_cart(lines: [MILK], totals: TOTALS, json: true)
      end

      def test_json_output_carries_the_whole_cart
        run_cart(lines: [MILK], totals: TOTALS, json: true)

        ids = stdout_json["lines"].map { |line| line["item_id"] }

        assert_equal ["10450114"], ids
      end

      def test_the_worker_is_asked_for_a_read_not_a_change
        worker = FakeWorker.new(results: { "cart.show" => { "lines" => [], "totals" => {} } })
        run_cart(worker: worker)

        actions = worker.calls.map { |call| call[:action] }

        assert_equal ["cart.show"], actions
      end

      # An unreadable cart is not an empty one, and the distinction has to
      # survive all the way to the exit code.
      def test_an_unreadable_cart_is_an_error_not_an_empty_list
        worker = FakeWorker.new(
          errors: { "cart.show" => WorkerError.new("the cart came back without a readable list of items",
                                                   code: "cart_unreadable") }
        )

        error = assert_raises(WorkerError) { run_cart(worker: worker) }
        assert_includes error.message, "readable"
      end

      def test_a_bot_block_asks_for_a_login
        worker = FakeWorker.new(
          errors: { "cart.show" => WorkerError.new("bot protection blocked this request",
                                                   code: "challenge_detected") }
        )

        error = assert_raises(WorkerError) { run_cart(worker: worker) }
        assert_predicate error, :login_required?
      end

      def test_an_unknown_subcommand_is_a_usage_error
        assert_raises(UsageError) { run_cart(path: %w[cart empty]) }
      end

      def test_extra_arguments_are_refused
        assert_raises(UsageError) { run_cart(args: ["milk"]) }
      end

      private

      def run_cart(worker: nil, lines: [], totals: {}, problems: [], json: false,
                   path: %w[cart show], args: [])
        worker ||= FakeWorker.new(
          results: {
            "cart.show" => { "lines" => lines, "totals" => totals, "problems" => problems }
          }
        )
        context = build_context(worker: worker, json: json)
        Cart.new(context).run(path, args)
      end
    end
  end
end
