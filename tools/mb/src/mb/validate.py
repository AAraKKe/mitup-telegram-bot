from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import typer
from rich.table import Table

from . import checks, console, locales_ops, migrate_ops, testing

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
    results: list[GateResult] = []
    for index, (name, gate) in enumerate(gates, start=1):
        console.step(f"({index}/{len(gates)}) {name}")
        result = GateResult(name, gate())
        if result.passed:
            console.success(name)
        else:
            console.error(f"{name} failed (exit code {result.exit_code}).")
        results.append(result)
    return results


def overall_exit_code(results: list[GateResult]) -> int:
    return 0 if all(result.passed for result in results) else 1


def summary_table(results: list[GateResult]) -> Table:
    table = console.styled_table("Validation summary")
    table.add_column("Gate")
    table.add_column("Result")
    table.add_column("Exit code", justify="right")
    for result in results:
        table.add_row(result.name, console.status_cell(result.passed), str(result.exit_code))
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
        ("locales", locales_ops.ensure_all_translations),
        ("migrations", migrate_ops.validate_migration_graph),
    ]


def validate_command(
    run_all: Annotated[bool, typer.Option("--all", help="Also run db tests, locale and migration validation.")] = False,
):
    """Run every quality gate without stopping at the first failure, then summarize."""
    gates = standard_gates() + (extended_gates() if run_all else [])
    results = run_gates(gates)
    console.show(summary_table(results))
    failed = [result for result in results if not result.passed]
    if failed:
        console.error(f"{len(failed)} of {len(results)} gates failed: {', '.join(result.name for result in failed)}.")
    else:
        console.success("All gates passed.")
    raise typer.Exit(overall_exit_code(results))
