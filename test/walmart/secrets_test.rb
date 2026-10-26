# frozen_string_literal: true

require "pp"
require "yaml"

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

    def message_from_raising(secret)
      raise "credential was #{secret}"
    rescue RuntimeError => e
      e.message
    end

    # The leak we are guarding against is never a deliberate print. It is a
    # backtrace, a `p` left in while debugging, or some object that merely
    # happens to hold a Secret being logged. So this walks every route Ruby
    # has from an object to text, rather than the two we happen to call.
    def test_no_route_from_a_secret_to_text_reveals_it
      secret = Secrets::Secret.new("hunter2")
      holder = Struct.new(:credential).new(secret)

      renderings = {
        "to_s" => secret.to_s,
        "inspect" => secret.inspect,
        "interpolation" => "#{secret}", # rubocop:disable Style/RedundantInterpolation
        "inside an array" => [secret].inspect,
        "inside a hash" => { password: secret }.inspect,
        "format %s" => format("%s", secret),
        "format %p" => format("%p", secret),
        "json" => JSON.generate({ "password" => secret }),
        "pretty json" => JSON.pretty_generate({ "password" => secret }),
        "pretty print" => PP.pp(secret, +""),
        "an object holding one" => holder.inspect,
        "an exception message" => message_from_raising(secret),
        "String()" => String(secret)
      }

      renderings.each do |route, rendered|
        refute_includes rendered, "hunter2", "a secret leaked through #{route}"
      end
    end

    # Marshal and Psych read instance variables directly, so none of the text
    # routes above protect them. They raise rather than redacting: a Secret
    # that round-trips as "[REDACTED]" is a credential that silently fails to
    # log in, which is harder to diagnose than an exception at the line that
    # tried to serialize it.
    def test_a_secret_refuses_to_be_marshalled
      error = assert_raises(Error) { Marshal.dump(Secrets::Secret.new("hunter2")) }

      assert_equal "secret_serialization", error.code
    end

    def test_a_secret_refuses_to_be_dumped_as_yaml
      assert_raises(Error) { YAML.dump(Secrets::Secret.new("hunter2")) }
    end

    def test_a_secret_still_hands_over_its_value_when_asked_directly
      # The whole point is that one call site can read it; a Secret nobody can
      # use is not secure, it is broken.
      assert_equal "hunter2", Secrets::Secret.new("hunter2").value
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

    # Eric hit this on his first real login. The keychain held a password item
    # with an empty value: `security` exits 0, the presence probe says present,
    # doctor reported "email + password set", and login said "no stored
    # credentials found" and offered to create the record that already existed.
    # Three components, all self-consistent, and the advice did not work.
    def test_an_item_that_exists_but_holds_nothing_is_not_a_credential
      provider = keychain(runner: ->(args) { args.include?("-w") ? [0, "\n"] : [0, "attributes"] })

      assert_equal :present, provider.probe("password")
      assert_nil provider.read("password")
      assert_includes provider.empty_accounts, "password"
      assert_nil provider.last_error, "an empty value is not a store failure"
    end

    def test_a_readable_credential_is_never_reported_as_empty
      provider = keychain(runner: ->(args) { args.include?("-w") ? [0, "sekret\n"] : [0, "attrs"] })

      provider.read("password")

      assert_empty provider.empty_accounts
    end

    def test_the_facade_reports_which_account_is_empty
      secrets = Secrets.new(provider: keychain(runner: lambda { |args|
        return [0, "attrs"] unless args.include?("-w")

        args.include?("password") ? [0, "\n"] : [0, "someone@example.com\n"]
      }))

      assert_nil secrets.credentials_payload
      assert_equal ["password"], secrets.empty_accounts
      refute_predicate secrets, :unreadable?
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

    def test_secret_service_uses_argv_arrays_and_attributes_instead_of_the_value
      calls = []
      runner = lambda do |args|
        calls << args
        args.include?("lookup") ? [0, "sekret\n", ""] : [0, "attributes", ""]
      end
      provider = secret_service(runner: runner)

      assert_predicate provider, :available?
      assert provider.present?("email")
      assert_equal %w[search --all service walmart-cli account email], calls.first
      assert_equal "sekret", provider.read("password").value
      assert_equal %w[lookup service walmart-cli account password], calls.last
    end

    # `search` prints attributes only. `lookup` prints the value and may prompt
    # to unlock the item, so a presence check must never use it.
    def test_secret_service_presence_check_does_not_request_the_value
      calls = []
      provider = secret_service(runner: lambda { |args|
        calls << args
        [0, "", ""]
      })

      provider.present?("password")

      refute_includes calls.flatten, "lookup"
    end

    def test_secret_service_reports_a_missing_item_as_absent
      provider = secret_service(runner: ->(_args) { [1, "", "No such item at path"] })

      assert_equal :absent, provider.probe("email")
      assert_nil provider.read("email")
      assert_nil provider.last_error
    end

    # A refusal is only told apart from a missing item by the error text, and
    # that text must not be copied into last_error: secret-tool's messages are
    # not a contract, and an error string is exactly where a leaked value would
    # be printed.
    def test_secret_service_reports_a_refused_lookup_as_an_error_without_its_text
      provider = secret_service(runner: ->(_args) { [1, "", "prompt dismissed, value was sekret"] })

      assert_equal :error, provider.probe("email")
      assert_match(/failed for account email/, provider.last_error)
      refute_includes provider.last_error, "sekret"
    end

    def test_secret_service_treats_an_empty_item_as_not_a_credential
      provider = secret_service(runner: lambda { |args|
        args.include?("lookup") ? [0, "\n", ""] : [0, "attrs", ""]
      })

      assert_equal :present, provider.probe("password")
      assert_nil provider.read("password")
      assert_includes provider.empty_accounts, "password"
    end

    def test_secret_service_is_unavailable_off_linux
      provider = Secrets::SecretServiceProvider.new(
        runner: ->(_args) { [0, "x", ""] }, platform: "arm64-darwin23", env: env
      )

      refute_predicate provider, :available?
      assert_equal :absent, provider.probe("email")
      assert_nil provider.read("email")
    end

    def test_secret_service_is_unavailable_without_the_binary
      provider = Secrets::SecretServiceProvider.new(
        runner: ->(_args) { [0, "x", ""] }, platform: "x86_64-linux",
        env: { "PATH" => File.join(tmp, "empty") }
      )

      refute_predicate provider, :available?
    end

    def test_linux_defaults_to_secret_service
      secrets = Secrets.default(platform: "x86_64-linux")

      assert_equal "secret-service", secrets.provider_name
      assert_match(/secret-tool/, secrets.describe)
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
      with_binary("security") do |bin|
        Secrets::KeychainProvider.new(
          runner: runner, platform: "arm64-darwin23", env: { "PATH" => bin }
        )
      end
    end

    def secret_service(runner:)
      with_binary("secret-tool") do |bin|
        Secrets::SecretServiceProvider.new(
          runner: runner, platform: "x86_64-linux", env: { "PATH" => bin }
        )
      end
    end

    # The binary only has to exist and be executable: availability is decided
    # by finding it on PATH, and the subprocess itself is the injected runner.
    def with_binary(name)
      bin = File.join(tmp, "bin-#{name}")
      FileUtils.mkdir_p(bin)
      path = File.join(bin, name)
      File.write(path, "#!/bin/sh\nexit 0\n")
      File.chmod(0o755, path)
      yield bin
    end
  end
end
