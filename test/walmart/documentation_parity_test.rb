# frozen_string_literal: true

require_relative "../test_helper"
require "json"

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

    # Every file that can state these claims, derived rather than remembered.
    #
    # This was a hand-written list of six. It missed pyworker/browser.py, whose
    # on_request docstring still said the mutation is issued from a Web Worker
    # -- in the code that justifies how we observe it. Parity with one file,
    # then parity with six, and the claim was sitting in the seventh.
    #
    # So the list is now every tracked file that could carry prose: git decides
    # membership, not memory. Vendored trees and captures are excluded because
    # they are not ours to correct; the exclusion is narrow and visible, which
    # a forgotten filename is not.
    # This file is excluded from its own scan, because a list of forbidden
    # claims has to contain them to be a list. That is the only self-exemption,
    # and it is one line rather than a habit of adding names.
    EXCLUDED = %r{
      \A(?:
        pyworker/(?:recon|\.venv)/ | vendor/ | \.git/ |
        test/walmart/documentation_parity_test\.rb\z
      )
    }x
    NARRATIVE_EXTENSIONS = %w[.md .py .rb .sh].freeze

    def self.narrative_files
      @narrative_files ||= IO.popen(["git", "-C", ROOT, "ls-files", "-z"], &:read)
                             .split("\0")
                             .grep_v(EXCLUDED)
                             .select { |path| NARRATIVE_EXTENSIONS.include?(File.extname(path)) }
                             .select { |path| File.file?(File.join(ROOT, path)) }
    end

    # Claims disproved by evidence or by a commit. The retraction in
    # CONTRACTS.md is the one place they may appear.
    RETRACTED = {
      /runs off the main thread/ => "the requests were observed with from_worker false",
      /issued from a Web Worker/ => "the requests come from the page",
      /there is no cart mutation code/ => "pyworker/cart_mutate.py exists",
      /not_implemented stub/ => "cart.add and cart.remove are implemented and gated",
      # The request has been observed four times. Calling the mutation
      # unobserved names the wrong unknown, and reads as a present claim even
      # in a file whose first line says the request is known.
      /mutation.{0,40}never.{0,20}observ/ => "the request is observed; the response is the unknown"
    }.freeze

    # A retracted claim may be quoted, but only by a sentence that says it is
    # false. That is the difference between a history and a restatement, and it
    # is the whole distinction this test exists to keep.
    RETRACTING = Regexp.union(
      /disproved|disproves|retracted|inferred|believed/i,
      /was wrong|still said|never .*(true|observation)/i
    ).freeze

    def test_no_file_states_a_claim_the_evidence_retracted
      assert_operator self.class.narrative_files.length, :>, 50,
                      "the file list collapsed; a parity test over nothing passes"

      RETRACTED.each do |claim, correction|
        self.class.narrative_files.each do |file|
          offending = read(file).scan(/^.*#{claim.source}.*$/i).reject do |line|
            line.match?(RETRACTING) || retraction.include?(line)
          end
          assert_empty offending, "#{file} states #{claim.source.inspect} as fact, but #{correction}"
        end
      end
    end

    # The list is derived, so the derivation is the thing that can silently
    # stop working. These are the files the hand-written version listed, plus
    # the one it missed.
    def test_the_derived_list_contains_the_files_that_have_carried_these_claims
      %w[
        docs/CONTRACTS.md docs/TESTING.md DESIGN.md README.md
        scripts/capture-cart-mutation.py pyworker/cart_mutate.py pyworker/browser.py
      ].each do |file|
        assert_includes self.class.narrative_files, file
      end
    end

    def test_the_help_text_describes_what_the_capture_keeps
      help = capture_source[/\A#!.*?\n"""(.*?)"""/m, 1]

      refute_nil help, "the capture script has no docstring, which is its --help"
      assert_match(/no response body/i, help)
      assert_match(/[Nn]o headers/, help)
      assert_match(/response/i, help)
    end

    # Numbers in prose that describe something countable in the repository.
    #
    # Both of these were wrong. The mutant count in every commit message from
    # e7ca045 onward was the previous figure plus the new entries, never a
    # count, and the drift reached fifteen. The defect tally was thirteen high
    # by the same method. A running total in someone's head, restated with the
    # authority of a measurement.
    # Both places the number appears. The first version of this test read one
    # of them, so changing the other drifted silently -- a count checked in one
    # place is the same bug one level down.
    def test_every_stated_defect_count_is_the_number_of_recorded_defects
      stated = testing.scan(/(\d+) (?:recorded defects|defects in the table)/).flatten

      assert_equal 2, stated.length, "expected both statements of the defect count"
      stated.each do |number|
        assert_equal defect_rows.length, number.to_i,
                     "TESTING.md claims #{number} defects and the table has #{defect_rows.length}"
      end
    end

    def test_the_stated_mutant_count_is_the_number_of_mutants
      stated = testing[%r{(\d+) mutants in `script/mutants\.json`}, 1]
      planned = JSON.parse(read("script/mutants.json")).length

      refute_nil stated, "TESTING.md no longer states a mutant count"
      assert_equal planned, stated.to_i,
                   "TESTING.md claims #{stated} mutants and the plan has #{planned}"
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
    def testing = @testing ||= read("docs/TESTING.md")

    # The ledger's data rows: not the header, not the separator.
    def defect_rows
      @defect_rows ||= begin
        section = testing[/^## The failure mode.*?^## The one to read first/m].to_s
        section.lines.select { |line| line.start_with?("| ") }
               .reject { |line| line.include?("What it could not see") }
               .reject { |line| line.gsub(/[|\-\s]/, "").empty? }
      end
    end

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
