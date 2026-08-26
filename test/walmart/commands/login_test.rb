# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    class LoginTest < TestCase
      SIGNED_IN = { "signed_in" => true, "detected_by" => "sign-out", "elapsed_s" => 12.0 }.freeze

      def test_login_is_always_visible
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        run_login(worker: worker)

        assert_equal false, worker.calls.first[:options]["headless"]
      end

      def test_stored_credentials_are_forwarded_over_the_worker_request
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        run_login(worker: worker, secrets: full_secrets)

        assert_equal({ "email" => "eric@example.com", "password" => "hunter2" },
                     worker.calls.first[:secrets])
      end

      # Safety invariant 8: nothing printed may contain a credential.
      def test_login_output_never_contains_a_credential
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        run_login(worker: worker, secrets: full_secrets)

        refute_includes out.string, "hunter2"
        refute_includes err.string, "hunter2"
        refute_includes out.string, "eric@example.com"
        refute_includes err.string, "eric@example.com"
      end

      def test_manual_flag_withholds_stored_credentials
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        run_login(worker: worker, secrets: full_secrets, args: ["--manual"])

        assert_nil worker.calls.first[:secrets]
        assert_equal true, worker.calls.first[:params]["manual"]
      end

      def test_missing_credentials_fall_back_to_manual_login
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        code = run_login(worker: worker, secrets: Secrets.new(provider: FakeProvider.new({})))

        assert_equal ExitCodes::OK, code
        assert_nil worker.calls.first[:secrets]
        assert_includes out.string + err.string, "manual sign-in"
      end

      # A locked or denied keychain looks exactly like an empty one from here.
      # The state Eric actually had: a keychain item that exists and is empty.
      # It must not read as "none found", which offers to create the record he
      # already has, and it must not read as a store failure either.
      def test_an_empty_keychain_entry_is_named_rather_than_called_missing
        worker = FakeWorker.new(results: { "login" => { "signed_in" => true } })
        empty = Secrets.new(provider: FakeProvider.new({ "email" => "a@b.c", "password" => "" }))

        run_login(worker: worker, secrets: empty)
        combined = out.string + err.string

        assert_includes combined, "password entry is empty"
        assert_includes combined, "-U -s walmart-cli"
        refute_includes combined, "no stored credentials found"
      end

      # Telling that user "no stored credentials found" sends them to type a
      # password by hand when the real fix is unlocking the keychain.
      def test_an_unreadable_keychain_is_not_reported_as_missing_credentials
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        denied = Secrets.new(provider: FakeProvider.new({}, error_accounts: %w[email password]))
        run_login(worker: worker, secrets: denied)

        combined = out.string + err.string

        assert_includes combined, "credential store unreadable"
        assert_includes combined, "fake refusal"
        refute_includes combined, "no stored credentials found"
      end

      def test_an_unreadable_keychain_still_opens_a_manual_login
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        denied = Secrets.new(provider: FakeProvider.new({}, error_accounts: %w[password]))
        code = run_login(worker: worker, secrets: denied)

        assert_equal ExitCodes::OK, code
        assert_nil worker.calls.first[:secrets]
      end

      # The human may be clearing press-and-hold challenges and typing a texted
      # code, so the worker must be given time.
      def test_worker_timeout_exceeds_the_wait_for_the_sms_code
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        run_login(worker: worker, args: ["--wait", "60"])

        assert_equal 60, worker.calls.first[:params]["wait_seconds"]
        assert_operator worker.calls.first[:timeout], :>, 60
      end

      # `--wait abc` used to become 0: the worker then said "no signed-in session
      # after 0s" and the typo looked like a worker bug.
      def test_a_non_numeric_wait_is_a_usage_error
        context = build_context(worker: FakeWorker.new)

        error = assert_raises(UsageError) { Login.new(context).run(["login"], ["--wait", "abc"]) }

        assert_match(/--wait needs a positive whole number/, error.message)
      end

      def test_an_empty_wait_is_a_usage_error
        context = build_context(worker: FakeWorker.new)

        assert_raises(UsageError) { Login.new(context).run(["login"], ["--wait="]) }
      end

      def test_a_zero_wait_is_a_usage_error
        context = build_context(worker: FakeWorker.new)

        assert_raises(UsageError) { Login.new(context).run(["login"], ["--wait", "0"]) }
      end

      def test_a_negative_wait_is_a_usage_error
        context = build_context(worker: FakeWorker.new)

        assert_raises(UsageError) { Login.new(context).run(["login"], ["--wait=-30"]) }
      end

      def test_a_bad_wait_never_opens_a_browser
        worker = FakeWorker.new(results: { "login" => SIGNED_IN })
        context = build_context(worker: worker)

        assert_raises(UsageError) { Login.new(context).run(["login"], ["--wait", "abc"]) }
        assert_empty worker.calls
      end

      def test_progress_events_reach_the_user
        worker = FakeWorker.new(
          results: { "login" => SIGNED_IN },
          events: { "login" => [{ "type" => "progress", "message" => "clear the press-and-hold challenge" }] }
        )
        run_login(worker: worker)

        assert_includes err.string + out.string, "clear the press-and-hold challenge"
      end

      # During a five-minute visible login these are the only explanation the
      # user gets for why the window is just sitting there.
      def test_warn_and_error_log_events_are_shown_without_verbose
        worker = FakeWorker.new(
          results: { "login" => SIGNED_IN },
          events: { "login" => [
            { "type" => "log", "level" => "warn", "message" => "email field not found" },
            { "type" => "log", "level" => "error", "message" => "refused to click a control" },
            { "type" => "log", "level" => "info", "message" => "routine chatter" }
          ] }
        )
        run_login(worker: worker)

        combined = out.string + err.string

        assert_includes combined, "email field not found"
        assert_includes combined, "refused to click a control"
        refute_includes combined, "routine chatter"
      end

      def test_failed_login_exits_one_and_explains
        worker = FakeWorker.new(
          results: { "login" => { "signed_in" => false, "notes" => ["no signed-in session after 300s"] } }
        )
        code = run_login(worker: worker)

        assert_equal ExitCodes::FAILED, code
        assert_includes err.string, "no signed-in session"
      end

      def test_logout_requires_confirmation_when_not_interactive
        FileUtils.mkdir_p(paths.browser_profile)
        context = build_context(worker: FakeWorker.new)

        error = assert_raises(UsageError) { Login.new(context).run(["logout"], []) }

        assert_equal "confirmation_required", error.code
        assert_path_exists paths.browser_profile
      end

      def test_logout_with_confirm_removes_the_profile
        FileUtils.mkdir_p(paths.browser_profile)
        File.write(paths.profile_lock, "1")
        context = build_context(worker: FakeWorker.new, options: { confirm: true })
        code = Login.new(context).run(["logout"], [])

        assert_equal ExitCodes::OK, code
        refute_path_exists paths.browser_profile
        refute_path_exists paths.profile_lock
      end

      # A profile that survived deletion but was reported as gone would leave a
      # signed-in session on disk while the user believes they signed out.
      # FileUtils.rm_rf fails silently, which is exactly how that happens.
      def test_logout_verifies_deletion_before_claiming_success
        FileUtils.mkdir_p(paths.browser_profile)
        parent = File.dirname(paths.browser_profile)
        context = build_context(worker: FakeWorker.new, options: { confirm: true })

        File.chmod(0o500, parent)
        begin
          error = assert_raises(Error) { Login.new(context).run(["logout"], []) }

          assert_equal "logout_failed", error.code
          assert_match(/still there/, error.message)
          assert_path_exists paths.browser_profile
        ensure
          File.chmod(0o700, parent)
        end
      end

      def test_logout_without_a_session_is_not_an_error
        context = build_context(worker: FakeWorker.new)

        assert_equal ExitCodes::OK, Login.new(context).run(["logout"], [])
      end

      def test_unknown_flag_is_rejected_rather_than_ignored
        context = build_context(worker: FakeWorker.new)

        assert_raises(UsageError) { Login.new(context).run(["login"], ["--headless"]) }
      end

      def test_switches_given_a_value_are_rejected
        context = build_context(worker: FakeWorker.new)

        assert_raises(UsageError) { Login.new(context).run(["login"], ["--manual=true"]) }
        assert_raises(UsageError) { Login.new(context).run(["login"], ["--force=true"]) }
      end

      def test_a_stray_word_is_refused_rather_than_treated_as_login_args
        context = build_context(worker: FakeWorker.new)

        error = assert_raises(UsageError) { Login.new(context).run(["login"], ["logout"]) }

        assert_match(/unexpected argument: logout/, error.message)
      end

      def test_wait_will_not_swallow_the_next_flag
        context = build_context(worker: FakeWorker.new)

        assert_raises(UsageError) { Login.new(context).run(["login"], ["--wait", "--force"]) }
      end

      private

      def full_secrets
        Secrets.new(provider: FakeProvider.new({ "email" => "eric@example.com",
                                                 "password" => "hunter2" }))
      end

      def run_login(worker:, secrets: nil, args: [], json: false)
        context = build_context(worker: worker, secrets: secrets, json: json)
        Login.new(context).run(["login"], args)
      end
    end
  end
end
