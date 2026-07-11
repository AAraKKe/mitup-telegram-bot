from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RecordedCall:
    args: list[str]
    cwd: Path | None
    extra_env: dict[str, str] | None


@dataclass
class CommandRecorder:
    """Stand-in for runner.run_command that records invocations instead of spawning processes."""

    calls: list[RecordedCall] = field(default_factory=list)
    exit_codes: dict[str, int] = field(default_factory=dict)

    def __call__(self, args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> int:
        self.calls.append(RecordedCall(args=args, cwd=cwd, extra_env=extra_env))
        joined = " ".join(args)
        return next((code for fragment, code in self.exit_codes.items() if fragment in joined), 0)

    @property
    def commands(self) -> list[list[str]]:
        return [call.args for call in self.calls]
