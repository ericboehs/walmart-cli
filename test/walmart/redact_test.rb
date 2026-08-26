# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # Worker stderr is text we did not author: a traceback can quote a URL, a
  # header, or page content. We surface a tail of it on failure, so it has to
  # go through here first.
  class RedactTest < TestCase
    def test_emails_are_replaced
      assert_equal "sent to [REDACTED-EMAIL]", Redact.text("sent to eric.person@gmail.com")
    end

    def test_phone_numbers_are_replaced
      ["918-555-0134", "(918) 555-0134", "918.555.0134"].each do |number|
        refute_includes Redact.text("call #{number}"), number
      end
    end

    def test_card_numbers_are_replaced
      redacted = Redact.text("card 4111 1111 1111 1111 on file")

      refute_includes redacted, "4111 1111 1111 1111"
      assert_includes redacted, "[REDACTED-CARD]"
      assert_includes redacted, "card "
    end

    # A traceback quoting an environment dump is the realistic way these reach
    # a terminal. pyworker/scrub.py catches both; so must this side.
    def test_aws_access_keys_are_replaced
      redacted = Redact.text("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")

      refute_includes redacted, "AKIAIOSFODNN7EXAMPLE"
      assert_includes redacted, "[REDACTED-AWS-KEY]"
    end

    def test_temporary_aws_keys_are_replaced
      refute_includes Redact.text("ASIAIOSFODNN7EXAMPLE"), "ASIAIOSFODNN7EXAMPLE"
    end

    def test_private_key_blocks_are_replaced
      ["-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----", # gitleaks:allow
       "-----BEGIN OPENSSH PRIVATE KEY-----"].each do |header|
        redacted = Redact.text("read #{header} from disk")

        refute_includes redacted, "PRIVATE KEY-----"
        assert_includes redacted, "[REDACTED-PRIVATE-KEY]"
      end
    end

    def test_bearer_tokens_and_jwts_are_replaced
      jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnopqrstuv"

      refute_includes Redact.text("Authorization: Bearer #{jwt}"), jwt
    end

    def test_password_assignments_are_replaced
      refute_includes Redact.text('password="hunter2"'), "hunter2"
      refute_includes Redact.text("password: hunter2"), "hunter2"
    end

    def test_cookie_headers_are_replaced
      refute_includes Redact.text("Set-Cookie: CID=abc123"), "abc123"
    end

    def test_ordinary_diagnostics_survive_intact
      message = "TimeoutError: waiting for selector 'button.add-to-cart' failed"

      assert_equal message, Redact.text(message)
    end

    def test_tail_keeps_only_the_last_lines
      lines = (1..10).map { |n| "line #{n}" }

      assert_equal ["line 8", "line 9", "line 10"], Redact.tail(lines, limit: 3)
    end

    def test_tail_redacts_each_line_it_keeps
      tail = Redact.tail(["noise", "user eric@example.com failed"], limit: 2)

      refute_includes tail.join, "eric@example.com"
    end

    def test_tail_handles_nil_and_empty
      assert_empty Redact.tail(nil)
      assert_empty Redact.tail([])
    end
  end

  class WhichTest < TestCase
    def test_finds_an_executable_on_the_injected_path
      dir = File.join(tmp, "bin")
      FileUtils.mkdir_p(dir)
      tool = File.join(dir, "fzf")
      File.write(tool, "#!/bin/sh\n")
      File.chmod(0o755, tool)

      assert_equal tool, Which.find("fzf", env: { "PATH" => dir })
    end

    def test_returns_nil_when_missing
      assert_nil Which.find("fzf", env: { "PATH" => File.join(tmp, "empty") })
    end

    def test_ignores_a_directory_with_the_right_name
      dir = File.join(tmp, "bin")
      FileUtils.mkdir_p(File.join(dir, "fzf"))

      assert_nil Which.find("fzf", env: { "PATH" => dir })
    end

    def test_ignores_a_non_executable_file
      dir = File.join(tmp, "bin")
      FileUtils.mkdir_p(dir)
      File.write(File.join(dir, "fzf"), "not executable")
      File.chmod(0o644, File.join(dir, "fzf"))

      assert_nil Which.find("fzf", env: { "PATH" => dir })
    end

    def test_handles_an_empty_or_missing_path
      assert_nil Which.find("fzf", env: {})
      assert_nil Which.find("fzf", env: { "PATH" => "" })
      assert_nil Which.find(nil, env: { "PATH" => "/usr/bin" })
    end
  end
end
