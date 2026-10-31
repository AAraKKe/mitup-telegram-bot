import re
import shutil

from . import console, runner

UV_CACHE_VOLUME = "mitup-bot-uv-cache"

POSTGRES_CONTAINER_PORT = "5432"

# `docker compose port` prints the published address, such as `127.0.0.1:55000`.
PUBLISHED_PORT_RE = re.compile(r"^\S*:(\d+)$", re.MULTILINE)


def ensure_uv_cache_volume():
    """Create the shared uv download-cache volume if it does not exist yet.

    docker-compose.yaml declares the volume `external` so no single compose project claims
    ownership of it (compose warns in every other checkout otherwise), which also means compose
    never creates it. `docker volume create` is idempotent, so calling this before every compose
    invocation is safe; a failure is ignored because compose itself reports a missing docker
    daemon far more clearly.
    """
    runner.run_quiet(["docker", "volume", "create", UV_CACHE_VOLUME])


def postgres_host_port() -> int | None:
    """Host port of this checkout's Postgres, or None when it is not running or docker is absent."""
    if shutil.which("docker") is None:
        return None
    exit_code, output = runner.run_quiet(["docker", "compose", "port", "postgres", POSTGRES_CONTAINER_PORT])
    published_port = PUBLISHED_PORT_RE.search(output)
    if exit_code != 0 or published_port is None:
        return None
    return int(published_port.group(1))


def host_database_env() -> dict[str, str] | None:
    """Settings that point an app run on the host at this checkout's Postgres.

    Inside the compose containers there is no docker binary, so these stay None and dev.toml's
    in-network address applies.
    """
    host_port = postgres_host_port()
    if host_port is None:
        return None
    console.info(f"Using this checkout's Postgres on port {host_port}")
    return {"MITUPBOT__DB__URL": "127.0.0.1", "MITUPBOT__DB__PORT": str(host_port)}
