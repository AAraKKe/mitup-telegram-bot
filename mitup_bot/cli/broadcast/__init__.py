"""Sender phase of the mass-broadcast feature: the background fan-out that drains one queued
broadcast per run inside the recurrent-events service. Each delivery is sent immediately and
recorded as one structured `broadcast_delivery` log line per attempt. (Constraint: the
capture/`begin_write` outbox cannot observe per-send outcomes, so this module must not switch to
it.)

Per-delivery state machine (`BroadcastDeliveryStatus`; the claim and every transition out of
IN_PROGRESS are single atomic UPDATE statements):

              ┌─────────< re-claimed once next_attempt_time passes >─────────┐
              v                                                              │
  PENDING ─claim─> IN_PROGRESS ─── transient error, attempt_count < cap ──> RETRY_PENDING
 (audience             │
  snapshot)            ├── delivered ──────────────────────────────────────> SENT
                       ├── recipient unreachable ──────────────────────────> SKIPPED_INACTIVE
                       ├── permanent error, or transient at the cap ───────> FAILED
                       └── worker crash before the outcome is recorded ────> stuck: "orphan"

Invariants (the design contract — every bullet is load-bearing):

* No recipient is ever messaged twice. The claim is one statement —
  `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING ...` — so two live
  workers (ECS rolling-deploy overlap) can never take the same row, and only PENDING or due
  RETRY_PENDING rows are ever claimable, never IN_PROGRESS.
* FAILED means the Telegram call genuinely errored: the message was NOT delivered, so retrying
  it is double-send-safe — and it is strictly terminal once recorded. Orphans are the opposite —
  outcome unknown, possibly delivered — so finalization counts them separately and nothing ever
  retries them.
* Retries are bounded and paced: the claim bumps `attempt_count` atomically, a transient failure
  parks the row with a backoff, and at `MAX_DELIVERY_ATTEMPTS` it lands FAILED. Flood control
  (`RetryAfter`) halts the batch instantly — the triggering row keeps its attempt, the untried
  remainder is released with its claim increment undone, and the drain loop stops until the
  window passes. The dedicated low-rate broadcast bot (`bot.broadcast_max_rate`, built in
  `recurrent_events.build_broadcast_bot`) makes this reactive path a last resort.
* A broadcast finalizes only when no PENDING/RETRY_PENDING rows remain. While deferring, its
  `attempts` counter resets to 0, so `MAX_BROADCAST_ATTEMPTS` stays a pure crash-loop guard: the
  reset is unreachable on the exception path, and a broadcast legitimately waiting out backoff
  across ticks never creeps toward it. The operator summary DM fires exactly once — the
  compare-and-swap SENDING -> terminal transition picks the winner when two workers finalize.
* Resume-safe by construction: the audience snapshot is INSERT ... ON CONFLICT DO NOTHING on
  (broadcast_id, user_id), and final counts aggregate the delivery table, never in-memory state.
"""

from .claiming import claim_pending_batch, materialize_audience
from .finalize import finalize_and_report, finalize_broadcast
from .runner import run, send_all_pending
from .types import ANONYMOUS_INVITEE_TG_ID, MAX_BROADCAST_ATTEMPTS, MAX_DELIVERY_ATTEMPTS

__all__ = [
    "ANONYMOUS_INVITEE_TG_ID",
    "MAX_BROADCAST_ATTEMPTS",
    "MAX_DELIVERY_ATTEMPTS",
    "claim_pending_batch",
    "finalize_and_report",
    "finalize_broadcast",
    "materialize_audience",
    "run",
    "send_all_pending",
]
