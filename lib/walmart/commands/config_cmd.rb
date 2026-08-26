# frozen_string_literal: true

require "json"
require_relative "base"

module Walmart
  module Commands
    # `walmart config show|path|init|set|unset`
    #
    # Named ConfigCmd so it cannot shadow Walmart::Config inside this namespace.
    class ConfigCmd < Base
      # Settings whose values are structured and must be written by the command
      # that understands them, not typed as a string on a command line.
      STRUCTURED = %w[store].freeze

      def run(path, args)
        case path.last
        when "show" then with_no_arguments(args) { show }
        when "path" then with_no_arguments(args) { show_path }
        when "init" then with_no_arguments(args) { init }
        when "set" then set(args)
        when "unset" then unset(args)
        else raise UsageError, "usage: walmart config show|path|init|set|unset"
        end
      end

      private

      # Rejects `walmart config show garbage` rather than ignoring the word.
      def with_no_arguments(args)
        parse_flags(args)
        yield
      end

      def set(args)
        positional, = parse_flags(args, max_positional: 2)
        key, raw = positional
        raise UsageError, "usage: walmart config set <setting> <value>" if key.nil? || raw.nil?
        raise UsageError, "#{key} is set by its own command, not by config set" if STRUCTURED.include?(key)

        config.set(key, coerce(key, raw))
        config.save
        report_setting(key)
      end

      def unset(args)
        positional, = parse_flags(args, max_positional: 1)
        key = positional.first
        raise UsageError, "usage: walmart config unset <setting>" if key.nil?

        config.set(key, Config::DEFAULTS[key.to_s])
        config.save
        report_setting(key)
      end

      def report_setting(key)
        value = config.fetch(key)
        if json?
          fmt.emit_json({ "config_file" => paths.config_file, "setting" => key, "value" => value })
        else
          fmt.status("#{key} = #{render(value)}")
          fmt.say(paths.config_file)
        end
        ExitCodes::OK
      end

      # A config file is typed; a command line is not. Without this, "false"
      # lands as a string, reads as truthy, and the browser silently stays
      # visible -- which is the exact failure Config#validate! exists to catch,
      # so the coercion answers to it rather than guessing.
      def coerce(key, raw)
        case Config::DEFAULTS[key.to_s]
        when true, false then coerce_boolean(key, raw)
        when Integer then coerce_integer(key, raw)
        else raw == "" ? nil : raw
        end
      end

      def coerce_boolean(key, raw)
        return true if %w[true yes on 1].include?(raw)
        return false if %w[false no off 0].include?(raw)

        raise UsageError, "#{key} expects true or false (got #{raw.inspect})"
      end

      def coerce_integer(key, raw)
        raise UsageError, "#{key} expects a whole number (got #{raw.inspect})" unless /\A-?\d+\z/.match?(raw)

        raw.to_i
      end

      def show
        data = config.to_h
        if json?
          fmt.emit_json({ "config" => data, "paths" => paths.to_h, "exists" => config.exist? })
        else
          fmt.say("config file: #{paths.config_file}#{" (not written yet)" unless config.exist?}")
          fmt.say
          rows = data.map { |key, value| { setting: key, value: render(value) } }
          fmt.table(rows, [[:setting, "setting"], [:value, "value"]])
        end
        ExitCodes::OK
      end

      def show_path
        if json?
          fmt.emit_json({ "config_file" => paths.config_file, "paths" => paths.to_h })
        else
          fmt.say(paths.config_file)
        end
        ExitCodes::OK
      end

      def init
        existed = config.exist?
        config.save
        if json?
          fmt.emit_json({ "config_file" => paths.config_file, "created" => !existed })
        else
          fmt.say(existed ? "rewrote #{paths.config_file}" : "created #{paths.config_file}")
        end
        ExitCodes::OK
      end

      def render(value)
        case value
        when nil then "(unset)"
        when Hash then JSON.generate(value)
        else value.to_s
        end
      end
    end
  end
end
