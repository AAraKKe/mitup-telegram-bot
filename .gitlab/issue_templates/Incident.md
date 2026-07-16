<!--
Appended automatically to every incident GitLab creates from a CloudWatch alert.
Selected under Settings → Monitor → Incidents → Incident template. The dynamic context
(which alarm, triggering children, state transition, links) lives in the alert description
above this block — built by the alarm-action Lambda (apps/lambda-alarm).
-->

---

## Triage runbook

1. Read the alert description above: it names the alarm, the triggering child alarm(s) for
   composites, the state transition, and links to the CloudWatch alarm page. It also carries the
   alarm's own triage hint naming the most relevant saved queries.
2. Open [CloudWatch Logs Insights](https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#logsV2:logs-insights)
   and load the saved queries from the `Mitup/` folder (**Queries** panel → **Saved queries**).
   The table below maps every alarm to its drill-down queries.
3. Match the failing flow against the [log-flow registry](https://claude.ai/code/artifact/5471f7ea-1aca-41d7-9e25-52f6dcfdedfd)
   to know which fields, messages, and correlation ids (`update_id`, `run_id`, `tg_user_id`) that
   flow emits.
4. For the overall picture, use the [MitupCommandCenter dashboard](https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#dashboards/dashboard/MitupCommandCenter)
   and the [alarms overview](https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#alarmsV2:).

### Saved queries by alarm

| Alarm | Saved queries (in the `Mitup/` folder) |
|---|---|
| MitupSustainedFaults / MitupCriticalFaultRate | `Mitup/Bot — Recent faults with handler context` · `Mitup/Bot — Error log lines (non-EMF)` · `Mitup/Bot — Handler leaderboard` |
| MitupEventsAnyTypeFailing (any `MitupEventFault-*` child) | `Mitup/Events — Last run per event type` (failing vs not running) · `Mitup/Events — Recent event faults` (traceback column) · `Mitup/Events — Error log lines (non-EMF)` · `Mitup/Events — Trace one event run` |
| MitupDbPoolTimeouts / MitupEventsDbPoolTimeouts / MitupDbPoolCheckoutWaitP95 | `Mitup/DB — Pool saturation timeline` · `Mitup/Bot — Slowest individual requests` |
| MitupPatreonCreatorTokenExpiring | `Mitup/Events — Supporter-check run detail` |
| MitupNoUsersInDatabase / MitupNoMeetingsInDatabase | `Mitup/Events — Stats gauges timeline` (cliff vs decline) · `Mitup/All — Faults & errors across services` (migration or wipe activity) |
| Anything else / cross-service | `Mitup/All — Faults & errors across services` · `Mitup/All — Application logs only (no EMF)` · `Mitup/All — Warning+ rate by component and flow` |

### Useful context

- Structlog lines carry `component` + `flow`; EMF metric envelopes carry a top-level `_aws` key —
  the two `Mitup/All` view-splitter queries separate them.
- The `MitupEventFault-*` children treat missing data as breaching, so they fire both on failed
  runs and on runs that never happened (task down, scheduler stall). `Last run per event type`
  is the query that distinguishes the two.
- Alerts auto-resolve when the alarm returns to OK (the Lambda re-POSTs with `end_time`), and
  this incident closes with them. A short-lived incident usually means one transient failed run
  that recovered on the next cadence tick — still worth a glance at the traceback.
