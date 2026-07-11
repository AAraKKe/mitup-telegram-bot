import click

from mitup_bot.config import Env
from mitup_bot.events.service import (
    DEFAULT_DEACTIVATE_MEETINGS_INTERVAL,
    DEFAULT_GENERATE_STATS_INTERVAL,
    DEFAULT_MEETUPS_CLEANUP_INTERVAL,
    DEFAULT_NOTIFY_MEETING_STARTED_INTERVAL,
    DEFAULT_NOTIFY_MEETINGS_START,
    DEFAULT_SEND_BROADCASTS_INTERVAL,
    DEFAULT_SUPPORTER_CHECK_INTERVAL,
    DEFAULT_USER_CLEANUP_INTERVAL,
    IntervalsConfiguration,
    run_events,
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
@click.option(
    "--generate-stats-interval",
    default=DEFAULT_GENERATE_STATS_INTERVAL,
    help="Interval in seconds to generate bots stats",
    show_default=True,
)
@click.option(
    "--deactivate-meetings-interval",
    default=DEFAULT_DEACTIVATE_MEETINGS_INTERVAL,
    help="Interval in seconds to check for meetings to deactivate",
    show_default=True,
)
@click.option(
    "--meetups-cleanup-interval",
    default=DEFAULT_MEETUPS_CLEANUP_INTERVAL,
    help="Interval in seconds to cleanup meetups",
    show_default=True,
)
@click.option(
    "--notify-meeting-started-interval",
    default=DEFAULT_NOTIFY_MEETING_STARTED_INTERVAL,
    help="Interval in seconds to send notifications when a meeting has started",
    show_default=True,
)
@click.option(
    "--send-broadcasts-interval",
    default=DEFAULT_SEND_BROADCASTS_INTERVAL,
    help="Interval in seconds to send queued mass broadcasts",
    show_default=True,
)
@click.option(
    "--supporter-check-interval",
    default=DEFAULT_SUPPORTER_CHECK_INTERVAL,
    help="Interval in seconds to validate supporter memberships against Patreon",
    show_default=True,
)
@click.option(
    "--env",
    default=Env.DEV,
    type=click.Choice(Env, case_sensitive=False),
    help="Environment to execute the command with",
    show_default=True,
)
@click.option(
    "--start-time",
    default=0,
    type=float,
    help="Time before starting the first event",
)
def cli(
    user_cleanup_interval: int,
    notify_meeting_interval: int,
    generate_stats_interval: int,
    deactivate_meetings_interval: int,
    meetups_cleanup_interval: int,
    notify_meeting_started_interval: int,
    send_broadcasts_interval: int,
    supporter_check_interval: int,
    env: Env,
    start_time: float,
):
    """Launch all recurrent events periodically"""
    intervals = IntervalsConfiguration(
        user_cleanup=user_cleanup_interval,
        notify_start_meeting=notify_meeting_interval,
        notify_meeting_started=notify_meeting_started_interval,
        generate_stats=generate_stats_interval,
        deactivate_meetings=deactivate_meetings_interval,
        meetups_cleanup=meetups_cleanup_interval,
        send_broadcasts=send_broadcasts_interval,
        supporter_check=supporter_check_interval,
    )
    run_events(env, intervals, start_time)
