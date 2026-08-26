# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # Safety invariant 1: there is no checkout or place-order surface anywhere.
  #
  # These tests are the enforcement mechanism, not documentation. If someone
  # later adds a "convenient" checkout path, the build stops here.
  class CheckoutSurfaceTest < TestCase
    # The two audited files allowed to name checkout concepts, because their
    # job is to recognize and refuse them. Test files (basename `test_*`) are
    # also exempt, since they must be able to plant the terms they check for.
    ALLOWED_FILES = [
      "lib/walmart/safety.rb",
      "pyworker/safety.py"
    ].freeze

    def test_no_source_file_mentions_a_checkout_concept
      offenders = source_files.filter_map do |file|
        next if allowed?(file)

        found = Safety.violations(File.read(file))
        "#{relative(file)}: #{found.join(", ")}" unless found.empty?
      end

      assert_empty offenders,
                   "checkout-related vocabulary escaped safety.rb/safety.py:\n#{offenders.join("\n")}"
    end

    def test_command_surface_has_no_checkout_command
      names = (CLI::COMMANDS.keys + CLI::PLANNED.keys).map { |path| path.join(" ") }
      names.each do |name|
        assert_empty Safety.violations(name), "command surface exposes #{name}"
      end
    end

    def test_worker_action_registry_is_clean
      Worker::ACTIONS.each do |action|
        assert_empty Safety.violations(action), "worker action #{action} is forbidden"
      end
    end

    def test_worker_refuses_an_unregistered_action
      worker = Worker.new(paths: paths, config: config)
      error = assert_raises(SafetyError) { worker.run("cart.place") }

      assert_equal "unknown_action", error.code
      assert_equal ExitCodes::USAGE, error.exit_code
    end

    def test_ruby_and_python_action_registries_agree
      assert_equal Worker::ACTIONS.sort, python_actions.sort,
                   "Ruby and Python worker action lists have drifted apart"
    end

    # Two guard lists that disagree are worse than one: each side would believe
    # it was covered while a term slipped through the other.
    def test_ruby_and_python_forbidden_vocabulary_agree
      assert_equal Safety::FORBIDDEN_TERMS.sort, python_tuple("FORBIDDEN_TERMS").sort,
                   "forbidden term lists differ between safety.rb and safety.py"
    end

    def test_ruby_and_python_forbidden_url_fragments_agree
      assert_equal Safety::FORBIDDEN_URL_FRAGMENTS.sort, python_tuple("FORBIDDEN_URL_FRAGMENTS").sort,
                   "forbidden URL fragment lists differ between safety.rb and safety.py"
    end

    def test_forbidden_urls_are_recognized
      %w[
        https://www.walmart.com/checkout
        https://www.walmart.com/cart/checkout?x=1
        https://www.walmart.com/payment/add
        https://www.walmart.com/order-confirmation/123
      ].each do |url|
        assert Safety.forbidden_url?(url), "#{url} should be refused"
      end

      refute Safety.forbidden_url?("https://www.walmart.com/cart")
      refute Safety.forbidden_url?("https://www.walmart.com/ip/12345")
    end

    def test_separator_styles_cannot_smuggle_a_term
      %w[place_order place-order placeOrder PLACE.ORDER].each do |variant|
        refute_empty Safety.violations(variant), "#{variant} should be caught"
      end
    end

    def test_the_scan_covers_scripts_too
      # Reconnaissance tooling drives a real browser; it is exactly as capable
      # of wandering somewhere forbidden as the worker is.
      scanned = source_files.map { |file| relative(file) }

      assert_includes scanned, "scripts/recon.py"
      assert_includes scanned, "scripts/promote-fixture.py"
      assert_includes scanned, "bin/walmart"
    end

    private

    def source_files
      Dir.glob(File.join(ROOT, "{lib,pyworker,bin,scripts}/**/*")).select do |path|
        File.file?(path) &&
          !path.include?("/.venv/") &&
          !path.include?("__pycache__") &&
          !path.include?("/recon/") &&
          (path.end_with?(".rb", ".py") || path.end_with?("bin/walmart"))
      end
    end

    def allowed?(file)
      rel = relative(file)
      ALLOWED_FILES.include?(rel) || File.basename(rel).start_with?("test_")
    end

    def relative(file)
      file.delete_prefix("#{ROOT}/")
    end

    # Textual parse, on purpose: this test must not import or execute the
    # worker, and it must notice a rename that a runtime check would hide.
    def python_actions
      source = File.read(File.join(ROOT, "pyworker/fetch.py"))
      body = source[/^ACTIONS: .*?\{(.*?)^\}/m, 1]

      refute_nil body, "could not find the ACTIONS table in pyworker/fetch.py"
      body.scan(/^\s*"([^"]+)":/).flatten
    end

    # Same reasoning: read pyworker/safety.py as text so the two guard lists are
    # compared as written, not as imported.
    def python_tuple(name)
      source = File.read(File.join(ROOT, "pyworker/safety.py"))
      body = source[/^#{Regexp.escape(name)} = \((.*?)^\)/m, 1]

      refute_nil body, "could not find #{name} in pyworker/safety.py"
      body.scan(/"([^"]+)"/).flatten
    end
  end
end
