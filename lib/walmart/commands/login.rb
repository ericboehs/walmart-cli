# frozen_string_literal: true

require "fileutils"
require_relative "base"

module Walmart
  module Commands
    # `walmart login [--manual] [--force] [--wait SECONDS]` and `walmart logout`.
    #
    # Login is the one command that opens a window. Stored credentials, when
    # present, are typed into the page by the worker; every human-verification
    # step is finished by the human in that window. Observed live: two
    # press-and-hold challenges gate the form, and a texted code may follow.
    # Automating any of that is neither possible nor desirable here.
    class Login < Base
      DEFAULT_WAIT_SECONDS = 300

      def run(path, args)
        return logout(args) if path.last == "logout"

        login(args)
      end

      private

      def login(args)
        _positional, flags = parse_flags(args, boolean: %w[manual force], valued: %w[wait])
        manual = flags[:manual] == true
        wait = positive_integer_flag(flags, :wait, unit: "seconds", default: DEFAULT_WAIT_SECONDS)

        credentials = manual ? nil : secrets.credentials_payload
        announce(manual, credentials)
        data = worker.run(
          "login",
          params: { "manual" => manual, "force" => flags[:force] == true, "wait_seconds" => wait },
          secrets: credentials,
          options: { "headless" => false },
          # Generous: the human may be clearing one or more press-and-hold
          # challenges in this window, and possibly typing a texted code.
          timeout: wait + 120
        ) { |event| render_event(event) }

        report(data)
      end

      # Says what we are about to do without ever naming a credential value.
      #
      # "Absent" and "unreadable" must not read the same here. A locked or
      # denied keychain looks exactly like an empty one from credentials_payload
      # (both nil), and telling that user "no stored credentials found" sends
      # them to type a password by hand when the real fix is unlocking the
      # keychain.
      def announce(manual, credentials)
        if manual
          fmt.status("opening a visible browser for manual sign-in")
        elsif credentials
          fmt.status("opening a visible browser; stored credentials will be filled in")
        elsif secrets.empty_accounts.any?
          announce_empty_entries
        elsif secrets.unreadable?
          fmt.warn("credential store unreadable (#{secrets.last_error}); " \
                   "falling back to manual sign-in")
          fmt.hint("unlock the keychain and try again to have credentials filled in")
        else
          fmt.status("no stored credentials found; opening a visible browser for manual sign-in")
          fmt.hint("optional: security add-generic-password -s walmart-cli -a email -w")
        end
      end

      # Warnings and errors from the worker are not chatter: during a 300-second
      # visible login they are the only explanation the user gets for why the
      # window is sitting there. Only routine progress is gated behind --verbose.
      def render_event(event)
        case event["type"]
        when "progress" then fmt.status(event["message"].to_s)
        when "log"
          level = event["level"].to_s
          fmt.status(event["message"].to_s) if verbose? || %w[warn error].include?(level)
        end
      end

      # Distinct from "absent". The item is there and holds nothing, so
      # "no stored credentials found" would send someone to create a record they
      # already have -- which is how it was reported before, and the advice
      # quietly did not work.
      def announce_empty_entries
        accounts = secrets.empty_accounts.sort
        fmt.warn("the keychain #{accounts.join(" and ")} entry is empty; " \
                 "falling back to manual sign-in")
        fmt.hint("set it with: security add-generic-password -U -s walmart-cli " \
                 "-a #{accounts.first} -w")
      end

      def report(data)
        if json?
          fmt.emit_json({ "login" => data })
        elsif data["signed_in"]
          fmt.say(fmt.paint("signed in", :green))
          # Only ever name a command that exists. This said "next: walmart
          # store pin" for as long as store pin has been unimplemented, so the
          # first thing a new user was told to do after signing in was run a
          # command the CLI rejects. A suggestion is a promise about the tool.
          fmt.say("next: walmart search \"oat milk\", or walmart order sync to build the archive")
        else
          fmt.error("sign-in did not complete: #{Array(data["notes"]).join("; ")}")
        end

        data["signed_in"] ? ExitCodes::OK : ExitCodes::FAILED
      end

      def logout(args)
        _positional, _flags = parse_flags(args)
        profile = paths.browser_profile

        unless File.directory?(profile)
          fmt.emit_json({ "logout" => { "removed" => false, "reason" => "no session" } }) if json?
          fmt.say("no stored session") unless json?
          return ExitCodes::OK
        end

        confirm!("Delete the stored Walmart browser session?")
        FileUtils.rm_rf(profile)
        FileUtils.rm_f(paths.profile_lock)

        # Verify, rather than trust rm_rf. A profile that survived deletion but
        # was reported as gone would leave a signed-in session on disk while the
        # user believes they signed out.
        if File.exist?(profile)
          raise Error.new(
            "could not delete the stored session at #{profile}; it is still there",
            code: "logout_failed"
          )
        end

        if json?
          fmt.emit_json({ "logout" => { "removed" => true, "profile" => profile } })
        else
          fmt.say("removed #{profile}")
        end
        ExitCodes::OK
      end
    end
  end
end
