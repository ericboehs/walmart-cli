# frozen_string_literal: true

require "json"
require_relative "errors"

module Walmart
  # Human tables on one side, one complete JSON document on the other.
  #
  # Two rules the rest of the codebase depends on:
  #   * in --json mode nothing but the final document reaches stdout, so status
  #     chatter goes to stderr;
  #   * an empty result says what was searched, so "no matches" can never be
  #     confused with "we were signed out" (safety invariant 10).
  class Formatter
    COLORS = {
      reset: "\e[0m", bold: "\e[1m", dim: "\e[2m",
      red: "\e[31m", green: "\e[32m", yellow: "\e[33m", blue: "\e[34m", cyan: "\e[36m"
    }.freeze

    def initialize(json: false, quiet: false, color: "auto", out: $stdout, err: $stderr)
      @json = json
      @quiet = quiet
      @color_mode = color
      @out = out
      @err = err
    end

    attr_reader :out, :err

    def json? = @json
    def quiet? = @quiet

    def color?
      case @color_mode
      when "always", true then true
      when "never", false then false
      else @out.respond_to?(:tty?) && @out.tty? && ENV["NO_COLOR"].nil?
      end
    end

    def paint(text, color)
      return text.to_s unless color? && COLORS.key?(color)

      "#{COLORS[color]}#{text}#{COLORS[:reset]}"
    end

    # The single machine-readable document.
    #
    # Enforced rather than documented: two calls would produce two concatenated
    # pretty-printed objects, which is neither JSON nor NDJSON, and the caller
    # parsing our stdout would fail in a way that looks like our data is wrong.
    def emit_json(payload)
      if @json_emitted
        raise Error.new("internal error: tried to emit a second JSON document",
                        code: "double_json_emit")
      end

      @json_emitted = true
      @out.puts(JSON.pretty_generate(payload))
    end

    def json_emitted? = !!@json_emitted

    # Ordinary output. Suppressed entirely in JSON mode.
    def say(text = "")
      return if @json

      @out.puts(text)
    end

    # Status chatter: hidden by --quiet, and routed to stderr in JSON mode so
    # it can never corrupt the document.
    def status(text)
      return if @quiet

      if @json
        @err.puts(text)
      else
        @out.puts(paint(text, :dim))
      end
    end

    def warn(text)
      @err.puts(paint("warning: #{text}", :yellow))
    end

    def error(text)
      @err.puts(paint("error: #{text}", :red))
    end

    def hint(text)
      @err.puts(paint(text, :cyan))
    end

    # Renders rows as an aligned table. +columns+ is an array of
    # [key, header, align] with align :left or :right.
    def table(rows, columns)
      return if @json || rows.empty?

      widths = columns.map do |key, header, _align|
        [header.to_s.length, *rows.map { |row| cell(row, key).length }].max
      end

      @out.puts(paint(format_row(columns.map { |_k, h, _a| h }, widths, columns), :bold))
      rows.each do |row|
        @out.puts(format_row(columns.map { |key, _h, _a| cell(row, key) }, widths, columns))
      end
    end

    # Describes an empty result honestly: what was searched, and whether the
    # emptiness is healthy.
    def empty_result(what:, where: nil, reason: nil)
      target = where ? "#{what} in #{where}" : what
      if reason
        error("no results for #{target}: #{reason}")
      else
        say("No matches for #{target}.")
      end
    end

    private

    def cell(row, key)
      value = row.is_a?(Hash) ? (row[key] || row[key.to_s]) : row.public_send(key)
      value.nil? ? "" : value.to_s
    end

    def format_row(values, widths, columns)
      values.each_with_index.map do |value, index|
        align = columns[index][2] || :left
        width = widths[index]
        align == :right ? value.to_s.rjust(width) : value.to_s.ljust(width)
      end.join("  ").rstrip
    end
  end
end
