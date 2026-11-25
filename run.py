"""Lanzador directo de Weather Dashboard Pro (FastAPI + Uvicorn)."""

from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    print(f"Iniciando {settings.app_name} v{settings.app_version} en http://{settings.host}:{settings.port}")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
