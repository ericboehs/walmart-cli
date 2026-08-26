# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # Documentation is code that cannot fail, so the claims that would rot are
  # checked against the thing they describe.
  #
  # This exists because CONTRACTS.md spent weeks asserting that the cart
  # mutation runs off the main thread and that "there is no cart mutation
  # code".
  #
  # The two are not the same kind of statement, and this comment said they were
  # -- "both were true when written" -- which restated the retracted claim as
  # fact inside the test written to stop exactly that. The second was a fact
  # about the repository and was true at the time; a commit then made it false.
  # The first was never an observation. It was an inference from our own hooks
  # seeing nothing, believed when written and later disproved by evidence.
  #
  # A thing that was true and went stale is a maintenance problem. A thing that
  # was inferred and turned out wrong is a reasoning problem, and calling it
  # "true when written" quietly grants it evidence it never had.
  #
  # The document went on saying both, next to a contract section that
  # contradicted them.
  class DocumentationParityTest < TestCase
    ROOT = File.expand_path("../..", __dir__)

    # Every file that can state these claims, not just the one that did.
    #
    # The first version of this test read CONTRACTS.md and nothing else, which
    # is how it passed while the capture script's own docstring -- printed by
    # --help, so the most user-visible prose in the project -- still said the
    # mutation is issued from a Web Worker, that the cart actions are
    # not_implemented stubs, and that the contract has never been seen. A
    # parity test that reads one file gives parity with one file, and the
    # confidence it produced covered everything.
    NARRATIVE_FILES = %w[
      docs/CONTRACTS.md
      docs/TESTING.md
      DESIGN.md
      README.md
      scripts/capture-cart-mutation.py
      pyworker/cart_mutate.py
    ].freeze

    # Claims disproved by evidence or by a commit. The retraction in
    # CONTRACTS.md is the one place they may appear.
    RETRACTED = {
      /runs off the main thread/ => "the requests were observed with from_worker false",
      /issued from a Web Worker/ => "the requests come from the page",
      /there is no cart mutation code/ => "pyworker/cart_mutate.py exists",
      /not_implemented stub/ => "cart.add and cart.remove are implemented and gated"
    }.freeze

    # A retracted claim may be quoted, but only by a sentence that says it is
    # false. That is the difference between a history and a restatement, and it
    # is the whole distinction this test exists to keep.
    RETRACTING = Regexp.union(
      /disproved|disproves|retracted|inferred|believed/i,
      /was wrong|still said|never .*(true|observation)/i
    ).freeze

    def test_no_file_states_a_claim_the_evidence_retracted
      RETRACTED.each do |claim, correction|
        NARRATIVE_FILES.each do |file|
          offending = read(file).scan(/^.*#{claim.source}.*$/i).reject do |line|
            line.match?(RETRACTING) || retraction.include?(line)
          end
          assert_empty offending, "#{file} states #{claim.source.inspect} as fact, but #{correction}"
        end
      end
    end

    def test_the_help_text_describes_what_the_capture_keeps
      help = capture_source[/\A#!.*?\n"""(.*?)"""/m, 1]

      refute_nil help, "the capture script has no docstring, which is its --help"
      assert_match(/no response body/i, help)
      assert_match(/[Nn]o headers/, help)
      assert_match(/response/i, help)
    end

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

    def read(relative) = File.read(File.join(ROOT, relative))
    def contracts = @contracts ||= read("docs/CONTRACTS.md")

    # The one section allowed to quote the retracted claims, because quoting
    # them is what it is for.
    def retraction
      @retraction ||= contracts[/^### 2\. The conclusion drawn from that was wrong.*?^### /m].to_s
    end

    def mutation_source = @mutation_source ||= read("pyworker/cart_mutate.py")
    def capture_source = @capture_source ||= read("scripts/capture-cart-mutation.py")
  end
end
