from pathlib import Path

from mb import lock_check, runner


def test_passes_when_lock_is_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(runner, "run_quiet", lambda *args, **kwargs: (0, ""))

    exit_code = lock_check.run_check(Path("/repo"))

    assert exit_code == 0


def test_fails_and_points_to_uv_sync_when_lock_is_stale(monkeypatch, capsys):
    monkeypatch.setattr(
        runner, "run_quiet", lambda *args, **kwargs: (1, "The lockfile at `uv.lock` needs to be updated")
    )

    exit_code = lock_check.run_check(Path("/repo"))

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "uv sync" in output
    assert "git add uv.lock" in output


def test_runs_uv_lock_check_read_only(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_quiet(args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")
        captured["drop_env"] = kwargs.get("drop_env")
        return 0, ""

    monkeypatch.setattr(runner, "run_quiet", fake_run_quiet)

    lock_check.run_check(Path("/repo"))

    # It must call the read-only `uv lock --check`, never a lock-mutating command.
    assert captured["args"] == ["uv", "lock", "--check"]
    assert captured["cwd"] == Path("/repo")
    # UV_FROZEN must be stripped, or the CI image's UV_FROZEN=1 degrades --check to --check-exists.
    assert captured["drop_env"] == ["UV_FROZEN"]
