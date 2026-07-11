from . import runner


def run_format(check: bool = False) -> int:
    args = ["ruff", "format", "--check", "--diff", "."] if check else ["ruff", "format", "."]
    return runner.run_command(runner.uv(*args))


def run_lint(fix: bool = False) -> int:
    args = ["ruff", "check", "--fix", "--unsafe-fixes", "."] if fix else ["ruff", "check", "."]
    return runner.run_command(runner.uv(*args))


def run_typecheck() -> int:
    """Type-check the root project, then the mb tool itself (it has its own ty config)."""
    root_exit = runner.run_command(runner.uv("ty", "check"))
    mb_exit = runner.run_command(runner.uv("ty", "check"), cwd=runner.repo_root() / "tools/mb")
    return root_exit or mb_exit


def run_fix() -> int:
    format_exit = run_format()
    lint_exit = run_lint(fix=True)
    return format_exit or lint_exit
