# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    class CredentialsTest < TestCase
      def test_macos_prints_the_keychain_store_commands
        run_credentials(secrets: Secrets.default(platform: "arm64-darwin23"))

        assert_includes out.string, "security add-generic-password -s walmart-cli -a email -w"
        assert_includes out.string, "security add-generic-password -s walmart-cli -a password -w"
        refute_includes out.string, "secret-tool"
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
    end
  end
end
