# frozen_string_literal: true

require "test_helper"

module Walmart
  # After signing in, the CLI said "next: walmart store pin" -- a command it does
  # not implement and rejects with "unknown command". Eric hit it on the first
  # real run, following the tool's own instruction.
  #
  # It is the night's pattern once more, in the place it costs most: a
  # suggestion is a promise about the tool, and the first thing a new user is
  # told to do is the worst place to break one. Nothing checked it because a
  # string is not a call, and `PLANNED` exists precisely so `--help` can name
  # unimplemented commands honestly -- so the vocabulary was available and
  # nothing said which contexts may use it.
  #
  # Help output may name planned commands; it labels them as planned. Everything
  # else may only name commands that exist.
  class SuggestedCommandsTest < Minitest::Test
    ROOT = File.expand_path("../..", __dir__)

    # Help is where planned commands are deliberately advertised.
    ALLOWED_TO_NAME_PLANNED = ["lib/walmart/help.rb", "lib/walmart/cli.rb"].freeze

    # Words that follow "walmart " in prose rather than naming a command.
    NOT_COMMANDS = %w[command nonsense].freeze

    MENTION = /walmart ((?:[a-z][a-z-]*)(?: [a-z][a-z-]*)?)/

    def test_no_user_facing_text_suggests_a_command_that_does_not_exist
      offenders = mentions.reject { |_file, _line, path| runnable?(path) }

      assert_empty offenders.map { |file, line, path| "#{file}:#{line} names `walmart #{path}`" },
                   "the CLI is telling a user to run something it does not implement"
    end

    # The first version of this guard asked only "is it in PLANNED?", which
    # catches a command we declared and did not build, and misses a command that
    # was never declared at all. Writing `walmart browser start` produced prose
    # saying `walmart config set cdp_url ...` when no `config set` existed, and
    # this file -- written that same night, for that same bug -- said nothing.
    # An invented command is the commoner mistake, because inventing one takes
    # no prior step.
    def test_the_guard_catches_an_invented_command_not_only_a_planned_one
      refute runnable?("config teleport"), "a command nobody ever declared must not pass"
      refute runnable?("store pin"), "a declared-but-unbuilt command must not pass"
      assert runnable?("config set"), "a real command must pass"
    end

    # `walmart cart` is not runnable, and neither is `walmart config`. Prose that
    # names a group without a subcommand is still telling someone to type
    # something that returns an error.
    def test_a_bare_group_name_is_not_a_command
      refute runnable?("cart")
      refute runnable?("config")
      assert runnable?("cart show")
    end

    # The check above is only meaningful if it is finding text at all -- a
    # regex that matches nothing passes it perfectly.
    def test_the_scan_actually_finds_the_suggestions_it_is_auditing
      found = mentions.map { |_file, _line, path| path }

      assert_operator found.length, :>=, 15
      assert_includes found, "order sync"
      assert_includes found, "login"
    end

    # The specific regression, named so it cannot come back quietly.
    def test_nothing_suggests_the_unimplemented_store_pin
      refute_includes mentions.map { |_file, _line, path| path }, "store pin"
    end

    private

    # Exact membership, never a prefix. An earlier version asked whether any
    # real command *started with* these words, so `walmart cart add` passed
    # because "cart" matches "cart show" -- and `cart add` is the next command
    # we intend to build, which makes it the likeliest thing to be suggested
    # early. The check was blind to precisely the case it existed for.
    def runnable?(path)
      words = path.split
      return true if NOT_COMMANDS.include?(words.first)
      return true if words.first == "help"
      return true if CLI::COMMANDS.key?(words)

      # A two-word mention may be a real one-word command followed by an
      # argument: `walmart search oat` is `search`, not a command called
      # "search oat".
      words.length == 2 && CLI::COMMANDS.key?([words.first])
    end

    def mentions
      @mentions ||= Dir[File.join(ROOT, "lib/**/*.rb")].flat_map do |absolute|
        file = absolute.delete_prefix("#{ROOT}/")
        next [] if ALLOWED_TO_NAME_PLANNED.include?(file)

        File.readlines(absolute).each_with_index.flat_map do |line, index|
          line.scan(MENTION).map { |(path)| [file, index + 1, path.strip] }
        end
      end
    end
  end
end
