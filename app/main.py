"""Aplicación principal FastAPI de Weather Dashboard Pro."""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as api_router
from app.config import BASE_DIR, settings
from app.db.database import DatabaseManager, get_db
from app.db.repository import WeatherRepository
from app.services.scheduler import BackgroundScheduler
from app.services.weather_facade import WeatherFacade


def create_app(db_path: Path | str | None = None, enable_scheduler: bool = True) -> FastAPI:
    db = DatabaseManager(db_path) if db_path is not None else get_db()
    repo = WeatherRepository(db)
    facade = WeatherFacade(repo)
    scheduler = BackgroundScheduler(facade)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if enable_scheduler:
            scheduler.start()
        yield
        await scheduler.stop()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Plataforma meteorológica multiproveedor independiente en Python (sin IA).",
        lifespan=lifespan,
    )
    app.state.facade = facade
    app.state.scheduler = scheduler

    rate_buckets: dict[str, deque[float]] = defaultdict(deque)
    RATE_LIMIT_MAX = 180
    RATE_LIMIT_WINDOW = 60.0

    @app.middleware("http")
    async def security_and_rate_limit_middleware(request: Request, call_next):
        if request.url.path.startswith("/api/v1/"):
            client_host = request.client.host if request.client else "unknown"
            if client_host not in ("testclient", "127.0.0.1", "::1"):
                now_ts = time.monotonic()
                bucket = rate_buckets[client_host]
                while bucket and (now_ts - bucket[0]) > RATE_LIMIT_WINDOW:
                    bucket.popleft()
                if len(bucket) >= RATE_LIMIT_MAX:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Demasiadas solicitudes consecutivas. Reintentá en unos segundos."},
                    )
                bucket.append(now_ts)

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
        return response

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=500,
                content={"detail": f"Error interno del servidor: {type(exc).__name__}"},
            )
        return HTMLResponse(
            status_code=500,
            content="<h1>Error interno</h1><p>Ocurrió un inconveniente al cargar Weather Dashboard Pro.</p>",
        )

    static_dir = BASE_DIR / "static"
    templates_dir = BASE_DIR / "templates"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))

    app.include_router(api_router)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"app_name": settings.app_name, "app_version": settings.app_version},
        )

    return app


app = create_app()
