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
    """Metric to be emitted when the time is measured"""

    TIME = auto()
    """Metric to be emitted when the count is measured"""
    COUNT = auto()
    """This is a metric to be emitted when there is a processing error, user input error, etc."""
    ERROR = auto()
    """This is a metric to be emitted when there is a system fault, something that is not expected to happen."""
    FAULT = auto()
    """Emitted when in-memory conversation state was lost (e.g. a rolling deploy wiped user_data
    mid-flow, or flow-shaped input arrived with no active flow). Expected consequence of in-memory
    state, not a code fault, so it carries its own metric and bypasses the fault alarms."""
    CONTEXT_LOST = auto()
    """Metric to be emitted when the user tries to do an action with a meeting that does not belong to them."""
    MEETING_NOT_OWNED = auto()
    """Represents a message that has been deleted in Telegram and deleted from the database"""
    MESSAGE_DELETED = auto()
    """This metrics is emitted when someone interacts with a message of a meeting that should not be available."""
    STALE_MEETING_MESSAGE = auto()
    """Emitted when a callback carries a meeting id the caller has no claim on. Callback data is
    client-supplied, so this counts rejected attempts to act on an arbitrary meeting."""
    UNAUTHORIZED_MEETING_CALLBACK = auto()
    """Metric emitted when a MEMBER user has been detected as unreachable and transitioned to LEFT."""
    INACTIVE_USER_SET = auto()
    """Metric emitted by the user cleanup lambda with the number of inactive users found and to be deleted"""
    INACTIVE_USERS_DELETED = auto()
    """Gauge for users with status DELETION_REQUESTED (marked for erasure, awaiting the next cleanup purge)"""
    DELETION_REQUESTED_USERS = auto()
    """Number of joined links whose starting-soon write lifecycle raised (rolled back
    pre-commit, or aborted mid-drain by a systemic network error after the flag committed)"""
    NOTIFICATIONS_FAILED = auto()
    """Number of starting-soon notifications enqueued in committed per-link transactions
    (post-commit delivery failures surface as Fault metrics, not here)"""
    NOTIFICATIONS_SENT = auto()
    """Show how many notifications should be sent when a meeting is about to start"""
    NOTIFICATIONS_TO_SEND = auto()
    """Stat for total number of active users"""
    ACTIVE_USERS = auto()
    """Stat for the number of inactive users"""
    INACTIVE_USERS = auto()
    """Stat for the total of invited users"""
    INVITED_USERS = auto()
    """Stat for the total number of users in the database"""
    TOTAL_USERS = auto()
    """Stat for the total number of active meetings"""
    ACTIVE_MEETINGS = auto()
    """Stat for the number of active meetings with a datetime set"""
    MEETINGS_WITH_DATETIME = auto()
    """Stat for the number of active incognito meetings"""
    INCOGNITO_MEETINGS = auto()
    """Stat for the number of active public meetings"""
    PUBLIC_MEETINGS = auto()
    """Stat for the number of active meetings with invitation"""
    MEETINGS_WITH_INVITATION = auto()
    """Stat for the number of active meetings currently inside their in-progress window"""
    MEETINGS_IN_PROGRESS = auto()
    """Stat for the number of users on the waiting list of an active meeting"""
    WAITING_LIST_SIZE = auto()
    """Stat for the number of users with a linked Patreon account (supporter_subscriptions rows)"""
    LINKED_PATREON_ACCOUNTS = auto()
    """Stat for the number of linked subscriptions currently in their expiration grace period"""
    SUBSCRIPTIONS_IN_GRACE = auto()
    """Stat for the number of users that have turned meeting notifications off"""
    NOTIFICATIONS_DISABLED = auto()
    """Stat for the total number of shared meetings"""
    SHARED_MEETINGS = auto()
    """Stat for the total number of meetings in the database"""
    TOTAL_MEETINGS = auto()
    """Number of meetings that should be deactivated"""
    MEETINGS_TO_DEACTIVATE = auto()
    """Number of meetings successfully deactivated"""
    MEETINGS_DEACTIVATED = auto()
    """Number of meetings that failed to be deactivated"""
    MEETINGS_DEACTIVATION_FAILED = auto()
    """Number of meetings that should be deleted"""
    MEETUPS_ABOUT_TO_BE_DELETED = auto()
    """Number of meetings successfully deleted"""
    MEETUPS_DELETED = auto()
    """Number of database connections that were not properly closed (leaked connections)"""
    DB_CONNECTIONS_LEAKED = auto()
    """Number of meetings processed by the notify-meeting-started task"""
    MEETINGS_STARTED_PROCESSED = auto()
    """Number of started-meeting notifications enqueued in committed per-meeting transactions
    (post-commit delivery failures surface as Fault metrics, not here)"""
    STARTED_NOTIFICATIONS_SENT = auto()
    """Number of meetings whose started-notification write lifecycle raised (rolled back
    pre-commit, or aborted mid-drain by a systemic network error after the commit)"""
    STARTED_NOTIFICATIONS_FAILED = auto()
    """Telegram rejected the webhook request with a 403 Forbidden response"""
    WEBHOOK_FORBIDDEN = auto()
    """Webhook received a payload that could not be parsed as a Telegram Update"""
    WEBHOOK_MALFORMED_UPDATE = auto()
    """A membership delivery reached the Patreon webhook endpoint (before signature verification)"""
    PATREON_WEBHOOK_RECEIVED = auto()
    """A Patreon webhook delivery was rejected for a missing or invalid HMAC signature (1), or passed (0)"""
    PATREON_WEBHOOK_FORBIDDEN = auto()
    """A verified Patreon webhook delivery changed a linked user's supporter level (1), or was a no-op (0)"""
    PATREON_WEBHOOK_APPLIED = auto()
    """A Patreon webhook delivery carried a body that could not be parsed as a membership event"""
    PATREON_WEBHOOK_MALFORMED = auto()
    """The Patreon webhook endpoint failed to process a verified delivery (surfaced as 500; Patreon retries)"""
    PATREON_WEBHOOK_FAULT = auto()
    """Startup registration of the Patreon membership webhook failed (isolated; startup continues)"""
    PATREON_WEBHOOK_REGISTRATION_FAILED = auto()
    """FastAPI/uvicorn lifespan failed during startup"""
    LIFESPAN_STARTUP_FAILED = auto()
    """FastAPI/uvicorn lifespan failed during shutdown"""
    LIFESPAN_SHUTDOWN_FAILED = auto()
    """Number of rows read from the source Rails table during a Rails → Python migration run"""
    MIGRATION_ROWS_READ = auto()
    """Number of rows written (or that would be written, in dry-run) to the new schema"""
    MIGRATION_ROWS_INSERTED = auto()
    """Number of rows skipped during migration (already imported, deduped, phantom user, etc.)"""
    MIGRATION_ROWS_SKIPPED = auto()
    """Number of rows that failed to migrate due to mapping or insert errors"""
    MIGRATION_ROWS_FAILED = auto()
    """Wall-clock duration of a migration phase, in milliseconds"""
    MIGRATION_PHASE_DURATION_MS = auto()
    """Difference between Rails row count and new-DB row count for a given table after verification"""
    MIGRATION_VERIFICATION_DELTA = auto()
    """Total bytes written (or stubbed in dry-run) to S3 for an archived table"""
    MIGRATION_ARCHIVE_BYTES = auto()
    """Number of rows streamed to S3 for an archived table"""
    MIGRATION_ARCHIVE_ROWS = auto()
    """Gauge for users with status JOINED_ONLY (joined via inline button, never DM-ed the bot)"""
    JOINED_ONLY_USERS = auto()
    """Number of JOINED_ONLY users deleted after all their meetings were deactivated"""
    JOINED_ONLY_USERS_DELETED = auto()
    """Gauge of pooled DB connections currently checked out (emitted on checkout and checkin)"""
    DB_POOL_CONNECTIONS_IN_USE = auto()
    """New physical DB connections opened by the pool (growth and overflow churn)"""
    DB_POOL_CONNECTIONS_OPENED = auto()
    """Milliseconds a transaction waited to check out a pooled DB connection"""
    DB_POOL_CHECKOUT_WAIT_TIME = auto()
    """Checkout attempts that gave up after waiting pool_timeout seconds for an exhausted pool"""
    DB_POOL_TIMEOUT = auto()
    """A single broadcast delivery resolved to SENT (1) or not (0); emitted once per recorded delivery"""
    BROADCAST_DELIVERY_SENT = auto()
    """A single broadcast delivery resolved to FAILED (1) or not (0); emitted once per recorded delivery"""
    BROADCAST_DELIVERY_FAILED = auto()
    """A single broadcast delivery was parked for a transient-failure retry (1) or not (0); per recorded delivery"""
    BROADCAST_DELIVERY_RETRY_PENDING = auto()
    """A single broadcast delivery was skipped for an unreachable recipient (1) or not (0); per recorded delivery"""
    BROADCAST_DELIVERY_SKIPPED_INACTIVE = auto()
    """Number of deliveries that reached their recipient in one drained batch iteration"""
    BROADCAST_BATCH_MESSAGES_SENT = auto()
    """Live 0-100 completion of the broadcast, computed from the delivery table after each batch"""
    BROADCAST_PROGRESS_PERCENT = auto()
    """Backlog gauge: broadcasts sitting QUEUED; emitted only on sender ticks with backlog to report"""
    BROADCASTS_QUEUED = auto()
    """Backlog gauge: broadcasts currently SENDING; emitted only on sender ticks with backlog to report"""
    BROADCASTS_SENDING = auto()
    """Backlog gauge: deliveries parked RETRY_PENDING; emitted only on sender ticks with backlog to report"""
    BROADCAST_DELIVERIES_RETRY_PENDING = auto()
    """Number of expired meetings whose permanent deletion failed (owner unreachable for the notice)"""
    MEETINGS_DELETION_FAILED = auto()
    """A callback query reached the registry fallback — no registered handler matched it"""
    UNHANDLED_CALLBACK = auto()
    """A user cancelled a feature flow before completing it (emitted under the Feature dimension)"""
    FEATURE_CANCELLED = auto()
    """A multi-step flow was entered (emitted under the Feature dimension, e.g. the Patreon link funnel)"""
    FLOW_STARTED = auto()
    """A multi-step flow reached its success end (emitted under the Feature dimension)"""
    FLOW_COMPLETED = auto()

    def with_prefix(self, prefix: str, separator: str = "/") -> str:
        return f"{prefix}{separator}{self.value}"


class Feature(CamelCaseStrEnum):
    SET_TIMEZONE = auto()
    NEW_USER_REGISTERED = auto()
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
