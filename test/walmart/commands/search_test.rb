# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    # The display half of search is testable today; the request half is blocked
    # in the worker until the variables contract is observed. These tests cover
    # what the user sees, using worker results shaped from the observed schema.
    class SearchTest < TestCase
      MILK = {
        "item_id" => "10450114",
        "name" => "Great Value Whole Milk, 1 Gallon",
        "price" => 3.12,
        "price_display" => "$3.12",
        "unit_price_display" => "2.4 ¢/fl oz",
        "seller_name" => "Walmart.com",
        "first_party" => true,
        "delivery" => true,
        "delivery_date" => "2026-08-27"
      }.freeze

      MARKETPLACE = {
        "item_id" => "55",
        "name" => "Bulk Milk Powder, 50 lb",
        "price" => 214.00,
        "price_display" => "$214.00",
        "seller_name" => "Food Service Direct",
        "first_party" => false,
        "delivery" => false
      }.freeze

      def test_results_are_listed_with_price_and_delivery
        run_search(results: [MILK])

        assert_includes out.string, "Great Value Whole Milk"
        assert_includes out.string, "$3.12"
        assert_includes out.string, "2.4 ¢/fl oz"
        assert_includes out.string, "2026-08-27"
      end

      def test_marketplace_sellers_are_hidden_by_default
        run_search(results: [MILK, MARKETPLACE])

        assert_includes out.string, "Great Value Whole Milk"
        refute_includes out.string, "Food Service Direct"
      end

      def test_the_marketplace_flag_widens_the_list
        run_search(results: [MILK, MARKETPLACE], args: ["whole milk", "--marketplace"])

        assert_includes out.string, "Food Service Direct"
      end

      # "Walmart has none" and "we filtered them all out" are different facts,
      # and reporting the second as the first sends the user hunting a bug in
      # Walmart's catalog instead of reading our own flag.
      def test_filtering_everything_out_says_so
        code = run_search(results: [MARKETPLACE])

        assert_equal ExitCodes::OK, code
        assert_includes out.string + err.string, "marketplace"
        assert_includes out.string + err.string, "--marketplace"
      end

      def test_a_genuinely_empty_result_is_reported_plainly
        run_search(results: [])

        combined = out.string + err.string
        assert_includes combined, "whole milk"
        refute_includes combined, "--marketplace"
      end

      def test_the_query_reaches_the_worker
        worker = search_worker([MILK])
        run_search(worker: worker)

        assert_equal "search", worker.calls.first[:action]
        assert_equal "whole milk", worker.calls.first[:params]["query"]
      end

      def test_unquoted_words_are_joined_into_one_query
        # `walmart search whole milk` is how people type. Refusing it teaches
        # nothing and helps no one.
        worker = search_worker([MILK])
        run_search(worker: worker, args: %w[whole milk])

        assert_equal "whole milk", worker.calls.first[:params]["query"]
      end

      def test_an_empty_query_is_a_usage_error
        assert_raises(UsageError) { run_search(args: []) }
      end

      def test_paging_flags_reach_the_worker
        worker = search_worker([MILK])
        run_search(worker: worker, args: ["whole milk", "--page", "3", "--limit", "5"])

        assert_equal 3, worker.calls.first[:params]["page"]
        assert_equal 5, worker.calls.first[:params]["limit"]
      end

      def test_a_nonsense_page_is_refused_before_the_worker_runs
        worker = search_worker([MILK])

        assert_raises(UsageError) { run_search(worker: worker, args: ["milk", "--page", "abc"]) }
        assert_empty worker.calls
      end

      def test_more_results_are_advertised_with_the_next_page
        run_search(results: [MILK], pagination: { "has_more" => true, "page" => 2 })

        assert_includes out.string + err.string, "--page 3"
      end

      def test_json_output_is_machine_readable
        code = run_search(results: [MILK], json: true)

        payload = stdout_json
        assert_equal ExitCodes::OK, code
        assert_equal "whole milk", payload["query"]
        assert_equal "10450114", payload["items"].first["item_id"]
      end

      def test_json_output_respects_the_default_filter
        run_search(results: [MILK, MARKETPLACE], json: true)

        ids = stdout_json["items"].map { |item| item["item_id"] }

        assert_equal ["10450114"], ids
      end

      # A price we could not read must print as nothing, not as $0.00. A wrong
      # price is worse than a missing one.
      def test_an_unreadable_price_is_left_blank
        run_search(results: [MILK.merge("price" => nil, "price_display" => nil)])

        refute_includes out.string, "$0.00"
        assert_includes out.string, "Great Value Whole Milk"
      end

      def test_a_price_without_a_rendered_string_is_formatted
        run_search(results: [MILK.merge("price_display" => nil)])

        assert_includes out.string, "$3.12"
      end

      def test_an_item_without_a_delivery_date_still_says_yes
        run_search(results: [MILK.merge("delivery_date" => nil)])

        assert_includes out.string, "yes"
      end

      # The worker refuses to guess the request; the CLI must pass that refusal
      # through intact rather than reporting an empty catalog.
      def test_the_blocked_request_contract_surfaces_as_an_error
        worker = FakeWorker.new(
          errors: { "search" => WorkerError.new("the request contract has not been observed",
                                                code: "not_implemented") }
        )

        error = assert_raises(WorkerError) { run_search(worker: worker) }
        assert_includes error.message, "has not been observed"
      end

      def test_a_bot_block_is_reported_as_a_challenge_not_a_failure
        worker = FakeWorker.new(
          errors: { "search" => WorkerError.new("bot protection blocked this request",
                                                code: "challenge_detected") }
        )

        error = assert_raises(WorkerError) { run_search(worker: worker) }
        assert_predicate error, :login_required?
      end

      private

      def search_worker(items, pagination: {})
        FakeWorker.new(results: { "search" => { "items" => items, "pagination" => pagination } })
      end

      def run_search(worker: nil, results: nil, pagination: {}, args: ["whole milk"], json: false)
        worker ||= search_worker(Array(results), pagination: pagination)
        context = build_context(worker: worker, json: json)
        Search.new(context).run(["search"], args)
      end
    end
  end
end
