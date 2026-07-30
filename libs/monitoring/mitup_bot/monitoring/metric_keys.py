# This module contais static metric names to be sure to use always the same names no matter
# where we emit the metric from
from enum import StrEnum, auto


class CamelCaseStrEnum(StrEnum):
    """This is a string enum that has, as value, the camel case version of the enum name."""

    @property
    def value(self) -> str:
        # Instead of having the snakecase lets use CamelCase
        return self.name.title().replace("_", "")

    def __str__(self) -> str:
        return self.value


class MetricKey(CamelCaseStrEnum):
    """Static metric names, so the same observable is always emitted under one name.

    A new member needs a named reader — a dashboard widget, an alarm or a saved query — or a
    docstring saying it is diagnostic-only and deliberately unread. See the `observability` skill.
    """

    TIME = auto()
    """Metric to be emitted when the time is measured"""
    COUNT = auto()
    """Metric to be emitted when the count is measured"""
    ERROR = auto()
    """This is a metric to be emitted when there is a processing error, user input error, etc."""
    FAULT = auto()
    """This is a metric to be emitted when there is a system fault, something that is not expected to happen.
    It is the outcome of one invocation, written exactly once per logger per flush window by the
    wrapper that owns the invocation (`callback_with_metrics`, `handle_maintainance`) — plus
    `registry.process_update_error` for a failure no wrapped callback owned — and by nothing else.
    EMF appends repeated values under the same name, so a second writer turns the datapoint into an
    array that the fault alarms and the `filter Fault = 1` triage query both stop matching."""
    POST_COMMIT_API_FAULT = auto()
    """A queued Telegram call failed while the outbox drained after the transaction committed. This is
    a partial rendering failure, not the invocation outcome — the DB is already correct and the handler
    goes on to finish on `Fault=0` — so it counts on its own series instead of the aggregate `FAULT`."""
    CONTEXT_LOST = auto()
    """Emitted when in-memory conversation state was lost (e.g. a rolling deploy wiped user_data
    mid-flow, or flow-shaped input arrived with no active flow). Expected consequence of in-memory
    state, not a code fault, so it carries its own metric and bypasses the fault alarms."""
    MEETING_NOT_OWNED = auto()
    """Metric to be emitted when the user tries to do an action with a meeting that does not belong to them."""
    MESSAGE_DELETED = auto()
    """Represents a message that has been deleted in Telegram and deleted from the database"""
    STALE_MEETING_MESSAGE = auto()
    """This metrics is emitted when someone interacts with a message of a meeting that should not be available."""
    UNAUTHORIZED_MEETING_CALLBACK = auto()
    """Emitted when a callback carries a meeting id the caller has no claim on. Callback data is
    client-supplied, so this counts rejected attempts to act on an arbitrary meeting."""
    INACTIVE_USER_SET = auto()
    """Metric emitted when a MEMBER user has been detected as unreachable and transitioned to LEFT."""
    INACTIVE_USERS_DELETED = auto()
    """Metric emitted by the user cleanup lambda with the number of inactive users found and to be deleted"""
    LEFT_USERS_DEMOTED = auto()
    """Number of LEFT users past the grace period demoted to JOINED_ONLY because only join links to
    active meetings still depend on them"""
    DELETION_REQUESTED_USERS = auto()
    """Gauge for users with status DELETION_REQUESTED (marked for erasure, awaiting the next cleanup purge)"""
    NOTIFICATIONS_FAILED = auto()
    """Number of joined links whose starting-soon write lifecycle raised (rolled back
    pre-commit, or aborted mid-drain by a systemic network error after the flag committed)"""
    NOTIFICATIONS_SENT = auto()
    """Number of starting-soon notifications enqueued in committed per-link transactions
    (post-commit delivery failures surface as Fault metrics, not here)"""
    NOTIFICATIONS_TO_SEND = auto()
    """Show how many notifications should be sent when a meeting is about to start"""
    ACTIVE_USERS = auto()
    """Stat for total number of active users"""
    INACTIVE_USERS = auto()
    """Stat for the number of inactive users"""
    INVITED_USERS = auto()
    """Stat for the total of invited users"""
    TOTAL_USERS = auto()
    """Stat for the total number of users in the database"""
    ACTIVE_MEETINGS = auto()
    """Stat for the total number of active meetings"""
    MEETINGS_WITH_DATETIME = auto()
    """Stat for the number of active meetings with a datetime set"""
    INCOGNITO_MEETINGS = auto()
    """Stat for the number of active incognito meetings"""
    PUBLIC_MEETINGS = auto()
    """Stat for the number of active public meetings"""
    MEETINGS_WITH_INVITATION = auto()
    """Stat for the number of active meetings with invitation"""
    MEETINGS_IN_PROGRESS = auto()
    """Stat for the number of active meetings currently inside their in-progress window"""
    WAITING_LIST_SIZE = auto()
    """Stat for the number of users on the waiting list of an active meeting"""
    LINKED_PATREON_ACCOUNTS = auto()
    """Stat for the number of users with a linked Patreon account (supporter_subscriptions rows)"""
    SUBSCRIPTIONS_IN_GRACE = auto()
    """Stat for the number of linked subscriptions currently in their expiration grace period"""
    NOTIFICATIONS_DISABLED = auto()
    """Stat for the number of users that have turned meeting notifications off"""
    SHARED_MEETINGS = auto()
    """Stat for the total number of shared meetings"""
    TOTAL_MEETINGS = auto()
    """Stat for the total number of meetings in the database"""
    MEETINGS_TO_DEACTIVATE = auto()
    """Number of meetings that should be deactivated"""
    MEETINGS_DEACTIVATED = auto()
    """Number of meetings successfully deactivated"""
    MEETINGS_DEACTIVATION_FAILED = auto()
    """Number of meetings that failed to be deactivated"""
    MEETUPS_ABOUT_TO_BE_DELETED = auto()
    """Number of meetings that should be deleted"""
    MEETUPS_DELETED = auto()
    """Number of meetings successfully deleted"""
    DB_CONNECTIONS_LEAKED = auto()
    """Number of database connections that were not properly closed (leaked connections)"""
    MEETINGS_STARTED_PROCESSED = auto()
    """Number of meetings processed by the notify-meeting-started task"""
    STARTED_NOTIFICATIONS_SENT = auto()
    """Number of started-meeting notifications enqueued in committed per-meeting transactions
    (post-commit delivery failures surface as Fault metrics, not here)"""
    STARTED_NOTIFICATIONS_FAILED = auto()
    """Number of meetings whose started-notification write lifecycle raised (rolled back
    pre-commit, or aborted mid-drain by a systemic network error after the commit)"""
    WEBHOOK_FORBIDDEN = auto()
    """Telegram rejected the webhook request with a 403 Forbidden response"""
    WEBHOOK_MALFORMED_UPDATE = auto()
    """Webhook received a payload that could not be parsed as a Telegram Update"""
    PATREON_WEBHOOK_RECEIVED = auto()
    """A membership delivery reached the Patreon webhook endpoint (before signature verification)"""
    PATREON_WEBHOOK_FORBIDDEN = auto()
    """A Patreon webhook delivery was rejected for a missing or invalid HMAC signature (1), or passed (0)"""
    PATREON_WEBHOOK_APPLIED = auto()
    """A verified Patreon webhook delivery changed a linked user's supporter level (1), or was a no-op (0)"""
    PATREON_WEBHOOK_MALFORMED = auto()
    """A Patreon webhook delivery carried a body that could not be parsed as a membership event"""
    PATREON_WEBHOOK_FAULT = auto()
    """The Patreon webhook endpoint failed to process a verified delivery (surfaced as 500; Patreon retries)"""
    PATREON_WEBHOOK_REGISTRATION_FAILED = auto()
    """Startup registration of the Patreon membership webhook failed (isolated; startup continues)"""
    LIFESPAN_STARTUP_FAILED = auto()
    """FastAPI/uvicorn lifespan failed during startup"""
    LIFESPAN_SHUTDOWN_FAILED = auto()
    """FastAPI/uvicorn lifespan failed during shutdown"""
    MIGRATION_ROWS_READ = auto()
    """Number of rows read from the source Rails table during a Rails → Python migration run"""
    MIGRATION_ROWS_INSERTED = auto()
    """Number of rows written (or that would be written, in dry-run) to the new schema"""
    MIGRATION_ROWS_SKIPPED = auto()
    """Number of rows skipped during migration (already imported, deduped, phantom user, etc.)"""
    MIGRATION_ROWS_FAILED = auto()
    """Number of rows that failed to migrate due to mapping or insert errors"""
    MIGRATION_PHASE_DURATION_MS = auto()
    """Wall-clock duration of a migration phase, in milliseconds"""
    MIGRATION_VERIFICATION_DELTA = auto()
    """Difference between Rails row count and new-DB row count for a given table after verification"""
    MIGRATION_ARCHIVE_BYTES = auto()
    """Total bytes written (or stubbed in dry-run) to S3 for an archived table"""
    MIGRATION_ARCHIVE_ROWS = auto()
    """Number of rows streamed to S3 for an archived table"""
    JOINED_ONLY_USERS = auto()
    """Gauge for users with status JOINED_ONLY (joined via inline button, never DM-ed the bot)"""
    JOINED_ONLY_USERS_DELETED = auto()
    """Number of JOINED_ONLY users deleted after all their meetings were deactivated"""
    DB_POOL_CONNECTIONS_IN_USE = auto()
    """Gauge of pooled DB connections currently checked out (emitted on checkout and checkin)"""
    DB_POOL_CONNECTIONS_OPENED = auto()
    """New physical DB connections opened by the pool (growth and overflow churn)"""
    DB_POOL_CHECKOUT_WAIT_TIME = auto()
    """Milliseconds a transaction waited to check out a pooled DB connection"""
    DB_POOL_TIMEOUT = auto()
    """Checkout attempts that gave up after waiting pool_timeout seconds for an exhausted pool"""
    BROADCAST_DELIVERY_SENT = auto()
    """A single broadcast delivery resolved to SENT (1) or not (0); emitted once per recorded delivery"""
    BROADCAST_DELIVERY_FAILED = auto()
    """A single broadcast delivery resolved to FAILED (1) or not (0); emitted once per recorded delivery"""
    BROADCAST_DELIVERY_RETRY_PENDING = auto()
    """A single broadcast delivery was parked for a transient-failure retry (1) or not (0); per recorded delivery"""
    BROADCAST_DELIVERY_SKIPPED_INACTIVE = auto()
    """A single broadcast delivery was skipped for an unreachable recipient (1) or not (0); per recorded delivery"""
    BROADCAST_BATCH_MESSAGES_SENT = auto()
    """Number of deliveries that reached their recipient in one drained batch iteration"""
    BROADCAST_PROGRESS_PERCENT = auto()
    """Live 0-100 completion of the broadcast, computed from the delivery table after each batch"""
    BROADCASTS_QUEUED = auto()
    """Backlog gauge: broadcasts sitting QUEUED; emitted only on sender ticks with backlog to report"""
    BROADCASTS_SENDING = auto()
    """Backlog gauge: broadcasts currently SENDING; emitted only on sender ticks with backlog to report"""
    BROADCAST_DELIVERIES_RETRY_PENDING = auto()
    """Backlog gauge: deliveries parked RETRY_PENDING; emitted only on sender ticks with backlog to report"""
    MEETINGS_DELETION_FAILED = auto()
    """Number of expired meetings left undeleted this run because notifying the owner raised an error;
    they are nominated again on the next run"""
    MEETUPS_DELETED_UNNOTIFIED = auto()
    """Number of expired meetings deleted this run without their owner being told, because the owner
    has blocked the bot or no longer exists (subset of MeetupsDeleted)"""
    EXPIRATION_NOTIFICATIONS_FAILED = auto()
    """Number of expiration warnings that failed to send this run; their meetings stay in the warning
    pool and are nominated again on the next run"""
    UNHANDLED_CALLBACK = auto()
    """A callback query reached the registry fallback — no registered handler matched it"""
    FEATURE_CANCELLED = auto()
    """A user cancelled a feature flow before completing it (emitted under the Feature dimension)"""
    FLOW_STARTED = auto()
    """A multi-step flow was entered (emitted under the Feature dimension, e.g. the Patreon link funnel)"""
    FLOW_COMPLETED = auto()
    """A multi-step flow reached its success end (emitted under the Feature dimension)"""
    PATREON_LINK_STAGED = auto()
    """A Patreon consent completed and its pairing code was issued, awaiting confirmation in Telegram"""
    PATREON_LINK_PROMPTED = auto()
    """A pairing code reached the confirmation prompt in Telegram (emitted under the Feature dimension)"""
    PATREON_LINK_REFUSED = auto()
    """A Patreon link attempt ended without linking; the `Patreon link attempt refused` log line
    says which branch"""
    PATREON_PENDING_LINKS_DELETED = auto()
    """Pending Patreon links erased by the cleanup run (expired, spent, or purged with their user)"""
    NEW_MEMBERS = auto()
    """Users who completed onboarding in the last 24 hours, split by the `AcquisitionSource`
    dimension and reported once per source each run, zeros included. Read by the new-members widget
    on the infra dashboard, which needs the dense series: a source that stops producing members is
    only visible as a run of real zeros."""
    UNROUTED_REQUEST = auto()
    """HTTP requests that matched no declared route, counted in memory and emitted as one
    dimensionless sample per minute — a quiet minute emits nothing, because an EMF record is itself
    a log line and the volume is the whole point. Read by the infra `Unrouted requests (scanning)`
    widget and the `MitupUnroutedRequestSweep` alarm. The paths probed are caller-controlled and
    unbounded, so none of them reaches this record or the stream. A spike is a scan, not a bug."""

    def with_prefix(self, prefix: str, separator: str = "/") -> str:
        return f"{prefix}{separator}{self.value}"


class Feature(CamelCaseStrEnum):
    SET_TIMEZONE = auto()
    CREATE_MEETING = auto()
    EDIT_MEETING = auto()
    REACTIVATE_MEETING = auto()
    SHARE_MEETING = auto()
    JOIN_MEETING = auto()
    LEAVE_MEETING = auto()
    INVITE_USERS = auto()
    ATTACH_TO_CHAT = auto()
    SEARCH_CHAT_MEETINGS = auto()
    PATREON_LINK = auto()
    RICH_MESSAGE = auto()
