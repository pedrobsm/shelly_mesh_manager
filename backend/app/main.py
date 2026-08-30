"""Application entrypoint: static frontend + API on one origin (SPEC §3.1)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router
from .config import settings
from .db import init_db
from .demo_fixtures import seed_demo
from .discovery import ScanManager, run_scan
from .graph import rebuild_edges

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("shelly_mesh_manager")

BACKEND_DIR = Path(__file__).resolve().parent.parent
STATIC_CANDIDATES = [BACKEND_DIR / "static", BACKEND_DIR.parent / "frontend" / "dist"]


def static_dir() -> Path | None:
    for candidate in STATIC_CANDIDATES:
        if (candidate / "index.html").is_file():
            return candidate
    return None


async def _periodic_scan(app: FastAPI) -> None:
    interval = settings.scan_interval_min * 60
    while True:
        await asyncio.sleep(interval)
        scans: ScanManager = app.state.scans
        if scans.running:
            continue
        try:
            await scans.start()
        except Exception:  # pragma: no cover - never kill the loop
            log.exception("periodic scan failed to start")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = await init_db(settings.db_path)
    app.state.db = db
    app.state.settings = settings
    app.state.scans = ScanManager(db, settings)

    if settings.demo_mode:
        log.info("DEMO_MODE=true — seeding fixture network, no network I/O")
        await seed_demo(db)
        await rebuild_edges(db)
    else:
        app.state.startup_scan = asyncio.create_task(run_scan(db, settings))

    ticker = None
    if not settings.demo_mode and settings.scan_interval_min > 0:
        ticker = asyncio.create_task(_periodic_scan(app))

    try:
        yield
    finally:
        for task in (ticker, getattr(app.state, "startup_scan", None)):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        await db.close()


app = FastAPI(title="Shelly Mesh Manager", version=__version__, lifespan=lifespan)
app.include_router(router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        body = detail
    else:
        body = {"error": "http_error", "detail": str(detail)}
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"error": "invalid_request", "detail": str(exc.errors())}
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500, content={"error": "internal_error", "detail": str(exc)}
    )


_static = static_dir()
if _static is not None:
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """Serve the built SPA; all paths stay relative so Ingress can host it."""
        candidate = (_static / full_path).resolve()
        if full_path and candidate.is_file() and _static.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_static / "index.html")

else:  # pragma: no cover - only before the frontend is built

    @app.get("/", include_in_schema=False)
    async def missing_frontend() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "frontend_not_built",
                "detail": "run `make dev` or build the image; expected frontend/dist/index.html",
            },
        )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
