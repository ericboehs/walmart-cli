# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    # `store pin` is what gives the cart-mutation store guard something to
    # compare against. Before it existed the guard refused every request, which
    # was correct and useless, so these tests are mostly about the ways pinning
    # could be worse than not pinning: pinning nothing, pinning after a failed
    # read, or pinning fields nobody has observed.
    class StoreTest < TestCase
      def test_pinning_saves_the_store_the_cart_reports
        run_store("pin", worker: worker_returning({ "fulfillment" => { "store_id" => "499" } }))

        assert_equal "499", reloaded_config.store["store_id"]
      end

      # DESIGN.md asks for name and zip. Neither has ever appeared in an
      # observed response, so neither is written. This is the same rule that
      # keeps the $6.99 charge unnamed: a plausible mapping is still a guess.
      def test_pinning_does_not_invent_a_name_or_a_zip
        run_store("pin", worker: worker_returning({ "fulfillment" => { "store_id" => "499" } }))

        assert_equal({ "store_id" => "499" }, reloaded_config.store)
      end

      # An unreadable cart is not a cart at store nil. Saving here would unpin
      # whatever was already there on the strength of a failed request.
      def test_a_cart_that_could_not_be_read_does_not_unpin_anything
        context = build_context(worker: worker_returning({ "error" => { "code" => "challenge_detected",
                                                                        "message" => "blocked" } }))
        context.config.store = { "store_id" => "77" }
        context.config.save

        error = assert_raises(Error) { Store.new(context).run(%w[store pin], []) }

        assert_match(/blocked/, error.message)
        assert_equal "77", reloaded_config.store["store_id"]
      end

      def test_a_cart_without_a_store_is_reported_not_pinned_as_blank
        context = build_context(worker: worker_returning({ "fulfillment" => { "store_id" => nil } }))

        assert_raises(Error) { Store.new(context).run(%w[store pin], []) }
        assert_nil reloaded_config.store
      end

      def test_show_says_when_nothing_is_pinned
        out = capture_store("show")

        assert_match(/no store pinned/, out)
      end

      def test_show_names_the_fields_it_does_not_have
        context = build_context
        context.config.store = { "store_id" => "499" }
        context.config.save

        Store.new(context).run(%w[store show], [])

        assert_match(/never observed/, out.string)
      end

      def test_an_unknown_subcommand_is_a_usage_error
        assert_raises(UsageError) { Store.new(build_context).run(%w[store teleport], []) }
      end

      def test_an_unexpected_argument_is_a_usage_error
        assert_raises(UsageError) { Store.new(build_context).run(%w[store show], ["499"]) }
      end

      private

      def worker_returning(payload)
        worker = Object.new
        worker.define_singleton_method(:run) { |_action, _options = {}| payload }
        worker
      end

      def run_store(subcommand, worker:)
        Store.new(build_context(worker: worker)).run(["store", subcommand], [])
      end

      def capture_store(subcommand)
        Store.new(build_context).run(["store", subcommand], [])
        out.string
      end

      # Read from disk rather than from the object under test, so a pin that
      # never reached the file cannot pass.
      def reloaded_config
        Config.load(paths: paths)
      end
    end
  end
end
