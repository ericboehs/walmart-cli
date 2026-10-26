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
        entries = secrets.store_entries
        if json?
          fmt.emit_json(document(entries))
        elsif entries.empty?
          fmt.say("no credential store on this platform; walmart login opens a browser")
        else
          entries.each { |entry| print_entry(entry) }
        end
        ExitCodes::OK
      end

      private

      # A refused probe prints two commands for one account. Without a label
      # they sit next to each other and look like a duplicate.
      def print_entry(entry)
        fmt.say(entry[:label]) if entry[:label]
        fmt.say(entry[:command])
      end

      def document(entries)
        body = { "provider" => secrets.provider_name,
                 "commands" => entries.map { |entry| entry[:command] } }
        labels = entries.filter_map { |entry| entry[:label] }
        body["labels"] = labels unless labels.empty?
        body
      end
    end
  end
end
