"""Recurrent-event jobs and the periodic runner that schedules them.

Each job module exposes an async `run(api, client)` entry point; `service.py` owns the
`EventType` catalogue, per-event intervals, and the TaskGroup loop that the `recurrent-events`
CLI command invokes.
"""
