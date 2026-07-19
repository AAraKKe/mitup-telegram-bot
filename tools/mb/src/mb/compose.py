from . import runner

UV_CACHE_VOLUME = "mitup-bot-uv-cache"


def ensure_uv_cache_volume():
    """Create the shared uv download-cache volume if it does not exist yet.

    docker-compose.yaml declares the volume `external` so no single compose project claims
    ownership of it (compose warns in every other checkout otherwise), which also means compose
    never creates it. `docker volume create` is idempotent, so calling this before every compose
    invocation is safe; a failure is ignored because compose itself reports a missing docker
    daemon far more clearly.
    """
    runner.run_quiet(["docker", "volume", "create", UV_CACHE_VOLUME])
