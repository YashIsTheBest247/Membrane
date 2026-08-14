"""The Membrane proxy application.

One FastAPI container holds the L1–L4 pipeline, the forensics API, the SSE feed
and the Telegram webhook route. There is no second runtime to operate.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import approvals, audit
from .config import get_settings
from .db import dispose_db, init_db, session_scope
from .events import bus
from .policy import get_policy
from .routers import approvals as approvals_router
from .routers import bench, forensics, gateway, mcp, stream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
logger = logging.getLogger("membrane")

DESCRIPTION = """
A semi-permeable barrier for AI agents. Content passes through; instructions do not.

Membrane is a transparent proxy on an agent's retrieval and tool-call boundary.
Four layers run in sequence on every span of untrusted text:

* **L1 sanitiser** — strips payloads a human reader never sees
* **L2 separator** — splits declarative content from imperative spans
* **L3 taint tracker** — attaches provenance and traces tool arguments back to it
* **L4 capability firewall** — holds any call outside a signed intent contract

Uncertainty resolves to a human on Telegram, not to a guess. Silence is denial.
Content is inspected, never retained: the audit trail stores salted hashes,
verdicts and provenance edges rather than text.
"""


async def _janitor() -> None:
    """Background maintenance: expire cards, purge bounded retention."""
    while True:
        try:
            await asyncio.sleep(5.0)
            expired = await approvals.expire_stale()
            if expired:
                logger.info("expired %d held action(s) to denial", expired)
            if get_settings().replay_retention_enabled:
                async with session_scope() as session:
                    purged = await audit.purge_expired_replay(session)
                if purged:
                    logger.info("purged %d expired replay span(s)", purged)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - keep the loop alive
            logger.exception("janitor iteration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    for problem in settings.assert_production_safe():
        logger.warning("CONFIGURATION: %s", problem)

    await init_db()
    await bus.start()

    app.state.http_client = httpx.AsyncClient(
        timeout=10.0, limits=httpx.Limits(max_connections=32)
    )
    app.state.started_at = time.time()
    app.state.janitor = asyncio.create_task(_janitor())

    logger.info(
        "membrane up · env=%s · db=%s · redis=%s · telegram=%s",
        settings.environment,
        "postgres" if settings.is_postgres else "sqlite",
        bool(settings.redis_url),
        bool(settings.telegram_bot_token),
    )
    try:
        yield
    finally:
        app.state.janitor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.janitor
        await app.state.http_client.aclose()
        await bus.stop()
        await dispose_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Membrane",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_timing_header(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - started) * 1000
        response.headers["X-Membrane-Latency-Ms"] = f"{elapsed:.2f}"
        return response

    app.include_router(gateway.router)
    app.include_router(approvals_router.router)
    app.include_router(forensics.router)
    app.include_router(stream.router)
    app.include_router(mcp.router)
    app.include_router(bench.router)

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {
            "service": "membrane",
            "tagline": "Content passes through. Instructions do not.",
            "layers": ["l1_sanitiser", "l2_separator", "l3_taint", "l4_capability"],
            "verdicts": ["pass", "strip", "hold", "block"],
            "docs": "/docs",
        }

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        """Liveness. Deliberately does not touch the database."""
        return {"ok": True, "uptime_seconds": round(time.time() - app.state.started_at, 1)}

    @app.get("/readyz", tags=["meta"])
    async def readyz() -> JSONResponse:
        """Readiness. A failure here means the proxy is not accepting traffic.

        Membrane refuses traffic it cannot audit: if the audit store is
        unreachable, the correct behaviour is to fail closed at the load
        balancer rather than to enforce policy silently.
        """
        checks: dict[str, object] = {}
        ok = True
        try:
            from sqlalchemy import text as sql_text

            async with session_scope() as session:
                await session.execute(sql_text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"unavailable: {exc}"
            ok = False

        settings = get_settings()
        checks["escalation_configured"] = bool(
            settings.gemini_api_key or settings.vertex_project
        )
        checks["telegram_configured"] = bool(
            settings.telegram_bot_token and settings.telegram_chat_id
        )
        checks["capabilities"] = len(get_policy().capability_names)
        checks["config_warnings"] = settings.assert_production_safe()

        return JSONResponse({"ok": ok, "checks": checks},
                           status_code=200 if ok else 503)

    return app


app = create_app()
