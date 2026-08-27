# frozen_string_literal: true

require_relative "../test_helper"
require "json"

module Walmart
  # The options Ruby sends the worker, pinned so Python can be tested against
  # the real shape rather than an imagined one.
  #
  # This test exists because of a specific defect: the cart mutation's store
  # guard was tested with `pinned_store` as a bare string, while this side has
  # always sent the whole store object. Both suites passed. In production the
  # guard would have compared a live store id against the rendering of a Hash
  # and refused every correctly configured request.
  #
  # So the contract file is the shared artefact: this test asserts the producer
  # still emits that shape, and the Python tests consume the same file.
  class WorkerOptionsContractTest < TestCase
    CONTRACT = JSON.parse(File.read(File.expand_path("../contracts/worker_options.json", __dir__)))

    # The store the production writer actually produces. `store pin` saves
    # store_id and nothing else, so a contract carrying a name and a ZIP would
    # pin Python's tests to a shape this side can no longer emit -- which is
    # the defect this file exists for, in the other direction.
    PINNED = { "store_id" => "5260" }.freeze

    def test_the_worker_sends_the_shape_the_contract_records
      assert_equal CONTRACT.keys.reject { |key| key.start_with?("_") }.sort,
                   options_for(store: PINNED).keys.sort
    end

    def test_the_contract_records_what_store_pin_writes
      assert_equal CONTRACT["pinned_store"], PINNED
      assert_equal %w[store_id], CONTRACT["pinned_store"].keys
    end

    def test_the_pinned_store_is_the_whole_store_object_not_an_id
      store = PINNED
      pinned = options_for(store: store)["pinned_store"]

      refute_kind_of String, pinned, "Python's guard reads pinned_store as an object"
      assert_equal store, pinned
      assert_equal CONTRACT["pinned_store"].keys.sort, pinned.keys.sort
    end

    def test_an_unpinned_store_sends_nothing_rather_than_an_empty_object
      assert_nil options_for(store: nil)["pinned_store"]
    end

    private

    def options_for(store:)
      config = Config.new(paths: paths, data: store ? { "store" => store } : {})
      Worker.new(paths: paths, config: config).send(:default_options)
    end
  end
end
