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
      end

      # Dropped from the table on request: it was the widest column and it
      # carried the least. The parsed values are still in --json, so nothing
      # that wants them has lost them.
      def test_the_fulfillment_column_is_gone
        run_cart(lines: [MILK])

        refute_match(/\bhow\b/, out.string)
        refute_match(/Delivery/, out.string)
      end

      def test_json_still_carries_what_the_table_stopped_showing
        run_cart(lines: [MILK], json: true)

        assert_equal "Delivery", JSON.parse(out.string)["lines"].first["fulfillment_label"]
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

      # --- what Eric's real cart showed ------------------------------------------------

      # Parsed, then thrown away by the renderer: --json carried fees and taxes
      # while the human output showed a subtotal, a total, and no account of
      # the difference between them.
      def test_fees_and_taxes_are_shown_not_only_serialised
        run_cart(lines: [MILK], totals: TOTALS.merge(
          "fees" => 6.99, "fees_display" => "$6.99",
          "taxes" => 1.10, "taxes_display" => "$1.10"
        ))

        assert_match(/fees\s+\$6\.99/, out.string)
        assert_match(/taxes\s+\$1\.10/, out.string)
      end

      def test_the_rows_read_in_receipt_order
        run_cart(lines: [MILK], totals: TOTALS.merge("fees" => 6.99, "taxes" => 1.10))
        order = out.string.scan(/^(subtotal|savings|fees|taxes|total)\b/).flatten

        assert_equal %w[subtotal savings fees taxes total], order
      end

      # Tax is charged on the surcharges above it -- the known example being
      # the below-order-minimum fee on a cart under $35 -- so it reads after
      # them. Printing it first implies a smaller base than was actually taxed.
      def test_tax_is_charged_on_top_of_the_unattributed_amount
        run_cart(lines: [MILK], totals: { "subtotal" => 13.56, "unexplained" => 6.99,
                                          "taxes" => 1.10, "total" => 21.65 })
        order = out.string.scan(/^(subtotal|unexplained|taxes|total)\b/).flatten

        assert_equal %w[subtotal unexplained taxes total], order
      end

      # $13.56 of items against a $20.55 total, and nothing said where the rest
      # went.
      def test_an_unexplained_difference_is_named_as_unexplained
        run_cart(lines: [MILK], totals: { "subtotal" => 13.56, "total" => 20.55,
                                          "unexplained" => 6.99 })

        assert_match(/unexplained\s+\$6\.99/, out.string)
        assert_match(/not accounted for by the rows Walmart named/, err.string)
      end

      # It is a subtraction, not an explanation.
      def test_the_difference_is_never_given_a_name_we_invented
        run_cart(lines: [MILK], totals: { "subtotal" => 13.56, "total" => 20.55,
                                          "unexplained" => 6.99 })

        refute_match(/other charges|delivery fee|estimated tax/i, out.string + err.string)
      end

      def test_a_fully_explained_cart_says_nothing_about_a_difference
        run_cart(lines: [MILK], totals: TOTALS)

        refute_match(/unexplained/, out.string + err.string)
      end

      def test_a_credit_reads_as_a_credit
        run_cart(lines: [MILK], totals: { "subtotal" => 20.00, "total" => 18.00,
                                          "unexplained" => -2.0 })

        assert_match(/unexplained\s+-\$2\.00/, out.string)
      end

      # --- changing the cart ---------------------------------------------------
      #
      # The worker decides the outcome; this side decides what the outcome
      # means to a shell and to a person. Every outcome is asserted for both.

      CHANGED = {
        "item_id" => "10450114", "outcome" => "applied",
        "quantity_before" => 1, "quantity_after" => 2,
        "quantity_intended" => 2, "changed" => true, "message" => ""
      }.freeze

      def test_a_confirmed_change_reports_the_new_quantity
        code = run_change(CHANGED)
        assert_equal ExitCodes::OK, code
        assert_includes out.string, "10450114: 1 -> 2"
      end

      def test_an_unverified_change_is_exit_three_not_zero
        code = run_change(CHANGED.merge("outcome" => "unverified",
                                        "message" => "could not confirm"))
        assert_equal ExitCodes::UNVERIFIED, code
        assert_includes err.string, "could not confirm"
      end

      def test_a_rejected_change_is_a_clean_failure
        code = run_change(CHANGED.merge("outcome" => "rejected", "message" => "out of stock"))
        assert_equal ExitCodes::FAILED, code
      end

      def test_a_stale_contract_is_a_failure_not_an_unknown
        code = run_change(CHANGED.merge("outcome" => "contract_stale",
                                        "message" => "update walmart-cli"))
        assert_equal ExitCodes::FAILED, code
        assert_includes err.string, "update walmart-cli"
      end

      # An outcome this version has never heard of must not read as success.
      def test_an_unknown_outcome_is_unverified
        assert_equal ExitCodes::UNVERIFIED, run_change(CHANGED.merge("outcome" => "who knows"))
      end

      def test_no_outcome_at_all_is_unverified
        assert_equal ExitCodes::UNVERIFIED, run_change(CHANGED.except("outcome"))
      end

      def test_a_change_asks_before_it_acts
        worker = FakeWorker.new(results: { "cart.add" => CHANGED })
        context = build_context(worker: worker)
        error = assert_raises(UsageError) { Cart.new(context).run(%w[cart add], %w[10450114]) }
        assert_match(/--confirm/, error.message)
        assert_empty worker.calls
      end

      def test_a_declined_change_sends_nothing
        worker = FakeWorker.new(results: { "cart.add" => CHANGED })
        # A TTY on both ends, so the interactive path actually runs and the
        # answer is a real "no" rather than a non-interactive refusal.
        def out.tty? = true
        io = StringIO.new("n\n")
        def io.tty? = true
        context = build_context(worker: worker, stdin: io)
        assert_raises(UsageError) { Cart.new(context).run(%w[cart add], %w[10450114]) }
        assert_empty worker.calls
      end

      def test_add_defaults_to_one
        worker = run_change_worker(%w[cart add], %w[10450114])
        assert_equal({ "item_id" => "10450114", "delta" => 1, "remove" => false },
                     worker.calls.first[:params])
      end

      def test_add_passes_the_quantity_through_as_a_delta
        worker = run_change_worker(%w[cart add], %w[10450114 3])
        assert_equal 3, worker.calls.first[:params]["delta"]
      end

      def test_a_negative_quantity_is_a_decrement
        worker = run_change_worker(%w[cart add], %w[10450114 -2])
        assert_equal(-2, worker.calls.first[:params]["delta"])
      end

      def test_remove_sends_no_delta_at_all
        worker = run_change_worker(%w[cart remove], %w[10450114])
        assert_equal({ "item_id" => "10450114", "delta" => nil, "remove" => true },
                     worker.calls.first[:params])
      end

      # The absolute quantity never crosses the interface. Asserted on what the
      # command sends rather than on its source, because the first version of
      # this test grepped for the word "absolute" and matched the comment
      # explaining why absolutes are forbidden.
      def test_nothing_the_cli_accepts_can_set_an_absolute_quantity
        [%w[10450114], %w[10450114 3], %w[10450114 -2]].each do |args|
          worker = run_change_worker(%w[cart add], args)
          params = worker.calls.first[:params]
          assert_equal %w[item_id delta remove].sort, params.keys.sort
          refute params.key?("quantity"), "sent an absolute quantity for #{args.inspect}"
        end
      end

      def test_a_change_needs_an_item
        assert_raises(UsageError) { run_change_worker(%w[cart add], []) }
      end

      def test_a_change_of_zero_is_refused
        assert_raises(UsageError) { run_change_worker(%w[cart add], %w[10450114 0]) }
      end

      def test_a_quantity_that_is_not_a_number_is_refused
        assert_raises(UsageError) { run_change_worker(%w[cart add], %w[10450114 lots]) }
      end

      def test_an_unknown_subcommand_is_refused
        assert_raises(UsageError) { run_cart(path: %w[cart empty]) }
      end

      private

      def run_change(result, json: false)
        worker = FakeWorker.new(results: { "cart.add" => result })
        context = build_context(worker: worker, json: json, options: { confirm: true })
        Cart.new(context).run(%w[cart add], %w[10450114])
      end

      def run_change_worker(path, args)
        worker = FakeWorker.new(results: { "cart.add" => CHANGED, "cart.remove" => CHANGED })
        Cart.new(build_context(worker: worker, options: { confirm: true })).run(path, args)
        worker
      end

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
