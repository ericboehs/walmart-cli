# frozen_string_literal: true

require "test_helper"

module Walmart
  # Whatever a command returns becomes the process's exit status, so returning
  # anything but an Integer is a crash at Kernel#exit -- outside every rescue,
  # after the output has already been printed.
  #
  # That shipped: item and cart show both returned nil in JSON mode for their
  # whole existence, past 350 tests, because a test that asserts on stdout is
  # not looking at the return value at all. Fixing the two commands does not
  # stop the next one from doing it, so this asserts the property across every
  # registered command instead of per command.
  #
  # Deliberately not routed through CLI.run: that normalises a bad return into
  # ExitCodes::FAILED, which is right for a user and useless for a test, since
  # it would turn the bug being hunted into a passing integer.
  class ExitCodeContractTest < TestCase
    # Enough of a reply for each command to render something. Shapes come from
    # the parsers' own tests; the point here is the return value, not the words.
    RESULTS = {
      "probe" => { "python" => "3.10", "playwright" => "1.47", "browser" => "chromium",
                   "profile_exists" => true },
      "session.status" => { "signed_in" => true },
      "login" => { "signed_in" => true },
      "search" => { "items" => [{ "item_id" => "1", "name" => "Milk", "price" => 1.0,
                                  "first_party" => true, "availability" => "IN_STOCK" }],
                    "pagination" => { "page" => 1 } },
      "item" => { "item_id" => "1", "name" => "Milk", "price" => 1.0, "first_party" => true,
                  "seller_name" => "Walmart.com", "seller_type" => "INTERNAL",
                  "availability" => "IN_STOCK",
                  "variants" => { "has_variants" => false, "count" => 0 } },
      "cart.show" => { "lines" => [{ "item_id" => "1", "name" => "Milk", "quantity" => 1,
                                     "price" => 1.0 }],
                       "totals" => { "subtotal" => 1.0 }, "problems" => [] },
      "order.sync" => { "orders" => [], "seen" => [], "stopped" => { "reason" => "complete" } }
    }.freeze

    # One invocation per registered command, with arguments valid enough to
    # reach the rendering path -- the place the bug lived.
    INVOCATIONS = {
      %w[doctor] => [],
      %w[login] => [],
      %w[search] => ["milk"],
      %w[item] => ["10450114"],
      %w[cart show] => [],
      %w[order sync] => [],
      %w[order list] => [],
      %w[order search] => ["milk"],
      %w[order show] => ["nope"],
      %w[config show] => [],
      %w[config path] => [],
      %w[config init] => [],
      %w[logout] => [],
      %w[version] => []
    }.freeze

    def test_every_registered_command_is_covered_here
      registered = CLI::COMMANDS.keys.map { |path| path }
      missing = registered - INVOCATIONS.keys

      assert_empty missing, "add an invocation for #{missing.inspect}, or this proves nothing " \
                            "about the command someone just added"
    end

    def test_no_command_returns_something_kernel_exit_cannot_take
      INVOCATIONS.each do |path, args|
        [false, true].each do |json|
          code = invoke(path + args, json: json)

          assert_kind_of Integer, code,
                         "`walmart #{(path + args).join(" ")}#{" --json" if json}` returned " \
                         "#{code.inspect}; that reaches Kernel#exit as a TypeError"
        end
      end
    end

    private

    # CLI#run, not CLI.run: the instance method returns what the command
    # returned, while the class method is the thing that repairs it.
    def invoke(words, json:)
      argv = words + (json ? ["--json"] : [])
      context = build_context(worker: FakeWorker.new(results: RESULTS), json: json)
      CLI.new(argv, stdout: out, stderr: err, env: env, context: context).run
    rescue Error => e
      # A command that refuses is fine here; a command that returns junk is not.
      e.exit_code
    end
  end
end
