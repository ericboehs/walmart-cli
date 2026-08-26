# frozen_string_literal: true

require_relative "../errors"

module Walmart
  module Commands
    # Shared plumbing for every command.
    class Base
      def initialize(context)
        @context = context
      end

      attr_reader :context

      def config    = context.config
      def paths     = context.paths
      def fmt       = context.formatter
      def secrets   = context.secrets
      def worker    = context.worker
      def json?     = context.json?
      def verbose?  = context.verbose?

      def run(_path, _args)
        raise NotImplementedError
      end

      private

      # Parses `--flag=value` / `--flag value` pairs out of an argument list.
      # Returns [positional, flags]. Unknown flags are a usage error, because
      # silently ignoring a typo'd safety flag is how accidents happen.
      #
      # +max_positional+ defaults to zero: every command implemented today takes
      # no positional arguments, so a stray word is a typo. `walmart login
      # logout` is the one that matters — it used to sign in while the user
      # believed they had signed out.
      def parse_flags(args, valued: [], boolean: [], max_positional: 0)
        positional = []
        flags = {}
        queue = args.dup

        while (arg = queue.shift)
          if arg.start_with?("--") && arg.include?("=")
            name, value = arg[2..].split("=", 2)
            assert_known!(name, valued, boolean)
            # `--confirm=false` reading as true is precisely the accident this
            # tool cannot afford, so a boolean with a value is refused outright.
            if boolean.include?(name)
              raise UsageError, "--#{name} is a switch and takes no value; use --#{name} on its own"
            end

            flags[name.to_sym] = value
          elsif arg.start_with?("--")
            name = arg[2..]
            assert_known!(name, valued, boolean)
            flags[name.to_sym] = valued.include?(name) ? take_value!(name, queue) : true
          else
            positional << arg
          end
        end

        assert_positional!(positional, max_positional)
        [positional, flags]
      end

      # `--wait --force` must not read as wait="--force": that silently drops a
      # flag the user asked for and gives the other one a nonsense value.
      def take_value!(name, queue)
        value = queue.shift
        if value.nil? || value.start_with?("--")
          raise UsageError, "--#{name} needs a value#{" (got #{value})" if value}"
        end

        value
      end

      def assert_positional!(positional, allowed)
        return if positional.length <= allowed

        extra = positional.drop(allowed)
        raise UsageError, "unexpected argument#{"s" if extra.length > 1}: #{extra.join(" ")}"
      end

      def assert_known!(name, valued, boolean)
        return if valued.include?(name) || boolean.include?(name)

        raise UsageError, "unknown flag: --#{name}"
      end

      # `--wait abc` used to become 0, and the user then read "no signed-in
      # session after 0s" and blamed the worker. A flag whose value we cannot
      # understand is a usage error, not a zero.
      def positive_integer_flag(flags, name, default:)
        raw = flags[name]
        return default if raw.nil? || raw == true

        unless raw.to_s.match?(/\A\d+\z/) && raw.to_s.to_i.positive?
          raise UsageError, "--#{name} needs a positive whole number of seconds (got #{raw.inspect})"
        end

        raw.to_s.to_i
      end

      # Confirmation gate for destructive work. --json never prompts; a
      # non-interactive run without --confirm is a usage error, not a guess.
      #
      # EOF (a closed stdin) counts as "no". A pipe that ends is not consent.
      def confirm!(question)
        return true if context.confirm?

        unless context.interactive?
          raise UsageError.new("#{question} requires --confirm in non-interactive mode",
                               code: "confirmation_required")
        end

        fmt.out.print("#{question} [y/N] ")
        answer = context.stdin.gets
        raise UsageError.new("declined (no answer)", code: "declined") if answer.nil?
        return true if %w[y yes].include?(answer.strip.downcase)

        raise UsageError.new("declined", code: "declined")
      end
    end
  end
end
