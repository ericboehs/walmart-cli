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
        # "present", not "set". doctor never reads a value, so it cannot know a
        # credential is usable -- and it reported "set" for an empty keychain
        # item while login said none were found. A check may only claim what it
        # looked at.
        assert_includes out.string, "email + password present"
        refute_includes out.string, "password set"
      end

      # This asserted the hint said "walmart store pin", which is how the bug
      # survived: the tool advised an unimplemented command and a test required
      # it to. A check can hold a defect in place as firmly as it can catch one,
      # and this one read as perfectly reasonable -- "warns with the next step"
      # is a sensible thing to want. Nothing here asked whether the next step
      # was a command that exists.
      # Was: warns *without* naming a command, because `store pin` did not
      # exist and a remedy that does not exist is worse than no remedy. It
      # exists now, so the same check runs the other way round.
      def test_unpinned_store_warns_and_names_the_command_that_fixes_it
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_includes out.string, "no store pinned"
        # Hints are stderr, so that piping the table somewhere does not carry
        # advice into a file that wanted data.
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

      # Our flock says the profile is free and Chromium's own lock says it is
      # not. That pair means a browser outlived the command that started it,
      # which is the one failure with no `walmart` process in ps to find.
      def test_a_stale_chromium_lock_is_reported_as_a_leftover_browser
        FileUtils.mkdir_p(paths.browser_profile)
        File.write(File.join(paths.browser_profile, "SingletonLock"), "")

        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_includes out.string, "warn  profile"
        assert_includes err.string, "leftover Chromium"
      end

      # Chromium writes SingletonLock as a symlink to a host:pid target that
      # does not exist as a file. File.exist? follows the link and says no.
      def test_a_dangling_singleton_symlink_still_counts
        FileUtils.mkdir_p(paths.browser_profile)
        File.symlink("somehost-12345", File.join(paths.browser_profile, "SingletonLock"))

        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_includes err.string, "leftover Chromium"
      end

      def test_a_clean_profile_does_not_warn_about_a_browser
        FileUtils.mkdir_p(paths.browser_profile)

        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_includes out.string, "ok    profile"
        refute_includes err.string, "leftover Chromium"
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

      # Eric configured cdp_url, ran doctor, and got a page of green ticks that
      # said nothing about the attachment he had just set up -- while the
      # profile line reported a directory attached mode never touches.
      def test_doctor_reports_that_it_is_attached
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }),
                   config: config("cdp_url" => "http://127.0.0.1:9222"),
                   version: "Chrome/151")

        assert_match(%r{attachment.+attached to http://127\.0\.0\.1:9222 \(Chrome/151\)}, out.string)
      end

      def test_a_configured_but_dead_endpoint_is_a_warning_with_the_remedy
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }),
                   config: config("cdp_url" => "http://127.0.0.1:9222"),
                   version: nil)

        assert_match(/nothing is listening/, out.string)
        # Hints go to stderr, so the remedy is asserted where it is printed.
        assert_match(/attachment: run: walmart browser start/, err.string)
      end

      def test_not_attached_is_reported_as_a_normal_state_not_a_warning
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }))

        assert_match(/ok\s+attachment\s+not attached/, out.string)
      end

      # "no browser profile yet -- run walmart login" is advice for a mode we
      # are not in.
      def test_the_launched_profile_is_not_reported_as_missing_while_attached
        run_doctor(worker: FakeWorker.new(results: { "probe" => PROBE }),
                   config: config("cdp_url" => "http://127.0.0.1:9222"),
                   version: "Chrome/151")

        refute_match(/no browser profile yet/, out.string)
        assert_match(/not used while attached/, out.string)
      end

      private

      # `version:` stands in for the CDP endpoint's reply. Stubbed rather than
      # served, because doctor must not need a live browser to report on one --
      # and a test that opens a socket is a test that fails on someone's laptop
      # for reasons unrelated to the code.
      def run_doctor(worker:, secrets: nil, json: false, config: nil, version: :none)
        context = build_context(worker: worker, secrets: secrets, json: json)
        context.define_singleton_method(:config) { config } if config
        doctor = Doctor.new(context)
        doctor.define_singleton_method(:attachment_version) { |_url| version } unless version == :none
        doctor.run(["doctor"], [])
      end
    end
  end
end
