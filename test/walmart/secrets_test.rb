# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # Safety invariant 8. No test here may reach the real Keychain: the provider
  # is always faked or its subprocess runner is injected.
  class SecretsTest < TestCase
    def test_secret_values_never_render_themselves
      secret = Secrets::Secret.new("hunter2")

      assert_equal "[REDACTED]", secret.to_s
      assert_equal "[REDACTED]", "#{secret}" # rubocop:disable Style/RedundantInterpolation
      refute_includes secret.inspect, "hunter2"
      refute_includes JSON.generate({ "password" => secret }), "hunter2"
      assert_equal "hunter2", secret.value
    end

    def test_status_reports_states_not_values
      secrets = Secrets.new(provider: FakeProvider.new({ "email" => "a@example.com",
                                                         "password" => "p" }))

      assert_equal({ "provider" => "fake", "available" => true,
                     "email" => "present", "password" => "present",
                     "email_present" => true, "password_present" => true },
                   secrets.status)
      refute_includes secrets.status.to_s, "a@example.com"
    end

    # "absent" means add a record. "error" means the store refused us and adding
    # another record will not help. Conflating them sends the user in circles.
    def test_absent_and_unreadable_are_different_states
      absent = Secrets.new(provider: FakeProvider.new({}))
      denied = Secrets.new(provider: FakeProvider.new({ "email" => "a@example.com" },
                                                      error_accounts: %w[password]))

      assert_equal "absent", absent.status["password"]
      refute_predicate absent, :unreadable?

      assert_equal "error", denied.status["password"]
      assert_predicate denied, :unreadable?
      assert_equal "fake refusal", denied.last_error
    end

    def test_missing_credentials_are_not_an_error
      secrets = Secrets.new(provider: FakeProvider.new({}))

      assert_nil secrets.credentials_payload
      refute_predicate secrets, :complete?
    end

    def test_partial_credentials_do_not_produce_a_payload
      secrets = Secrets.new(provider: FakeProvider.new({ "email" => "a@example.com" }))

      assert_nil secrets.credentials_payload
    end

    def test_credentials_payload_is_the_only_place_raw_values_appear
      secrets = Secrets.new(provider: FakeProvider.new({ "email" => "a@example.com",
                                                         "password" => "p" }))

      assert_equal({ "email" => "a@example.com", "password" => "p" }, secrets.credentials_payload)
      refute_includes secrets.inspect, "a@example.com"
    end

    # Platform and PATH are injected so these run identically on Linux CI.
    def test_keychain_uses_argv_arrays_and_the_documented_record_names
      calls = []
      runner = lambda do |args|
        calls << args
        args.include?("-w") ? [0, "sekret\n"] : [0, "attributes"]
      end
      provider = keychain(runner: runner)

      assert_predicate provider, :available?
      assert provider.present?("email")
      assert_equal ["find-generic-password", "-s", "walmart-cli", "-a", "email"], calls.first
      assert_equal "sekret", provider.read("password").value
      assert_equal ["find-generic-password", "-s", "walmart-cli", "-a", "password", "-w"], calls.last
    end

    # Presence must not unlock the value, so the check omits -w.
    def test_presence_check_does_not_request_the_password_itself
      calls = []
      provider = keychain(runner: lambda { |args|
        calls << args
        [0, ""]
      })

      provider.present?("password")

      refute_includes calls.flatten, "-w"
    end

    def test_keychain_reports_a_missing_record_as_absent
      provider = keychain(runner: ->(_args) { [44, ""] })

      assert_equal :absent, provider.probe("email")
      refute provider.present?("email")
      assert_nil provider.read("email")
      assert_nil provider.last_error
    end

    def test_keychain_reports_a_denied_lookup_as_an_error
      provider = keychain(runner: ->(_args) { [51, ""] })

      assert_equal :error, provider.probe("email")
      refute provider.present?("email")
      assert_match(/exited 51/, provider.last_error)
    end

    def test_keychain_is_unavailable_off_macos
      provider = Secrets::KeychainProvider.new(
        runner: ->(_args) { [0, "x"] }, platform: "x86_64-linux", env: env
      )

      refute_predicate provider, :available?
      assert_equal :absent, provider.probe("email")
      assert_nil provider.read("email")
    end

    def test_keychain_is_unavailable_without_the_security_binary
      provider = Secrets::KeychainProvider.new(
        runner: ->(_args) { [0, "x"] }, platform: "arm64-darwin23",
        env: { "PATH" => File.join(tmp, "empty") }
      )

      refute_predicate provider, :available?
    end

    def test_linux_defaults_to_a_provider_that_is_honest_about_being_unimplemented
      secrets = Secrets.default(platform: "x86_64-linux")

      assert_equal "secret-service", secrets.provider_name
      refute_predicate secrets, :available?
      assert_nil secrets.credentials_payload
      assert_match(/manual login/, secrets.describe)
    end

    def test_macos_defaults_to_keychain
      assert_equal "keychain", Secrets.default(platform: "arm64-darwin23").provider_name
    end

    def test_unknown_platform_gets_the_null_provider
      assert_equal "none", Secrets.default(platform: "sparc-solaris").provider_name
    end

    private

    # A keychain provider that believes it is on macOS and can find `security`,
    # with the subprocess itself injected. Runs anywhere.
    def keychain(runner:)
      bin = File.join(tmp, "bin")
      FileUtils.mkdir_p(bin)
      security = File.join(bin, "security")
      File.write(security, "#!/bin/sh\nexit 0\n")
      File.chmod(0o755, security)

      Secrets::KeychainProvider.new(
        runner: runner, platform: "arm64-darwin23", env: { "PATH" => bin }
      )
    end
  end
end
