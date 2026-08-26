# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    class DoctorTest < TestCase
      PROBE = {
        "protocol" => 1,
        "python" => "3.12.0",
        "playwright" => "1.48.0",
        "browsers" => { "chrome" => "/Applications/Google Chrome" },
        "profile_exists" => false
      }.freeze

      def test_healthy_environment_exits_zero
        code = run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_equal ExitCodes::OK, code
        assert_includes out.string, "playwright"
        assert_includes out.string, "1.48.0"
      end

      def test_missing_playwright_is_a_hard_failure_with_a_fix
        probe = PROBE.merge("playwright" => nil)
        code = run_doctor(worker: FakeWorker.new(results: { "probe" => probe }))

        assert_equal ExitCodes::FAILED, code
        assert_includes err.string, "uv sync"
      end

      def test_missing_browser_is_a_hard_failure
        probe = PROBE.merge("browsers" => {})
        code = run_doctor(worker: FakeWorker.new(results: { "probe" => probe }))

        assert_equal ExitCodes::FAILED, code
        assert_includes out.string, "FAIL"
        assert_includes err.string, "install Google Chrome"
      end

      # "Install a browser" is the wrong advice when a browser is installed and
      # Playwright is the thing that is broken.
      def test_a_broken_playwright_lookup_reads_differently_from_a_missing_browser
        probe = PROBE.merge(
          "browsers" => {},
          "browser_errors" => { "playwright-chromium" => "Executable doesn't exist" }
        )
        code = run_doctor(worker: FakeWorker.new(results: { "probe" => probe }))

        assert_equal ExitCodes::FAILED, code
        assert_includes out.string, "browser lookup failed"
        assert_includes out.string, "Executable doesn't exist"
        refute_includes out.string, "no Chrome or Chromium found"
      end

      def test_a_found_browser_with_a_lookup_error_warns_rather_than_fails
        probe = PROBE.merge(
          "browser_errors" => { "playwright-chromium" => "Executable doesn't exist" }
        )
        code = run_doctor(worker: FakeWorker.new(results: { "probe" => probe }))

        assert_equal ExitCodes::OK, code
        assert_includes out.string, "warn  browser"
        assert_includes out.string, "Executable doesn't exist"
      end

      def test_a_dead_worker_is_reported_rather_than_crashing_doctor
        worker = FakeWorker.new(errors: { "probe" => WorkerError.new("boom", code: "no_result") })
        code = run_doctor(worker: worker)

        assert_equal ExitCodes::FAILED, code
        assert_includes out.string, "boom"
      end

      # Credentials are a warning, never a failure: manual login always works.
      def test_missing_credentials_only_warn
        code = run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }),
                          secrets: Secrets.new(provider: FakeProvider.new({})))

        assert_equal ExitCodes::OK, code
        assert_includes out.string, "warn"
      end

      # Safety invariant 8: doctor may report presence, never values.
      def test_doctor_never_prints_a_credential_value
        provider = FakeProvider.new({ "email" => "eric@example.com", "password" => "hunter2" })
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }),
                   secrets: Secrets.new(provider: provider))

        refute_includes out.string, "hunter2"
        refute_includes out.string, "eric@example.com"
        refute_includes err.string, "hunter2"
        assert_includes out.string, "email + password set"
      end

      def test_unpinned_store_warns_with_the_next_step
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_includes err.string, "walmart store pin"
      end

      # The lock file is never unlinked, so after the first login it is there
      # forever. Warning about it forever would have doctor -- the one command
      # whose job is an accurate answer -- send users hunting for a phantom.
      def test_a_leftover_lock_file_does_not_warn_about_a_phantom_process
        FileUtils.mkdir_p(paths.browser_profile)
        File.write(paths.profile_lock, "12345")

        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_includes out.string, "ok    profile"
        refute_includes err.string, "another walmart command"
      end

      def test_an_actively_held_lock_warns
        FileUtils.mkdir_p(paths.browser_profile)
        File.write(paths.profile_lock, "12345")
        File.open(paths.profile_lock, "r+") do |holder|
          holder.flock(File::LOCK_EX | File::LOCK_NB)
          begin
            run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

            assert_includes out.string, "in use"
            assert_includes err.string, "another walmart command is running"
          ensure
            holder.flock(File::LOCK_UN)
          end
        end
      end

      def test_pinned_store_is_shown
        config.store = { "store_id" => "100", "name" => "Test Store", "zip" => "00000" }
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_includes out.string, "Test Store (100) 00000"
      end

      def test_json_output_is_one_document
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }), json: true)
        payload = JSON.parse(out.string)

        assert_includes payload.keys, "checks"
        assert_equal true, payload["ok"]
      end

      private

      def run_doctor(worker:, secrets: nil, json: false)
        context = build_context(worker: worker, secrets: secrets, json: json)
        Doctor.new(context).run(["doctor"], [])
      end
    end
  end
end
