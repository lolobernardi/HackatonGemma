"""Store de sesiones en memoria.

No hay base de datos, y es una decisión de diseño, no una limitación del
prototipo: los datos de salud no se retienen. Cuando la sesión se cierra o
queda inactiva, la entrada se **borra** del dict. No hay soft-delete, no hay
flag de "archivada", no queda rastro.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app import config
from app.schema import FichaClinica, SesionState, TurnoHistorial

logger = logging.getLogger(__name__)

# session_id -> estado. Único lugar donde vive la información clínica.
_SESIONES: dict[str, SesionState] = {}


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def crear_sesion() -> SesionState:
    """Crea una sesión nueva con ficha vacía y devuelve su estado."""
    ahora = _ahora()
    state = SesionState(
        session_id=str(uuid.uuid4()),
        ficha=FichaClinica(),
        creada_en=ahora,
        ultimo_acceso=ahora,
    )
    _SESIONES[state.session_id] = state
    logger.info("sesion_creada session_id=%s", state.session_id)
    return state


def obtener(session_id: str) -> SesionState:
    """Devuelve la sesión y refresca su marca de actividad. 404 si no existe."""
    state = _SESIONES.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="La sesión no existe o ya fue cerrada. Iniciá una nueva.",
        )
    state.ultimo_acceso = _ahora()
    return state


def existe(session_id: str) -> bool:
    return session_id in _SESIONES


def registrar_turno(state: SesionState, rol: str, texto: str) -> None:
    """Agrega una entrada al historial conversacional de la sesión."""
    state.historial.append(TurnoHistorial(rol=rol, texto=texto))  # type: ignore[arg-type]


def cerrar(session_id: str) -> bool:
    """Borra la sesión del store. Devuelve True si existía.

    Ojo: borra de verdad. No marca nada, no archiva. Es el mecanismo por el
    cual se cumple la promesa que se le hace al usuario en el mensaje de
    bienvenida.
    """
    existia = _SESIONES.pop(session_id, None) is not None
    if existia:
        logger.info("sesion_cerrada session_id=%s", session_id)
    return existia


def cantidad_sesiones() -> int:
    """Cantidad de sesiones vivas. Para /health y tests, no expone contenido."""
    return len(_SESIONES)


def limpiar_todo() -> None:
    """Vacía el store. Se usa en tests y al apagar la app."""
    _SESIONES.clear()


def barrer_inactivas(ttl_minutos: int | None = None) -> int:
    """Elimina las sesiones sin actividad reciente. Devuelve cuántas borró."""
    ttl = config.SESION_TTL_MINUTOS if ttl_minutos is None else ttl_minutos
    limite = _ahora() - timedelta(minutes=ttl)
    vencidas = [sid for sid, s in _SESIONES.items() if s.ultimo_acceso < limite]
    for sid in vencidas:
        _SESIONES.pop(sid, None)
        logger.info("sesion_expirada session_id=%s ttl_min=%s", sid, ttl)
    return len(vencidas)


async def barrido_periodico() -> None:
    """Tarea de fondo: barre sesiones inactivas cada `BARRIDO_INTERVALO_S`.

    Se lanza con `asyncio.create_task` al arrancar la app y se cancela al
    apagarla. Nunca deja que una excepción la mate en silencio.
    """
    while True:
        try:
            await asyncio.sleep(config.BARRIDO_INTERVALO_S)
            borradas = barrer_inactivas()
            if borradas:
                logger.info(
                    "barrido_completo borradas=%d vivas=%d",
                    borradas,
                    len(_SESIONES),
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensivo
            logger.exception("barrido_error")
