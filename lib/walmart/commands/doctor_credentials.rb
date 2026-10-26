# frozen_string_literal: true

module Walmart
  module Commands
    # The credentials row of `walmart doctor`. Split out because Doctor was
    # already at the class-length limit, and this row has two providers with
    # different honest answers.
    module DoctorCredentials
      # Booleans and states only. This never reads, prints, or logs a value.
      def credentials_check
        return secret_tool_check if secrets.provider_name == "secret-service"

        status = secrets.status
        if status["email"] == "error" || status["password"] == "error"
          # "absent" means add a record; "error" means the store refused us and
          # adding another record will not help. Do not conflate them.
          row(status: :warn,
              detail: "#{secrets.describe}: unreadable (#{secrets.last_error})",
              hint: "the keychain refused the lookup; walmart login still works manually")
        elsif status["email_present"] && status["password_present"]
          # "present", not "set". doctor deliberately never reads a credential
          # value -- that can prompt for biometrics -- so all it can honestly
          # report is that the items exist. An item can exist and hold nothing,
          # which is what "set" claimed and login then contradicted with "no
          # stored credentials found". Presence is not usability, and the word
          # has to stop short of what the check actually looked at.
          row(status: :ok,
              detail: "#{secrets.describe}: email + password present (values not read)")
        elsif status["available"]
          row(status: :warn,
              detail: "#{secrets.describe}: incomplete " \
                      "(email #{status["email"]}, password #{status["password"]})",
              hint: "walmart login works manually; or run: walmart credentials")
        else
          row(status: :warn, detail: secrets.describe,
              hint: "manual login only on this platform")
        end
      end

      # One line, two facts: is the binary there, and do the items exist.
      # Absence of the binary is not absence of the items -- we never looked.
      def secret_tool_check
        unless secrets.available?
          return row(status: :warn, detail: "secret-tool not installed",
                     hint: "install libsecret-tools; walmart login still works manually")
        end

        status = secrets.status
        presence = "email #{status["email"]}, password #{status["password"]}"
        if status["email"] == "error" || status["password"] == "error"
          row(status: :warn,
              detail: "secret-tool installed; unreadable (#{secrets.last_error})",
              hint: "the store refused the lookup; walmart login still works manually")
        elsif status["email_present"] && status["password_present"]
          row(status: :ok, detail: "secret-tool installed; #{presence} (values not read)")
        else
          row(status: :warn,
              detail: "secret-tool installed; #{presence} (values not read)",
              hint: "run: walmart credentials")
        end
      end

      private

      def row(status:, detail:, hint: nil)
        Doctor::Check.new(name: "credentials", status: status, detail: detail, hint: hint)
      end
    end
  end
end
