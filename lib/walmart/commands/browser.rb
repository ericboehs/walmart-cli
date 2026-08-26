# frozen_string_literal: true

require "uri"
require "net/http"
require_relative "base"

module Walmart
  module Commands
    # `walmart browser start` / `walmart browser status`
    #
    # Starts a Chrome that this tool can attach to, and that the user signs into
    # themselves. See DESIGN.md, "be a real browser, not a fake human": the
    # session is genuinely human because a human made it, so nothing about it
    # has to be forged.
    #
    # This command never navigates anywhere and never touches the Walmart
    # session. It launches a browser and gets out of the way. Everything that
    # follows -- signing in, clearing a challenge -- is done by the person, in
    # the window, by hand.
    class Browser < Base
      DEFAULT_PORT = 9222

      # Chrome 136+ refuses --remote-debugging-port when it would open the
      # user's default profile, because an unauthenticated debugging port on a
      # profile full of logged-in sessions is a cookie-theft primitive. A
      # separate directory is required, and is also what we want: this profile
      # holds one site, not the user's whole browsing life.
      PROFILE_DIRNAME = "attached-chrome"

      SUBCOMMANDS = %w[start status].freeze

      CHROME_PATHS = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser"
      ].freeze

      def run(path, args)
        subcommand = path[1]
        unless SUBCOMMANDS.include?(subcommand)
          raise UsageError, "unknown browser subcommand: #{subcommand || "(none)"}"
        end

        flags, positional = parse_browser_flags(args)
        raise UsageError, "unexpected argument: #{positional.first}" unless positional.empty?

        subcommand == "start" ? start(flags) : status(flags)
      end

      private

      def parse_browser_flags(args)
        positional, flags = parse_flags(args, valued: %w[port], max_positional: 0)
        [{ port: positional_integer_port(flags) }, positional]
      end

      def positional_integer_port(flags)
        positive_integer_flag(flags, :port, unit: "a TCP port", default: DEFAULT_PORT)
      end

      def start(flags)
        port = flags[:port]
        return already_running(port) if endpoint_version(port)

        chrome = chrome_binary
        raise Error, "no Google Chrome found at #{CHROME_PATHS.join(" or ")}" unless chrome

        profile = profile_path
        FileUtils.mkdir_p(profile)
        File.chmod(0o700, profile)
        spawn_chrome(chrome, port, profile)
        report_started(port, profile)
      end

      def spawn_chrome(chrome, port, profile)
        # Detached: the browser outlives this process on purpose. The user signs
        # in at their own pace, in their own time, and runs the next command
        # whenever they are ready.
        pid = Process.spawn(
          chrome,
          "--remote-debugging-port=#{port}",
          "--user-data-dir=#{profile}",
          "https://www.walmart.com/",
          out: File::NULL, err: File::NULL
        )
        Process.detach(pid)
        pid
      end

      def report_started(port, profile)
        url = "http://127.0.0.1:#{port}"
        return emit_json_result(url, profile, "started") if json?

        fmt.status("started Chrome with a debugging port on #{url}")
        fmt.say("profile: #{profile}")
        fmt.say("")
        fmt.say("Sign in to Walmart in that window yourself, and clear any")
        fmt.say("press-and-hold challenge by hand. Then point this tool at it:")
        fmt.say("")
        fmt.say("  walmart config set cdp_url #{url}")
        ExitCodes::OK
      end

      def already_running(port)
        url = "http://127.0.0.1:#{port}"
        return emit_json_result(url, profile_path, "already_running") if json?

        fmt.status("Chrome is already listening on #{url}")
        fmt.hint("walmart config set cdp_url #{url}")
        ExitCodes::OK
      end

      def status(flags)
        port = flags[:port]
        version = endpoint_version(port)
        url = "http://127.0.0.1:#{port}"
        if json?
          state = version ? "running" : "not_running"
          return emit_json_result(url, profile_path, state, version: version)
        end

        if version
          fmt.status("Chrome is listening on #{url} (#{version})")
          ExitCodes::OK
        else
          fmt.warn("nothing is listening on #{url}")
          fmt.hint("walmart browser start")
          ExitCodes::FAILED
        end
      end

      def emit_json_result(url, profile, state, version: nil)
        fmt.emit_json({ "cdp_url" => url, "profile" => profile, "state" => state, "browser" => version })
        state == "not_running" ? ExitCodes::FAILED : ExitCodes::OK
      end

      # Asks the endpoint what it is rather than assuming a listening socket is
      # Chrome. Loopback only, and never follows a redirect off it.
      def endpoint_version(port)
        uri = URI.parse("http://127.0.0.1:#{port}/json/version")
        response = Net::HTTP.start(uri.host, uri.port, open_timeout: 1, read_timeout: 2) do |http|
          http.get(uri.request_uri)
        end
        return nil unless response.is_a?(Net::HTTPSuccess)

        JSON.parse(response.body)["Browser"]
      rescue StandardError
        nil
      end

      def chrome_binary
        CHROME_PATHS.find { |candidate| File.executable?(candidate) }
      end

      def profile_path
        File.join(context.paths.data_home, PROFILE_DIRNAME)
      end
    end
  end
end
