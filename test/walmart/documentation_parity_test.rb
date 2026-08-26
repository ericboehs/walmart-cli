# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # Documentation is code that cannot fail, so the claims that would rot are
  # checked against the thing they describe.
  #
  # This exists because CONTRACTS.md spent weeks asserting that the cart
  # mutation runs off the main thread and that "there is no cart mutation
  # code". Both were true when written. Evidence later disproved the first and
  # a commit disproved the second, and the document went on saying them --
  # repeating the original misdiagnosis as present fact, next to a contract
  # section that contradicted it.
  class DocumentationParityTest < TestCase
    ROOT = File.expand_path("../..", __dir__)

    def test_the_contract_does_not_deny_code_that_exists
      assert_path_exists File.join(ROOT, "pyworker/cart_mutate.py")
      refute_match(/there is no cart mutation code/i, contracts,
                   "CONTRACTS.md says the mutation is unwritten, and pyworker/cart_mutate.py exists")
    end

    def test_the_worker_thread_diagnosis_is_only_quoted_as_history
      section = contracts[/^### 2\. The conclusion drawn from that was wrong.*?^### /m]

      refute_nil section, "the section recording the wrong diagnosis is gone"
      contracts.scan(/^.*runs off the main thread.*$/) do |line|
        assert_includes section, line,
                        "'runs off the main thread' appears outside the section that retracts it: #{line}"
      end
    end

    def test_both_gates_are_documented_as_gates
      %w[ACCEPTED_RESPONSE_SHAPE PER_LINE_DELIVERY_PROVEN].each do |constant|
        assert_match(/^#{constant}\b/, mutation_source, "#{constant} is gone from the worker")
      end
      assert_match(/response is not observed, and neither is per-line delivery/i, contracts,
                   "CONTRACTS.md no longer says both gates are shut")
    end

    def test_the_capture_is_described_by_the_fields_it_actually_keeps
      documented = contracts[/^## What the first response capture keeps.*?^## /m]
      refute_nil documented, "the section listing what is kept is gone"

      %w[headers body].each do |dropped|
        assert_match(/[Nn]o (request |response )?#{dropped}/, documented,
                     "CONTRACTS.md does not say #{dropped} are dropped")
        refute_match(/^\s+"#{dropped}":/, capture_source,
                     "the capture writes #{dropped} that CONTRACTS.md says it drops")
      end
    end

    private

    def contracts = @contracts ||= File.read(File.join(ROOT, "docs/CONTRACTS.md"))
    def mutation_source = @mutation_source ||= File.read(File.join(ROOT, "pyworker/cart_mutate.py"))
    def capture_source = @capture_source ||= File.read(File.join(ROOT, "scripts/capture-cart-mutation.py"))
  end
end
