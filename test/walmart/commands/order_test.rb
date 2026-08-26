# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    class OrderTest < TestCase
      RECORD = {
        "order_id" => "200012345678901",
        "display_id" => "2000123-45678",
        "date" => "2026-08-20T14:02:00.000Z",
        "type" => "delivery",
        "item_count" => 2,
        "totals" => { "grand_total" => 44.32 },
        "items" => [{ "item_id" => "10450114", "name" => "Great Value Whole Milk, 1 Gallon" }]
      }.freeze

      OLDER = RECORD.merge("order_id" => "100000000000001", "date" => "2025-02-03T10:00:00.000Z").freeze

      # --- Archiving --------------------------------------------------------------

      def test_orders_are_written_to_the_archive
        run_sync(orders: [RECORD])

        assert_equal "delivery", Archive.new(paths: paths).record("200012345678901")["type"]
      end

      def test_the_count_of_new_orders_is_reported
        run_sync(orders: [RECORD, OLDER])

        assert_includes out.string, "archived 2 orders"
      end

      def test_a_second_sync_archives_nothing_new
        run_sync(orders: [RECORD])
        run_sync(orders: [RECORD])

        assert_includes out.string, "1 already archived"
      end

      # The expensive part of a sync is the detail page, so the worker is told
      # what we already hold and skips those.
      def test_known_ids_are_sent_to_the_worker
        run_sync(orders: [RECORD])
        worker = sync_worker([])
        run_sync(worker: worker)

        assert_equal ["200012345678901"], worker.calls.last[:params]["known_ids"]
      end

      def test_full_asks_for_everything_again
        run_sync(orders: [RECORD])
        worker = sync_worker([RECORD])
        run_sync(worker: worker, args: ["--full"])

        assert_empty worker.calls.last[:params]["known_ids"]
        assert worker.calls.last[:params]["full"]
      end

      # --- The window --------------------------------------------------------------

      def test_the_default_window_is_two_years
        worker = sync_worker([])
        run_sync(worker: worker)

        expected = (Date.today << 24).iso8601

        assert_equal expected, worker.calls.last[:params]["since"]
      end

      def test_all_asks_for_every_year_walmart_still_shows
        worker = sync_worker([])
        run_sync(worker: worker, args: ["--all"])

        assert_nil worker.calls.last[:params]["since"]
      end

      def test_year_narrows_to_one_calendar_year
        worker = sync_worker([])
        run_sync(worker: worker, args: ["--year", "2025"])

        assert_equal "2025-01-01", worker.calls.last[:params]["since"]
      end

      def test_a_nonsense_year_is_a_usage_error
        assert_raises(UsageError) { run_sync(args: ["--year", "twenty"]) }
      end

      # --- Stopping early ------------------------------------------------------------

      def test_a_complete_walk_exits_clean
        code = run_sync(orders: [RECORD], stopped: { "reason" => "complete" })

        assert_equal ExitCodes::OK, code
        refute_includes err.string, "stopped early"
      end

      def test_reaching_the_cutoff_is_also_clean
        code = run_sync(orders: [RECORD], stopped: { "reason" => "cutoff" })

        assert_equal ExitCodes::OK, code
      end

      # An interrupted walk keeps its records -- they are worth having -- but a
      # script must be able to tell that history is missing.
      def test_a_blocked_walk_keeps_its_records_and_says_so
        code = run_sync(orders: [RECORD], stopped: { "reason" => "challenge_detected" })

        assert_equal ExitCodes::UNVERIFIED, code
        assert_includes err.string, "walmart login"
        assert_equal "delivery", Archive.new(paths: paths).record("200012345678901")["type"]
      end

      def test_a_throttled_walk_advises_waiting
        run_sync(orders: [], stopped: { "reason" => "rate_limited" })

        assert_includes err.string, "throttling"
      end

      def test_an_unfamiliar_stop_reason_is_still_reported_as_incomplete
        code = run_sync(orders: [], stopped: { "reason" => "history_unreadable" })

        assert_equal ExitCodes::UNVERIFIED, code
        assert_includes err.string, "history_unreadable"
      end

      # --- Sync state ------------------------------------------------------------------

      def test_an_interrupted_walk_leaves_the_archive_marked_incomplete
        run_sync(orders: [RECORD], stopped: { "reason" => "rate_limited" })

        assert_predicate Archive.new(paths: paths), :incomplete?
      end

      # The worker still reports a cursor in its stop note. We must not write it
      # down: nothing replays it, and an opaque session identifier on disk with
      # no consumer is exactly what the minimization rule is about.
      def test_a_cursor_offered_by_the_worker_is_not_persisted
        run_sync(orders: [RECORD], stopped: { "reason" => "rate_limited", "cursor" => "CURSOR" })

        refute_includes Archive.new(paths: paths).sync_state.keys, "cursor"
        refute_includes File.read(paths.index_file), "CURSOR"
      end

      # The claim the old naming made, tested directly for the first time: a
      # second sync does not continue from where the first stopped. It restarts
      # at page one, and the only thing carried over is the set of ids whose
      # detail pages can be skipped. Proving the request omits a cursor is what
      # the persistence test should have been doing all along.
      def test_a_second_sync_restarts_rather_than_resuming
        worker = FakeWorker.new(results: { "order.sync" => {
                                  "orders" => [RECORD], "seen" => [],
                                  "stopped" => { "reason" => "page_limit", "cursor" => "CURSOR" }
                                } })
        stopped = { "reason" => "page_limit", "cursor" => "CURSOR" }
        run_sync(worker: worker, orders: [RECORD], stopped: stopped)
        run_sync(worker: worker, orders: [RECORD], stopped: stopped)

        second = worker.calls.last[:params]

        refute_includes second.keys, "cursor"
        refute_includes second.values.join(" "), "CURSOR"
      end

      def test_a_complete_walk_leaves_the_archive_settled
        run_sync(orders: [RECORD], stopped: { "reason" => "complete" })

        refute_predicate Archive.new(paths: paths), :incomplete?
      end

      def test_json_output_reports_the_stop_reason
        run_sync(orders: [RECORD], stopped: { "reason" => "page_limit" }, json: true)

        assert_equal "page_limit", stdout_json["stopped"]["reason"]
        assert_equal 1, stdout_json["archived"]
      end

      def test_an_unknown_subcommand_is_a_usage_error
        assert_raises(UsageError) { run_sync(path: %w[order purge]) }
      end

      # SETTLED decides whether a sync reports success, and it is a list, and
      # its wrong version fails open -- add a real stop reason and a truncated
      # walk exits 0 with an archive that believes it is whole. Nothing else in
      # either suite notices, which is how it sat here unpinned.
      def test_only_finishing_and_reaching_the_window_edge_count_as_settled
        assert_equal %w[complete cutoff], Order::SETTLED,
                     "adding a reason here makes a truncated sync report success. " \
                     "page_limit and no_next_page both leave history unread; if a " \
                     "reason belongs here, argue it in DESIGN.md first."
      end

      # A reason renamed on the Python side degrades to the catch-all branch and
      # to UNVERIFIED, which is safe. A reason *added* there and forgotten here
      # is invisible, and it is the direction that eventually misclassifies one.
      # Compared as text so the two definitions stay independent.
      def test_ruby_knows_every_stop_reason_the_worker_can_produce
        source = File.read(File.join(ROOT, "pyworker/orders.py"))
        emitted = source.scan(/Stop\(\s*"([a-z_]+)"/).flatten
        constants = source.scan(/^(COMPLETE|CUTOFF|PAGE_LIMIT|NO_NEXT_PAGE) = "([a-z_]+)"/)
                          .map(&:last)
        challenge = File.read(File.join(ROOT, "pyworker/challenge.py"))[/CHALLENGE_CODE = "([a-z_]+)"/, 1]
        produced = (emitted + constants + [challenge]).compact.uniq.sort

        assert_equal produced, Order::KNOWN_REASONS.sort,
                     "the worker and this command disagree about what a stop can be called"
      end

      # Written out rather than computed as KNOWN_REASONS - SETTLED, which is
      # the trap: derived that way, growing SETTLED shrinks the list being
      # checked, so the test quietly stops looking at the reason that just
      # became wrong. A literal cannot be talked out of an assertion.
      MUST_NOT_REPORT_SUCCESS = %w[
        page_limit no_next_page history_unreadable order_unreadable
        rate_limited challenge_detected
      ].freeze

      def test_no_unsettled_reason_reports_success
        MUST_NOT_REPORT_SUCCESS.each do |reason|
          code = run_sync(stopped: { "reason" => reason })

          assert_equal ExitCodes::UNVERIFIED, code,
                       "#{reason} left history unread and still reported success"
        end
      end

      # The two literals together have to account for every reason, or a new
      # one could be added to neither and checked by nothing.
      def test_every_known_reason_is_classified_one_way_or_the_other
        assert_equal Order::KNOWN_REASONS.sort,
                     (Order::SETTLED + MUST_NOT_REPORT_SUCCESS).sort
      end

      # The Ruby half of the same property. Asserted on the request because the
      # promise -- "these will be left untouched" -- was printed by a pre-flight
      # that could not see what --full did to the list it relied on.
      def test_protected_ids_are_sent_even_under_full
        archive_with_incompatible_record
        worker = FakeWorker.new(results: { "order.sync" => { "orders" => [], "seen" => [] } })
        run_sync(worker: worker, args: ["--full"])

        params = worker.calls.first[:params]

        assert_empty params["known_ids"], "--full deliberately re-reads what we have"
        assert_equal ["200012345678901"], params["protected_ids"]
      end

      def test_a_record_we_refuse_to_overwrite_is_announced_before_the_network
        archive_with_incompatible_record
        run_sync(args: [])

        assert_match(/written by a newer walmart-cli/, err.string)
        assert_match(/upgrade walmart-cli/, err.string)
      end

      def test_an_incompatible_record_is_not_counted_as_damaged
        archive_with_incompatible_record
        run_sync(args: [])

        refute_match(/cannot be read/, err.string)
      end

      # Defense in depth: we do not ask for these, but the worker is a separate
      # process and one refused write must not lose the orders after it.
      def test_a_returned_protected_record_does_not_abort_the_store_loop
        archive_with_incompatible_record
        returned = [
          { "order_id" => "200012345678901", "date" => "2026-08-20", "items" => [] },
          { "order_id" => "400000000000000", "date" => "2026-08-21", "items" => [] }
        ]
        worker = FakeWorker.new(results: {
                                  "order.sync" => { "orders" => returned, "seen" => returned }
                                })
        run_sync(worker: worker, args: [])

        assert Archive.new(paths: paths).readable?("400000000000000"),
               "the order after the refusal is still stored"
      end

      # --year means one calendar year, which is what the help, the design and
      # this class all claimed while the request carried only a lower bound.
      def test_year_sends_both_ends_of_the_window
        worker = FakeWorker.new(results: { "order.sync" => { "orders" => [], "seen" => [] } })
        run_sync(worker: worker, args: ["--year", "2024"])
        params = worker.calls.first[:params]

        assert_equal "2024-01-01", params["since"]
        assert_equal "2025-01-01", params["before"]
      end

      def test_the_rolling_default_window_is_open_at_the_top
        worker = FakeWorker.new(results: { "order.sync" => { "orders" => [], "seen" => [] } })
        run_sync(worker: worker, args: [])

        assert_nil worker.calls.first[:params]["before"]
      end

      def test_all_is_open_at_both_ends
        worker = FakeWorker.new(results: { "order.sync" => { "orders" => [], "seen" => [] } })
        run_sync(worker: worker, args: ["--all"])
        params = worker.calls.first[:params]

        assert_nil params["since"]
        assert_nil params["before"]
      end

      def test_the_window_that_was_asked_for_is_recorded
        run_sync(args: ["--year", "2024"], stopped: { "reason" => "cutoff" })
        state = Archive.new(paths: paths).sync_state

        assert_equal "2024-01-01", state["since"]
        assert_equal "2025-01-01", state["before"]
      end

      private

      def sync_worker(orders, stopped: { "reason" => "complete" }, seen: nil)
        FakeWorker.new(
          results: {
            "order.sync" => {
              "orders" => orders,
              "seen" => seen || orders.map { |order| { "order_id" => order["order_id"] } },
              "stopped" => stopped
            }
          }
        )
      end

      # An archive holding one record written by a newer walmart-cli.
      def archive_with_incompatible_record
        store = Archive.new(paths: paths)
        store.write({ "order_id" => "200012345678901", "date" => "2026-08-20", "items" => [] })
        path = File.join(paths.orders_dir, store.entry("200012345678901")["path"])
        File.write(path, JSON.dump(JSON.parse(File.read(path)).merge("_format" => 2)))
      end

      def run_sync(worker: nil, orders: [], stopped: { "reason" => "complete" }, seen: nil,
                   json: false, path: %w[order sync], args: [])
        worker ||= sync_worker(orders, stopped: stopped, seen: seen)
        Order.new(build_context(worker: worker, json: json)).run(path, args)
      end
    end
  end
end
