# frozen_string_literal: true

require "json"
require_relative "base"

module Walmart
  module Commands
    # `walmart config show|path|init`
    #
    # Named ConfigCmd so it cannot shadow Walmart::Config inside this namespace.
    class ConfigCmd < Base
      def run(path, args)
        # Rejects `walmart config show garbage` rather than ignoring the word.
        parse_flags(args)

        case path.last
        when "show" then show
        when "path" then show_path
        when "init" then init
        else raise UsageError, "usage: walmart config show|path|init"
        end
      end

      private

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
