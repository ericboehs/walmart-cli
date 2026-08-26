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
    #
    # Signals go to the process *group*, not the pid. The worker is not a leaf:
    # it drives Playwright, which runs a Chromium tree. Killing only the parent
    # leaves that browser running, reparented to init, still holding Chromium's
    # own SingletonLock inside the persistent profile -- while our flock says
    # the profile is free. The next command then fails deep inside Playwright
    # instead of at our guard, so the user gets a stack trace rather than the
    # sentence we wrote for exactly this case, and there is no `walmart`
    # process in ps for them to find.
    def kill(wait_thread)
      target = signal_target(wait_thread.pid)
      return if target.nil?

      Process.kill("TERM", target)
      return unless wait_thread.join(KILL_GRACE_SECONDS).nil?

      @logger&.call(:warn, "worker ignored TERM; sending KILL")
      Process.kill("KILL", target)
      return unless wait_thread.join(KILL_GRACE_SECONDS).nil?

      warn_survivor(target, "survived KILL")
    rescue Errno::ESRCH
      # Already gone, which is the outcome we wanted.
      nil
    rescue Errno::EPERM => e
      # Not benign: something is still running, still holding the profile lock,
      # and the next command would blame a ghost. Say so now.
      warn_survivor(target, e.message)
    end

    # A negative pid signals the whole group. Returns nil when there is nothing
    # to signal.
    #
    # The guard against signalling our own group is the important line here:
    # if the spawn did not get its own group, the child shares ours, and
    # `kill(-our_group)` would take down the user's shell pipeline -- their
    # terminal, not just our worker. Refusing to escalate is the smaller
    # failure of the two.
    def signal_target(pid)
      return nil if pid.nil?

      group = Process.getpgid(pid)
      return -group unless group == Process.getpgrp

      @logger&.call(:warn, "worker shares this process group; signalling it alone")
      pid
    rescue Errno::ESRCH
      nil
    end

    def warn_survivor(target, reason)
      what = target.to_i.negative? ? "worker process group #{target.abs}" : "worker #{target}"
      @logger&.call(:warn, "could not stop #{what} (#{reason}); a browser may still be running " \
                           "and holding the profile lock")
      nil
    end
  end
end
