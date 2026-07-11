from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import typer
from rich.table import Table

from . import checks, runner, testing

Gate = tuple[str, Callable[[], int]]


@dataclass(frozen=True)
class GateResult:
    name: str
    exit_code: int

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def run_gates(gates: list[Gate]) -> list[GateResult]:
    """Run every gate even when earlier ones fail, so one broken check cannot mask another."""
    return [GateResult(name, gate()) for name, gate in gates]


def overall_exit_code(results: list[GateResult]) -> int:
    return 0 if all(result.passed for result in results) else 1


def summary_table(results: list[GateResult]) -> Table:
    table = Table(title="Validation summary")
    table.add_column("Gate")
    table.add_column("Result")
    table.add_column("Exit code", justify="right")
    for result in results:
        status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
        table.add_row(result.name, status, str(result.exit_code))
    return table


def standard_gates() -> list[Gate]:
    return [
        ("format", lambda: checks.run_format(check=True)),
        ("lint", checks.run_lint),
        ("typecheck", checks.run_typecheck),
        ("tests", lambda: testing.run_tests([], cov=True)),
    ]


def extended_gates() -> list[Gate]:
    return [
        ("db tests", lambda: testing.run_tests([], db=True)),
        ("locales", lambda: runner.run_command(runner.uv("mitup", "translations", "validate-locales"))),
        ("migrations", lambda: runner.run_command(runner.uv("mitup", "validate-migrations"))),
    ]


def validate_command(
    run_all: Annotated[bool, typer.Option("--all", help="Also run db tests, locale and migration validation.")] = False,
):
    """Run every quality gate without stopping at the first failure, then summarize."""
    gates = standard_gates() + (extended_gates() if run_all else [])
    results = run_gates(gates)
    runner.console.print(summary_table(results))
    raise typer.Exit(overall_exit_code(results))
