"""The route surface `create_app` exposes, independently of any single endpoint."""

import pytest
from fastapi import FastAPI

from mitup_bot.config import RunModes
from tests.helpers import build_test_web_app, build_web_client


@pytest.fixture
def web_app() -> FastAPI:
    return build_test_web_app(run_mode=RunModes.WEBHOOK)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_interactive_docs_routes_are_not_served(web_app: FastAPI, path: str):
    # The app is reachable at the public webhook domain, so FastAPI's default docs and schema
    # routes stay off — they would hand an anonymous caller the full route inventory.
    async with build_web_client(web_app) as client:
        response = await client.get(path)

    assert response.status_code == 404
