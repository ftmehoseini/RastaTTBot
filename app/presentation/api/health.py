"""HTTP health endpoint for Docker/monitoring, and the shared Redis/DB checks.

GET /health  → 200 {"status": "ok", ...} when the database and Redis respond, else 503.
The api package is also where a future admin dashboard/API would live.
"""

from typing import Any

from aiohttp import web
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger

log = get_logger(__name__)

_redis: Redis | None = None


def set_redis(client: Redis | None) -> None:
    global _redis
    _redis = client


async def check_redis() -> bool:
    if _redis is None:
        return False
    try:
        return bool(await _redis.ping())
    except Exception:
        return False


async def check_database(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def create_health_app(engine: AsyncEngine) -> web.Application:
    async def health(_: web.Request) -> web.Response:
        checks: dict[str, Any] = {"database": await check_database(engine), "redis": await check_redis()}
        ok = all(checks.values())
        return web.json_response({"status": "ok" if ok else "degraded", **checks}, status=200 if ok else 503)

    app = web.Application()
    app.router.add_get("/health", health)
    return app


async def start_health_server(engine: AsyncEngine, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(create_health_app(engine), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("health endpoint listening", host=host, port=port)
    return runner
