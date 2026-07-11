from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RecordedCall:
    args: list[str]
    cwd: Path | None
    extra_env: dict[str, str] | None


@dataclass
class CommandRecorder:
    """Stand-in for the runner's subprocess entry points; records invocations instead of spawning."""

    calls: list[RecordedCall] = field(default_factory=list)
    exit_codes: dict[str, int] = field(default_factory=dict)
    captured_outputs: dict[str, str] = field(default_factory=dict)

    def __call__(self, args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> int:
        self.calls.append(RecordedCall(args=args, cwd=cwd, extra_env=extra_env))
        joined = " ".join(args)
        return next((code for fragment, code in self.exit_codes.items() if fragment in joined), 0)

    def run_quiet(
        self, args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None
    ) -> tuple[int, str]:
        exit_code = self(args, cwd=cwd, extra_env=extra_env)
        joined = " ".join(args)
        captured = next((text for fragment, text in self.captured_outputs.items() if fragment in joined), "")
        return exit_code, captured

    @property
    def commands(self) -> list[list[str]]:
        return [call.args for call in self.calls]
