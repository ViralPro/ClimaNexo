"""Programador automático interno administrado exclusivamente por el servidor Python."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from app.config import settings
from app.services.weather_facade import WeatherFacade


class BackgroundScheduler:
    """Ejecuta periódicamente el ciclo RECOPILAR→VALIDAR→COMPARAR→ALMACENAR→ACTUALIZAR."""

    def __init__(self, facade: WeatherFacade) -> None:
        self.facade = facade
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.last_run_at: Optional[str] = None
        self.runs_completed: int = 0
        self.last_summary: dict[str, Any] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="weather-server-collector")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_once(self, force_all: bool = False) -> dict[str, Any]:
        summary = await self.facade.collector.run_cycle(
            worker_name="server_scheduler",
            force_all=force_all,
            max_locations=10,
        )
        self.last_run_at = summary["finished_at"]
        self.runs_completed += 1
        self.last_summary = summary
        return summary

    async def _loop(self) -> None:
        # Breve espera inicial para permitir arranque instantáneo de FastAPI
        await asyncio.sleep(5)
        while self._running:
            try:
                await self.run_once(force_all=False)
            except Exception:
                pass
            # Intervalo administrado 100% por el servidor (no modificable por el usuario)
            await asyncio.sleep(max(120, int(settings.collector_interval_seconds)))

    def status(self) -> dict[str, Any]:
        recent_runs = self.facade.repo.list_collection_runs(limit=3)
        return {
            "running": self._running,
            "managed_by": "server",
            "interval_seconds": settings.collector_interval_seconds,
            "last_run_at": self.last_run_at or (recent_runs[0]["finished_at"] if recent_runs else None),
            "runs_completed": self.runs_completed,
            "recent_runs": recent_runs,
        }
