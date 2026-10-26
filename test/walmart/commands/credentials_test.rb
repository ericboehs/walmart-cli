# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    class CredentialsTest < TestCase
      def test_macos_prints_the_create_form_when_nothing_is_stored
        run_credentials(secrets: keychain_secrets({}))

        assert_includes out.string, "security add-generic-password -s walmart-cli -a email -w"
        assert_includes out.string, "security add-generic-password -s walmart-cli -a password -w"
        refute_includes out.string, "-U"
        refute_includes out.string, "secret-tool"
      end

      # Without -U, `security` refuses to add an item that is already there,
      # which is the empty-password case: the record exists and holds nothing.
      # The probe that decides this must not ask for the value.
      def test_an_existing_keychain_item_is_printed_as_an_update
        calls = []
        run_credentials(secrets: keychain_secrets({ "email" => "" }, calls: calls))

        assert_includes out.string, "security add-generic-password -U -s walmart-cli -a email -w"
        assert_includes out.string, "security add-generic-password -s walmart-cli -a password -w"
        refute_includes out.string, "-U -s walmart-cli -a password"
        refute calls.flatten.include?("-w"), "printing the command read a credential"
      end

      # A refused probe is not "absent". Guessing the create form hands back a
      # command `security` will refuse if the item is in fact there.
      def test_an_unreadable_keychain_item_prints_both_forms
        calls = []
        run_credentials(secrets: keychain_secrets({}, calls: calls, errors: %w[email]))

        create = "email, create (probe refused)\nsecurity add-generic-password -s walmart-cli -a email -w"
        update = "email, update (probe refused)\nsecurity add-generic-password -U -s walmart-cli -a email -w"
        assert_includes out.string, create
        assert_includes out.string, update
        refute_includes out.string, "email -w\nsecurity add-generic-password -U"
        assert_includes out.string, "security add-generic-password -s walmart-cli -a password -w"
        refute_includes out.string, "password, create"
        refute_includes out.string, "-U -s walmart-cli -a password"
        refute calls.flatten.include?("-w"), "printing the command read a credential"
      end

      def test_an_unreadable_keychain_item_is_both_forms_in_json
        run_credentials(secrets: keychain_secrets({}, errors: %w[email]), json: true)
        document = JSON.parse(out.string)
        commands = document["commands"]

        assert_includes commands, "security add-generic-password -s walmart-cli -a email -w"
        assert_includes commands, "security add-generic-password -U -s walmart-cli -a email -w"
        refute_includes commands, "security add-generic-password -U -s walmart-cli -a password -w"
        assert_equal ["email, create (probe refused)", "email, update (probe refused)"], document["labels"]
        refute(commands.any? { |command| command.include?("probe refused") })
      end

      def test_linux_prints_the_secret_tool_store_commands
        run_credentials(secrets: Secrets.default(platform: "x86_64-linux"))

        assert_includes out.string, "secret-tool store --label='walmart-cli email'"
        assert_includes out.string, "account password"
        refute_includes out.string, "security "
      end

      def test_a_platform_with_no_store_says_so_instead_of_printing_nothing
        code = run_credentials(secrets: Secrets.default(platform: "sparc-solaris"))

        assert_equal ExitCodes::OK, code
        assert_includes out.string, "no credential store"
        assert_includes out.string, "walmart login"
      end

      def test_json_is_a_document_and_never_a_secret
        run_credentials(secrets: Secrets.default(platform: "x86_64-linux"), json: true)
        document = JSON.parse(out.string)

        assert_equal "secret-service", document["provider"]
        assert_equal 2, document["commands"].length
        refute_includes out.string, "hunter2"
      end

      def test_an_extra_argument_is_refused
        assert_raises(UsageError) { run_credentials(args: ["please"]) }
      end

      private

      def run_credentials(secrets: Secrets.default(platform: "arm64-darwin23"), json: false, args: [])
        context = build_context(secrets: secrets, json: json)
        Credentials.new(context).run(%w[credentials], args)
      end

      # A keychain provider that never calls the real `security`. +present+
      # maps account name to a stored value; the value is not what we print.
      def keychain_secrets(present, calls: [], errors: [])
        bin = File.join(tmp, "bin-security")
        FileUtils.mkdir_p(bin)
        path = File.join(bin, "security")
        File.write(path, "#!/bin/sh\nexit 0\n")
        File.chmod(0o755, path)
        runner = lambda do |args|
          calls << args
          account = args[args.index("-a") + 1]
          next [1, ""] if errors.include?(account)

          present.key?(account) ? [0, "attributes"] : [44, ""]
        end
        provider = Secrets::KeychainProvider.new(
          runner: runner, platform: "arm64-darwin23", env: { "PATH" => bin }
        )
        Secrets.new(provider: provider)
      end
    end
  end
end
