# frozen_string_literal: true

require "test_helper"

module Walmart
  # positive_integer_flag once hardcoded "seconds" into its error, which was
  # true of its first caller and wrong for the four that followed. The unit is
  # a required argument now, so a new caller cannot silently inherit someone
  # else's -- but it can still pass the wrong one, and a wrong unit reads
  # perfectly well at the call site.
  #
  # So the units are read out of the source and checked against what each flag
  # actually counts. Textual, deliberately: importing the commands and calling
  # them would test that the plumbing works, not that "--page" counts pages.
  class FlagUnitsTest < Minitest::Test
    ROOT = File.expand_path("../..", __dir__)

    EXPECTED = {
      "lib/walmart/commands/login.rb" => { wait: "seconds" },
      "lib/walmart/commands/order_read.rb" => { limit: "orders" },
      "lib/walmart/commands/search.rb" => { page: "pages", limit: "results" },
      "lib/walmart/commands/browser.rb" => { port: "a TCP port" }
    }.freeze

    CALL = /positive_integer_flag\(\s*flags,\s*:(\w+),\s*unit:\s*"([^"]+)"/

    def test_every_flag_counts_what_it_says_it_counts
      EXPECTED.each do |file, expected|
        found = File.read(File.join(ROOT, file)).scan(CALL).to_h { |name, unit| [name.to_sym, unit] }

        assert_equal expected, found, "#{file}: a flag counts something other than what it measures"
      end
    end

    # The completeness half: a sixth caller added to a file not listed above, or
    # to a listed file without updating EXPECTED, fails here by name.
    def test_every_caller_in_the_tree_is_accounted_for
      files = Dir[File.join(ROOT, "lib/**/*.rb")].select do |path|
        # A call, not the definition: the definition names its parameter
        # `flags` too, so a bare substring matches base.rb and always failed.
        File.read(path).match?(/positive_integer_flag\(\s*flags,\s*:/)
      end
      relative = files.map { |path| path.delete_prefix("#{ROOT}/") }.sort

      assert_equal EXPECTED.keys.sort, relative,
                   "a command calls positive_integer_flag and its unit is unchecked"
    end

    # --year deliberately does not use the helper: it takes a year, not a count
    # of things, and borrowing the message produced "a positive whole number of
    # a year". If someone routes it back through, this says why not.
    def test_the_year_flag_does_not_borrow_a_counting_message
      source = File.read(File.join(ROOT, "lib/walmart/commands/order.rb"))

      refute_match(/positive_integer_flag\(\s*flags,\s*:year/, source)
      assert_includes source, "--year expects a four-digit year"
    end
  end
end
