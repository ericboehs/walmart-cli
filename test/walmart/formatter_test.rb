# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  class FormatterTest < TestCase
    def test_json_mode_puts_only_the_document_on_stdout
      fmt = formatter(json: true)
      fmt.status("working...")
      fmt.say("this should not appear")
      fmt.emit_json({ "ok" => true })

      assert_equal({ "ok" => true }, JSON.parse(out.string))
      assert_includes err.string, "working..."
    end

    def test_quiet_suppresses_status_but_not_errors
      fmt = formatter(quiet: true)
      fmt.status("chatter")
      fmt.error("broken")

      assert_empty out.string
      assert_includes err.string, "error: broken"
    end

    def test_table_aligns_columns
      fmt = formatter
      fmt.table([{ a: "1", b: "short" }, { a: "22", b: "much longer" }],
                [[:a, "n", :right], [:b, "text"]])
      lines = out.string.lines.map(&:rstrip)

      assert_equal " n  text", lines[0]
      assert_equal " 1  short", lines[1]
      assert_equal "22  much longer", lines[2]
    end

    def test_table_is_silent_in_json_mode
      fmt = formatter(json: true)
      fmt.table([{ a: "1" }], [[:a, "n"]])

      assert_empty out.string
    end

    # Safety invariant 10: an empty result must say what was searched, and a
    # blocked read must not look like a healthy zero.
    def test_empty_result_distinguishes_healthy_from_blocked
      fmt = formatter
      fmt.empty_result(what: '"whole milk"', where: "My Items")

      assert_includes out.string, 'No matches for "whole milk" in My Items.'

      fmt.empty_result(what: '"whole milk"', reason: "signed out")

      assert_includes err.string, 'no results for "whole milk": signed out'
    end

    def test_color_is_off_when_asked
      fmt = Formatter.new(color: "never", out: out, err: err)

      assert_equal "plain", fmt.paint("plain", :red)
    end

    # Two documents on stdout is neither JSON nor NDJSON, and the caller parsing
    # our output would blame our data rather than our framing.
    def test_a_second_json_document_is_refused
      fmt = Formatter.new(json: true, out: out, err: err)
      fmt.emit_json({ "cart" => [] })

      error = assert_raises(Error) { fmt.emit_json({ "cart" => [] }) }

      assert_equal "double_json_emit", error.code
      assert_equal 1, JSON.parse(out.string).size
    end

    def test_the_first_json_document_still_lands
      fmt = Formatter.new(json: true, out: out, err: err)
      fmt.emit_json({ "ok" => true })

      assert_equal({ "ok" => true }, JSON.parse(out.string))
      assert_predicate fmt, :json_emitted?
    end
  end
end
