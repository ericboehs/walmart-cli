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
                       "totals" => { "subtotal" => 1.0 }, "problems" => [],
                       "fulfillment" => { "intent" => "DELIVERY", "store_id" => "499",
                                          "type" => nil } },
      "order.sync" => { "orders" => [], "seen" => [], "stopped" => { "reason" => "complete" } },
      "cart.add" => { "item_id" => "1", "outcome" => "applied", "quantity_before" => 1,
                      "quantity_after" => 2, "quantity_intended" => 2, "changed" => true,
                      "message" => "" },
      "cart.remove" => { "item_id" => "1", "outcome" => "applied", "quantity_before" => 1,
                         "quantity_after" => 0, "quantity_intended" => 0, "changed" => true,
                         "message" => "" }
    }.freeze

    # One invocation per registered command, with arguments valid enough to
    # reach the rendering path -- the place the bug lived.
    INVOCATIONS = {
      %w[doctor] => [],
      %w[login] => [],
      %w[search] => ["milk"],
      %w[item] => ["10450114"],
      %w[cart show] => [],
      %w[cart add] => %w[1 --confirm],
      %w[cart remove] => %w[1 --confirm],
      %w[order sync] => [],
      %w[order list] => [],
      %w[order search] => ["milk"],
      %w[order show] => ["nope"],
      %w[config show] => [],
      %w[config path] => [],
      %w[config init] => [],
      %w[config set] => %w[headless true],
      %w[config unset] => %w[headless],
      # Reads a loopback port that is not listening, which is the honest state
      # on a machine with no browser started. `start` is deliberately absent:
      # covering it would mean spawning a real Chrome from the suite.
      %w[browser status] => [],
      %w[logout] => [],
      # pin reads the same cart.show stub above, which names a store.
      %w[store show] => [],
      %w[store pin] => [],
      %w[version] => []
    }.freeze

    # Exercising this would spawn a real detached Chrome from the test suite.
    # Excluded on purpose, and the exclusion is checked below so it cannot
    # quietly become a place to park a command nobody wanted to cover.
    UNCOVERABLE = { %w[browser start] => "spawns a real browser process" }.freeze

    def test_every_registered_command_is_covered_here
      registered = CLI::COMMANDS.keys.map { |path| path }
      missing = registered - INVOCATIONS.keys - UNCOVERABLE.keys

      assert_empty missing, "add an invocation for #{missing.inspect}, or this proves nothing " \
                            "about the command someone just added"
    end

    # An exemption that outlives the command it exempts is a hole with a comment
    # over it.
    def test_every_exemption_still_names_a_real_command_and_says_why
      UNCOVERABLE.each do |path, reason|
        assert CLI::COMMANDS.key?(path), "#{path.join(" ")} is exempted but no longer registered"
        refute_empty reason.to_s
        refute INVOCATIONS.key?(path), "#{path.join(" ")} is both covered and exempted"
      end
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
