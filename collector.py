"""Proceso independiente y continuo de recopilación meteorológica (Python).

Ciclo continuo:
    RECOPILAR → VALIDAR → COMPARAR → ALMACENAR → ACTUALIZAR → CONSULTAR

Uso:
    python collector.py           # Corre continuamente en segundo plano (intervalo automático del servidor)
    python collector.py --once    # Ejecuta un ciclo único de recopilación y verificación
    python collector.py --force   # Fuerza actualización de todas las ubicaciones monitoreadas una vez
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys

from app.config import settings
from app.db.database import get_db
from app.db.repository import WeatherRepository
from app.services.collector_service import CollectorService


async def run_worker(once: bool = False, force: bool = False, interval: int | None = None) -> None:
    db = get_db()
    repo = WeatherRepository(db)
    collector = CollectorService(repo)
    sleep_sec = max(60, int(interval or settings.collector_interval_seconds))

    stop_event = asyncio.Event()

    def _handle_signal(*_args):
        print("\n[collector] Señal de detención recibida. Cerrando ordenadamente...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass

    print(
        f"[collector] Motor meteorológico automático iniciado "
        f"(BD: {db.db_path} · modo={'ciclo único' if once else f'continuo cada {sleep_sec}s'})"
    )

    while not stop_event.is_set():
        try:
            summary = await collector.run_cycle(worker_name="standalone_collector", force_all=force)
            print("[collector] Ciclo completado:", json.dumps(summary, ensure_ascii=False))
        except Exception as exc:
            print(f"[collector] Error recuperable en ciclo: {type(exc).__name__}: {exc}", file=sys.stderr)

        if once:
            break

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_sec)
        except asyncio.TimeoutError:
            continue

    print("[collector] Proceso finalizado correctamente.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Proceso recolector continuo de Weather Dashboard Pro")
    parser.add_argument("--once", action="store_true", help="Ejecuta un solo ciclo y finaliza")
    parser.add_argument("--force", action="store_true", help="Actualiza todas las ubicaciones sin esperar vencimiento")
    parser.add_argument("--interval", type=int, default=None, help="Intervalo en segundos entre ciclos (solo servidor)")
    args = parser.parse_args()
    asyncio.run(run_worker(once=args.once, force=args.force, interval=args.interval))


if __name__ == "__main__":
    main()
