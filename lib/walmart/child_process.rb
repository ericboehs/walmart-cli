# frozen_string_literal: true

module Walmart
  # Everything about the worker process as a *process*: how long it gets to
  # exit, and how it is stopped when it will not.
  #
  # Split out of Worker so the transport logic and the signal logic can be read
  # separately; the rules here are the difference between a clean exit and an
  # orphaned browser holding the profile lock.
  module ChildProcess
    # How long a worker gets to honor TERM before we escalate to KILL.
    KILL_GRACE_SECONDS = 5

    # Floor for the post-output exit wait when the timeout budget is already
    # spent. Closing a browser cleanly takes a moment; being late is not a
    # reason to leave one half-closed.
    EXIT_GRACE_SECONDS = 2

    private

    # The budget has to cover the child's exit, not just its output. A worker
    # that delivers its result and then wedges in Playwright's context.close()
    # -- the same stuck-Chromium case the KILL escalation exists for -- would
    # otherwise block here forever, long past worker_timeout_seconds, with
    # Ctrl-C as the only way out.
    #
    # A result already in hand still counts: execute() returns it, because a
    # cart change that reported success did happen even if cleanup had to be
    # cut short. Without a result we fall through to incomplete_error, which
    # keeps the unverified-mutation semantics.
    def await_exit(wait_thread, deadline)
      return wait_thread.value if wait_thread.join(exit_budget(deadline))

      @logger&.call(:warn, "worker finished its output but did not exit; killing it")
      kill(wait_thread)
      wait_thread.value
    end

    # Whatever is left of the budget, and never less than a moment to unwind:
    # being one millisecond late is not a reason to leave a browser half-closed.
    def exit_budget(deadline)
      remaining = deadline - Process.clock_gettime(Process::CLOCK_MONOTONIC)
      [remaining, EXIT_GRACE_SECONDS].max
    end

    # TERM first so the worker can close the browser cleanly, then KILL if it
    # ignores us. A child that swallows TERM would otherwise hang the CLI past
    # its own timeout, inside Open3's join.
    def kill(wait_thread)
      Process.kill("TERM", wait_thread.pid)
      return unless wait_thread.join(KILL_GRACE_SECONDS).nil?

      @logger&.call(:warn, "worker ignored TERM; sending KILL")
      Process.kill("KILL", wait_thread.pid)
      return unless wait_thread.join(KILL_GRACE_SECONDS).nil?

      warn_survivor(wait_thread.pid, "survived KILL")
    rescue Errno::ESRCH
      # Already gone, which is the outcome we wanted.
      nil
    rescue Errno::EPERM => e
      # Not benign: something is still running, still holding the profile lock,
      # and the next command would blame a ghost. Say so now.
      warn_survivor(wait_thread.pid, e.message)
    end

    def warn_survivor(pid, reason)
      @logger&.call(:warn, "could not stop worker #{pid} (#{reason}); a browser may still be running " \
                           "and holding the profile lock")
      nil
    end
  end
end
