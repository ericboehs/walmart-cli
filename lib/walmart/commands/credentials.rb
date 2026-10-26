# frozen_string_literal: true

require_relative "base"

module Walmart
  module Commands
    # `walmart credentials` - print the store commands for this platform.
    #
    # It never reads a value and never writes one. Creating the record is a
    # prompt in the platform's own tool, where a password belongs.
    class Credentials < Base
      def run(_path, args)
        parse_flags(args)
        commands = secrets.store_commands
        if json?
          fmt.emit_json({ "provider" => secrets.provider_name, "commands" => commands })
        elsif commands.empty?
          fmt.say("no credential store on this platform; walmart login opens a browser")
        else
          commands.each { |line| fmt.say(line) }
        end
        ExitCodes::OK
      end
    end
  end
end
