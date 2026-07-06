"""Shared FastAPI dependency getters for the web layer.

These narrow the ``Any`` that Starlette's ``app.state`` returns to concrete types at the parameter
level.
"""

from fastapi import Request
from telegram.ext import Application

from mitup_bot.monitoring.client import MetricsClient


def get_ptb_application(request: Request) -> Application:
    return request.app.state.ptb_app


def get_webhook_secret(request: Request) -> str | None:
    return request.app.state.secret_token


def get_metrics_client(request: Request) -> MetricsClient:
    return request.app.state.metrics_client
