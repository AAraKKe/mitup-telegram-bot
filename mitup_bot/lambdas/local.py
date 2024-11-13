# pragma: no cover
import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from random import uniform
from typing import Any, Protocol, TypedDict, Unpack, assert_never

import click

from . import recurrent_lambda

DEFAULT_USER_CLEANUP_INTERVAL = 3600
DEFAULT_NOTIFY_MEETINGS_START = 60


@dataclass
class IntervalsConfiguration:
    user_cleanup: int
    notify_start_meeting: int

    def get(self, event_type: recurrent_lambda.EventType) -> int:
        match event_type:
            case recurrent_lambda.EventType.USER_CLEANUP:
                return self.user_cleanup
            case recurrent_lambda.EventType.NOTIFY_START_MEETING:
                return self.notify_start_meeting
            case _:
                assert_never()


class LambdaArgs(TypedDict):
    event: dict[str, Any]
    context: Any


class LambaProtocol(Protocol):
    def __call__(self, event: dict[str, Any], context: Any) -> Awaitable[None]: ...


def event_from_type(event_type: recurrent_lambda.EventType) -> dict[str, Any]:
    return recurrent_lambda.MaintainanceEvent(event_type=event_type, env=recurrent_lambda.Env.DEV).model_dump()


async def run_periodic_lambda(
    coro: LambaProtocol,
    interval: int,
    time_before_start: float | None = None,
    **coro_args: Unpack[LambdaArgs],
):
    # If no time provided add 10% interval jitter
    time_before_start = time_before_start or uniform(0, interval * 0.1)
    await asyncio.sleep(time_before_start)

    # Run the coroutine indefinitely
    while True:
        await coro(**coro_args)
        await asyncio.sleep(interval)


async def run_all_tasks(intervals: IntervalsConfiguration):
    async with asyncio.TaskGroup() as tg:
        for event_type in recurrent_lambda.EventType:
            tg.create_task(
                run_periodic_lambda(
                    recurrent_lambda.handle_maintainance,
                    intervals.get(event_type),
                    time_before_start=0,
                    event=event_from_type(event_type),
                    context={},
                )
            )


@click.command()
@click.option(
    "--user-cleanup-interval",
    default=DEFAULT_USER_CLEANUP_INTERVAL,
    help="Interval in seconds for user cleanup",
    show_default=True,
)
@click.option(
    "--notify-meeting-interval",
    default=DEFAULT_NOTIFY_MEETINGS_START,
    help="Interval in seconds to send notifications about meetings starting soon",
    show_default=True,
)
def main(user_cleanup_interval: int, notify_meeting_interval: int):
    """This method is used when launching notifications locally as a container"""
    intervals = IntervalsConfiguration(user_cleanup=user_cleanup_interval, notify_start_meeting=notify_meeting_interval)
    asyncio.run(run_all_tasks(intervals))


if __name__ == "__main__":
    main()
