# rails-migration

One-off developer tool that migrates data from the legacy Rails bot Postgres into the
new-schema Postgres. It reads the Rails tables, maps each row to the new SQLModel schema,
writes an audit row per source row, archives the dropped Rails-only tables to S3, and
verifies row counts.

## Invocation

```bash
uv run mitup-rails-migration [OPTIONS]
```

Run `uv run mitup-rails-migration --help` for the full option list. The phases run in a
fixed order (`users`, `meetups`, `joins`, `invitations`, `messages`, `archive`, `verify`);
`--phases` selects a subset.

## Inputs

| Input | How it's supplied | Purpose |
|---|---|---|
| Rails source DB | `--rails-url` / `RAILS_DB_URL` | DSN of the legacy Rails Postgres to read from. |
| Target DB | `--env` selects the environment TOML; `MITUPBOT__DB__*` env vars override individual settings on top of it | Connection to the new-schema Postgres the run writes into. `--env` defaults to `prod`. |
| Archive bucket | `--archive-s3-uri` / `MIGRATE_ARCHIVE_S3_URI` | `s3://` prefix under which gzipped JSONL dumps of the dropped Rails-only tables are written. Required for a live `archive` phase. |

The tool loads the target-DB config from the selected environment's TOML and layers any
`MITUPBOT__DB__*` env vars over it, so the connection can point at the private RDS without
editing checked-in config.

## Connectivity

A single process must reach three networks at once:

- the Rails source Postgres on Heroku,
- the target Postgres on the private RDS, and
- the AWS APIs (S3 for archiving, CloudWatch for metrics).

The RDS is not publicly reachable, so the expected pattern is an SSM port-forward session
to the RDS instance, with `MITUPBOT__DB__*` (or the target-DB DSN) pointed at the local
forwarded port.

## Dry-run (default)

`--dry-run` is the default; pass `--no-dry-run` for a live cutover. In dry-run the full
pipeline executes and all metrics are emitted, but the outer DB transaction is rolled back
and no S3 objects are written.

A healthy dry-run shows:

- per-table rows-read counts (`MigrationRowsRead`) that are plausible against the Rails data,
- `MigrationRowsFailed` at `0` for every table,
- joins that legitimately skip duplicate `(user, meetup)` pairs (reported as skipped, not failed),
- no S3 objects written for the archived tables (byte counts are stubbed).

## Crash semantics

Every phase runs inside a single outer transaction. A crash mid-run rolls back the entire
transaction, including the audit rows, so nothing is left half-applied. A rerun therefore
starts from a clean slate. Per-row mapping or insert errors are isolated with SAVEPOINTs and
recorded as failures without aborting the surrounding transaction.

## Verify semantics

The `verify` phase emits `MigrationVerificationDelta` (Rails count minus new-DB count) per table:

- `users`, `meetups`, `messages`: delta should be `0`.
- `joined_users`: delta is positive by design. Its Rails side sums `user_join_meetups` and
  `user_waiting_lists`, and the joins phase dedups a user's rows across those two tables, so
  the new-DB count is legitimately lower than the summed Rails count.

Invitations are not verified.
