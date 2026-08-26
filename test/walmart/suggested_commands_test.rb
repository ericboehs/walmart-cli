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
      offenders = mentions.select { |_file, _line, path| planned?(path) }

      assert_empty offenders.map { |file, line, path| "#{file}:#{line} names `walmart #{path}`" },
                   "the CLI is telling a user to run something it does not implement"
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

    # Asks the precise question -- "is this a command we have declared and not
    # built" -- rather than the loose one. The first version asked whether the
    # text was *runnable*, and tolerated anything sharing a first word with a
    # real command, so `walmart cart add` passed: "cart" matches "cart show".
    # That is the next command we are going to build, which makes it the most
    # likely thing to be suggested early, and the check was blind to exactly it.
    def planned?(path)
      words = path.split
      return false if NOT_COMMANDS.include?(words.first)
      return true if CLI::PLANNED.key?(words)

      # A bare group name ("walmart store") that only planned commands live
      # under is the same promise in shorter form.
      words.length == 1 && CLI::PLANNED.keys.any? { |command| command.first == words.first } &&
        CLI::COMMANDS.keys.none? { |command| command.first == words.first }
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
