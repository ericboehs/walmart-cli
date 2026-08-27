# frozen_string_literal: true

require "fileutils"

require "rake/testtask"

PYWORKER = File.expand_path("pyworker", __dir__)

Rake::TestTask.new(:test) do |t|
  t.libs << "test" << "lib"
  t.test_files = FileList["test/**/*_test.rb"]
  t.warning = false
end

desc "Run RuboCop"
task :rubocop do
  sh "rubocop"
end

desc "Run the Python worker tests"
task :pytest do
  # Bytecode off, and not as a tidiness preference.
  #
  # An edit that lands in the same second as an import can leave a .pyc that
  # Python considers current, and the suite then reports on code that no longer
  # exists. It happened here: a fix to _read_label was already on disk while the
  # test kept failing against the previous version. The mutation harness rewrites
  # files far faster than once a second, so a mutant could be judged against
  # stale bytecode and reported caught or survived either way.
  sh "cd #{PYWORKER} && PYTHONDONTWRITEBYTECODE=1 uv run --quiet pytest"
end

desc "Lint and format-check the Python worker and scripts"
task :ruff do
  sh "cd #{PYWORKER} && uv run --quiet ruff check .."
  sh "cd #{PYWORKER} && uv run --quiet ruff format --check .."
end

desc "Install Python worker dependencies"
task :setup do
  sh "cd #{PYWORKER} && uv sync"
end

desc "Scan the repository for committed secrets"
task :gitleaks do
  sh "gitleaks detect --no-banner --redact" do |ok, _res|
    warn("gitleaks not installed or found findings; see output above") unless ok
  end
end

desc "Run the CLI end to end against a stub worker and check the bytes"
task :smoke do
  sh "ruby #{File.expand_path("script/smoke", __dir__)}"
end

# Not in ci: it runs the suite once per mutant, and it edits tracked files while
# it works. It is the check you run when you have changed a guard, and the one
# that answers "would anything have noticed?".
desc "Break each guard on purpose and check some suite fails"
task :mutants do
  script = File.expand_path("script/mutate", __dir__)
  plan = File.expand_path("script/mutants.json", __dir__)
  sh "ruby #{script} --plan #{plan} -- rake test"
end

desc "Everything CI runs"
task ci: %i[rubocop ruff test pytest smoke]

task default: :ci

desc "install the git hook that runs the CI task before each commit"
task :hooks do
  hooks = File.join(__dir__, ".git", "hooks")
  FileUtils.mkdir_p(hooks)
  source = File.join(__dir__, "script", "hooks", "pre-commit")
  target = File.join(hooks, "pre-commit")
  FileUtils.cp(source, target)
  FileUtils.chmod(0o755, target)
  puts "installed #{target} (runs `rake`: rubocop, ruff, test, pytest, smoke)"
  puts "gitleaks and mutants stay separate; run them yourself"
end
