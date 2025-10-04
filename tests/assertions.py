from unittest import mock

from deepdiff import DeepDiff


def assert_awaited_once_with_diff(method: mock.AsyncMock, *args, **kwargs):
    expected_call = mock.call(*args, **kwargs)

    if method.await_count == 0:
        raise AssertionError(f"Expected '{method._mock_name}' to be awaited once, but was not awaited.")
    if method.await_count > 1:
        raise AssertionError(
            f"Expected '{method._mock_name}' to be awaited once, but was awaited {method.await_count} times."
        )

    actual_call = method.await_args
    assert actual_call is not None

    if actual_call != expected_call:
        diff = DeepDiff(
            (actual_call.args, actual_call.kwargs),
            (expected_call.args, expected_call.kwargs),
            verbose_level=2,
            ignore_type_subclasses=False,
            threshold_to_diff_deeper=1,
            max_diffs=None,
        )
        raise AssertionError(f"Call arguments for '{method._mock_name}' differ:\n{diff.pretty()}")


def assert_awaited_with_diff(method: mock.AsyncMock, times: int, *args, **kwargs):
    expected_call = mock.call(*args, **kwargs)

    if method.await_count != times:
        raise AssertionError(
            f"Expected '{method._mock_name}' to be awaited {times} times, but was awaited {method.await_count} times."
        )

    if expected_call not in method.await_args_list:
        diffs = [
            DeepDiff(
                (expected_call.args, expected_call.kwargs),
                (call.args, call.kwargs),
                verbose_level=2,
                ignore_type_subclasses=False,
                threshold_to_diff_deeper=1,
                max_diffs=None,
            )
            for call in method.await_args_list
        ]
        raise AssertionError(
            f"Expected call to '{method._mock_name}' not found. Diffs:\n"
            + "\n---\n".join(diff.pretty() for diff in diffs)
        )
