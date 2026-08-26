# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    class ItemTest < TestCase
      MILK = {
        "item_id" => "10450114",
        "name" => "Great Value Whole Milk, 1 Gallon",
        "brand" => "Great Value",
        "price" => 3.12,
        "price_display" => "$3.12",
        "was_price" => 3.48,
        "unit_price_display" => "2.4 ¢/fl oz",
        "seller_name" => "Walmart.com",
        "seller_type" => "INTERNAL",
        "first_party" => true,
        "availability" => "IN_STOCK",
        "fulfillment_options" => [
          { "type" => "DELIVERY", "available" => true, "label" => "Delivery" },
          { "type" => "PICKUP", "available" => false, "label" => "Pickup" }
        ],
        "min_quantity" => 1,
        "max_quantity" => 12,
        "snap_eligible" => true,
        "variants" => { "has_variants" => false, "count" => 0, "shape_unobserved" => false }
      }.freeze

      def test_the_item_is_described
        run_item(result: MILK)

        assert_includes out.string, "Great Value Whole Milk"
        assert_includes out.string, "$3.12"
        assert_includes out.string, "2.4 ¢/fl oz"
      end

      # The seller line is the first-party answer. Making a reader infer it
      # from a name is how "Walmart Restored" gets mistaken for Walmart.
      def test_a_first_party_offer_says_so
        run_item(result: MILK)

        assert_includes out.string, "Walmart.com (Walmart)"
      end

      def test_a_marketplace_offer_says_so
        run_item(result: MILK.merge("first_party" => false, "seller_name" => "Food Service Direct"))

        assert_includes out.string, "Food Service Direct (marketplace)"
      end

      def test_only_available_fulfillment_shows
        run_item(result: MILK)

        assert_includes out.string, "Delivery"
        refute_includes out.string, "Pickup"
      end

      def test_an_item_with_no_fulfillment_says_no
        run_item(result: MILK.merge("fulfillment_options" => []))

        assert_includes out.string, "no"
      end

      # A blank line is better than "1-unknown", which reads like a fact.
      def test_unstated_limits_are_omitted
        run_item(result: MILK.merge("min_quantity" => nil, "max_quantity" => nil))

        refute_includes out.string, "limits"
      end

      def test_stated_limits_are_shown
        run_item(result: MILK)

        assert_includes out.string, "1-12"
      end

      # We have never seen a populated variant family, so the tool says it
      # cannot read one rather than showing a listing for maybe the wrong size.
      def test_unreadable_variants_are_called_out
        variants = { "has_variants" => true, "count" => 4, "shape_unobserved" => true }
        run_item(result: MILK.merge("variants" => variants))

        assert_includes err.string, "cannot read"
      end

      # Whatever a command returns is handed to Kernel#exit. Asserting on
      # stdout while ignoring the return value is how two commands shipped
      # printing a good document and then crashing on exit(nil).
      def test_json_mode_returns_an_exit_code_and_not_nil
        assert_equal ExitCodes::OK, run_item(result: MILK, json: true)
      end

      def test_json_carries_the_whole_item
        run_item(result: MILK, json: true)

        assert_equal "10450114", stdout_json["item_id"]
        assert_equal "INTERNAL", stdout_json["seller_type"]
      end

      def test_the_id_is_forwarded_to_the_worker
        worker = FakeWorker.new(results: { "item" => MILK })
        run_item(worker: worker, args: ["https://www.walmart.com/ip/whole-milk/10450114"])

        expected = "https://www.walmart.com/ip/whole-milk/10450114"

        assert_equal expected, worker.calls.last[:params]["item"]
      end

      def test_an_item_is_required
        assert_raises(UsageError) { run_item(args: []) }
      end

      def test_a_missing_item_surfaces_as_an_error
        worker = FakeWorker.new(
          errors: { "item" => WorkerError.new("Walmart has no item 999", code: "not_found") }
        )

        error = assert_raises(WorkerError) { run_item(worker: worker) }
        assert_includes error.message, "no item"
      end

      private

      def run_item(worker: nil, result: MILK, json: false, args: ["10450114"])
        worker ||= FakeWorker.new(results: { "item" => result })
        Item.new(build_context(worker: worker, json: json)).run(["item"], args)
      end
    end
  end
end
