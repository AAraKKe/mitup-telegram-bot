from . import runner


def run_format(check: bool = False) -> int:
    if check:
        return runner.uv("ruff", "format", "--check", "--diff", ".")
    return runner.uv("ruff", "format", ".")


def run_lint(fix: bool = False) -> int:
    if fix:
        return runner.uv("ruff", "check", "--fix", "--unsafe-fixes", ".")
    return runner.uv("ruff", "check", ".")


def run_typecheck() -> int:
    """Type-check the root project, then the mb tool.

    tools/mb is deliberately outside the root [tool.ty.src] include: it is a standalone
    package checked under its own config (tools/mb/pyproject.toml), so each run sees
    exactly one package's first-party roots.
    """
    root_exit = runner.uv("ty", "check")
    mb_exit = runner.uv("ty", "check", cwd=runner.repo_root() / "tools/mb")
    return root_exit or mb_exit


def run_fix() -> int:
    format_exit = run_format()
    lint_exit = run_lint(fix=True)
    return format_exit or lint_exit
