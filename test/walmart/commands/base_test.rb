# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    # Argument parsing and the confirmation gate. Both are safety surfaces:
    # a mis-parsed switch or a mis-read prompt is how a destructive command
    # runs when nobody agreed to it.
    class BaseTest < TestCase
      class Probe < Base
        def parse(args, **kwargs) = parse_flags(args, **kwargs)
        def ask(question) = confirm!(question)
        def positive_integer(flags, default:) = positive_integer_flag(flags, :wait, default: default)
      end

      def test_positional_and_valued_flags
        positional, flags = probe.parse(%w[milk --wait 60], valued: %w[wait], max_positional: 1)

        assert_equal %w[milk], positional
        assert_equal "60", flags[:wait]
      end

      # Every command implemented today takes no positional arguments, so a
      # stray word is a typo. `walmart login logout` is the dangerous one: it
      # used to sign in while the user believed they had signed out.
      def test_stray_positional_arguments_are_refused_by_default
        error = assert_raises(UsageError) { probe.parse(%w[logout], boolean: %w[manual]) }

        assert_match(/unexpected argument: logout/, error.message)
      end

      def test_extra_positional_arguments_beyond_the_limit_are_refused
        error = assert_raises(UsageError) { probe.parse(%w[milk eggs], max_positional: 1) }

        assert_match(/unexpected argument: eggs/, error.message)
      end

      # `--wait --force` must not read as wait="--force"; that quietly drops a
      # flag the user asked for.
      def test_a_valued_flag_will_not_swallow_another_flag
        error = assert_raises(UsageError) do
          probe.parse(%w[--wait --force], valued: %w[wait], boolean: %w[force])
        end

        assert_match(/--wait needs a value/, error.message)
      end

      def test_a_valued_flag_at_the_end_of_the_line_is_refused
        error = assert_raises(UsageError) { probe.parse(%w[--wait], valued: %w[wait]) }

        assert_match(/--wait needs a value/, error.message)
      end

      # Parsing keeps the value; whether -5 is *meaningful* is the command's
      # question, and positive_integer_flag answers it.
      def test_a_negative_number_is_still_a_usable_value
        _positional, flags = probe.parse(%w[--wait -5], valued: %w[wait])

        assert_equal "-5", flags[:wait]
      end

      def test_a_positive_integer_flag_accepts_a_number
        assert_equal 60, probe.positive_integer({ wait: "60" }, default: 300)
      end

      def test_a_positive_integer_flag_uses_the_default_when_absent
        assert_equal 300, probe.positive_integer({}, default: 300)
      end

      def test_a_positive_integer_flag_rejects_nonsense
        ["abc", "", "0", "-5", "1.5", "60s"].each do |value|
          assert_raises(UsageError, "expected --wait #{value.inspect} to be refused") do
            probe.positive_integer({ wait: value }, default: 300)
          end
        end
      end

      def test_valued_flags_accept_equals_form
        _positional, flags = probe.parse(%w[--wait=60], valued: %w[wait])

        assert_equal "60", flags[:wait]
      end

      def test_boolean_flags_are_true_when_present
        _positional, flags = probe.parse(%w[--manual], boolean: %w[manual])

        assert_equal true, flags[:manual]
      end

      # `--confirm=false` reading as true is exactly the accident this tool
      # cannot afford, so a switch given a value is refused rather than coerced.
      def test_boolean_flags_reject_an_equals_value
        %w[--manual=true --manual=false --manual=0].each do |arg|
          error = assert_raises(UsageError) { probe.parse([arg], boolean: %w[manual]) }

          assert_match(/takes no value/, error.message)
        end
      end

      def test_force_with_a_value_is_also_refused
        assert_raises(UsageError) { probe.parse(%w[--force=true], boolean: %w[force]) }
      end

      def test_unknown_flags_are_a_usage_error
        assert_raises(UsageError) { probe.parse(%w[--nope], boolean: %w[manual]) }
        assert_raises(UsageError) { probe.parse(%w[--nope=1], boolean: %w[manual]) }
      end

      def test_confirm_passes_immediately_with_the_confirm_flag
        command = probe(options: { confirm: true })

        assert command.ask("Delete everything?")
      end

      def test_confirm_accepts_yes_from_an_interactive_user
        command = probe(stdin: tty_stdin("y\n"))

        assert command.ask("Delete everything?")
      end

      def test_confirm_accepts_the_word_yes
        assert probe(stdin: tty_stdin("YES\n")).ask("Delete everything?")
      end

      def test_confirm_treats_no_as_declined
        error = assert_raises(UsageError) { probe(stdin: tty_stdin("n\n")).ask("Delete?") }

        assert_equal "declined", error.code
        assert_equal ExitCodes::USAGE, error.exit_code
      end

      def test_confirm_treats_a_bare_newline_as_declined
        assert_raises(UsageError) { probe(stdin: tty_stdin("\n")).ask("Delete?") }
      end

      # A pipe that ends is not consent.
      def test_confirm_treats_eof_as_declined
        error = assert_raises(UsageError) { probe(stdin: tty_stdin("")).ask("Delete?") }

        assert_match(/no answer/, error.message)
      end

      def test_confirm_refuses_to_prompt_when_not_interactive
        error = assert_raises(UsageError) { probe.ask("Delete?") }

        assert_equal "confirmation_required", error.code
      end

      # Safety invariant 7: --json is non-interactive, always.
      def test_confirm_never_prompts_in_json_mode
        command = probe(json: true, stdin: tty_stdin("y\n"))
        error = assert_raises(UsageError) { command.ask("Delete?") }

        assert_equal "confirmation_required", error.code
      end

      private

      def probe(options: {}, stdin: StringIO.new, json: false)
        # The interactive path needs a TTY on both ends.
        def out.tty? = true
        Probe.new(build_context(options: options, stdin: stdin, json: json))
      end

      # A StringIO that claims to be a terminal, so the interactive path runs
      # without a real TTY.
      def tty_stdin(content)
        io = StringIO.new(content)
        def io.tty? = true
        io
      end
    end
  end
end
