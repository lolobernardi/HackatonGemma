"""Acceso a la base de centros de salud (proyecto `centros_salud_db`).

Este módulo es la ÚNICA parte del backend que sabe que existe PostgreSQL. El
resto del sistema (`recursos.py`) le pide centros y recibe diccionarios.

La consulta es la Q1 de `centros_salud_db/postgres/consultas_api.sql`, que es
el contrato que dejó definido quien armó la base. Dos cosas de esa consulta que
NO hay que "simplificar", y están explicadas allá con más detalle:

1. Los `CAST(:param AS tipo)` no son decorativos: sin ellos psycopg2 manda un
   NULL sin tipo y PostgreSQL corta con "could not determine data type".
2. Va `CAST(:param AS tipo)` y no la forma corta `:param::tipo`: SQLAlchemy no
   sustituye un parámetro seguido de `::` y la consulta explota.

**Degradación segura**: si la base no responde, `buscar()` devuelve una lista
vacía y lo loguea. Nunca propaga la excepción. El motivo es clínico: que no
haya centros para sugerir no puede impedir que la persona reciba su nivel de
urgencia y las indicaciones de seguridad.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app import config

logger = logging.getLogger(__name__)

__all__ = ["buscar", "disponible", "cerrar_engine"]


# --------------------------------------------------------------------------- #
# Q1 — búsqueda principal
# --------------------------------------------------------------------------- #
# Copiada de centros_salud_db/postgres/consultas_api.sql. Si allá cambia, acá
# también. Todos los filtros son opcionales: el parámetro en None desactiva su
# condición.

_Q_BUSCAR = """
SELECT
    c.id,
    c.nombre,
    c.tipo,
    c.dependencia,
    c.ciudad,
    c.provincia,
    c.direccion,
    c.barrio,
    c.telefono,
    c.horario,
    (c.horario IS NOT NULL) AS horario_informado,
    c.latitud,
    c.longitud,

    CASE
        WHEN CAST(:lat AS double precision) IS NULL
          OR CAST(:lon AS double precision) IS NULL
          OR c.latitud  IS NULL
          OR c.longitud IS NULL
        THEN NULL
        ELSE round((6371 * acos(LEAST(1, GREATEST(-1,
                 cos(radians(CAST(:lat AS double precision))) * cos(radians(c.latitud))
               * cos(radians(c.longitud) - radians(CAST(:lon AS double precision)))
               + sin(radians(CAST(:lat AS double precision))) * sin(radians(c.latitud))
             ))))::numeric, 3)
    END AS distancia_km,

    COALESCE((
        SELECT array_agg(e.nombre ORDER BY e.nombre)
        FROM centro_especialidad ce
        JOIN especialidades e ON e.id = ce.especialidad_id
        WHERE ce.centro_id = c.id
    ), ARRAY[]::text[]) AS especialidades

FROM centros c
WHERE
    (CAST(:ciudad AS text) IS NULL
     OR unaccent(lower(c.ciudad)) = unaccent(lower(CAST(:ciudad AS text))))

AND (CAST(:especialidad AS text) IS NULL
     OR EXISTS (
         SELECT 1
         FROM centro_especialidad ce
         JOIN especialidades e ON e.id = ce.especialidad_id
         WHERE ce.centro_id = c.id
           AND unaccent(lower(e.nombre)) LIKE
               '%' || unaccent(lower(CAST(:especialidad AS text))) || '%'
     ))

AND (CAST(:tipo AS text) IS NULL
     OR unaccent(lower(c.tipo)) = unaccent(lower(CAST(:tipo AS text))))

AND (CAST(:barrio AS text) IS NULL
     OR unaccent(lower(coalesce(c.barrio, ''))) LIKE
        '%' || unaccent(lower(CAST(:barrio AS text))) || '%')

-- Los centros sin horario informado NO se descartan: solo 6 de 86 lo tienen
-- cargado, filtrarlos dejaría afuera casi toda la base.
AND (CAST(:hora AS time) IS NULL
     OR c.horario IS NULL
     OR (
         substring(c.horario from '^\\s*(\\d{1,2}:\\d{2})')::time <= CAST(:hora AS time)
         AND substring(c.horario from 'a\\s*(\\d{1,2}:\\d{2})')::time >= CAST(:hora AS time)
     ))

AND (CAST(:radio_km AS double precision) IS NULL
     OR CAST(:lat AS double precision) IS NULL
     OR CAST(:lon AS double precision) IS NULL
     OR (c.latitud IS NOT NULL AND c.longitud IS NOT NULL
         AND 6371 * acos(LEAST(1, GREATEST(-1,
                 cos(radians(CAST(:lat AS double precision))) * cos(radians(c.latitud))
               * cos(radians(c.longitud) - radians(CAST(:lon AS double precision)))
               + sin(radians(CAST(:lat AS double precision))) * sin(radians(c.latitud))
             ))) <= CAST(:radio_km AS double precision)))

ORDER BY
    distancia_km ASC NULLS LAST,
    c.ciudad,
    c.nombre
LIMIT COALESCE(CAST(:limite AS int), 100)
"""

# Hay que mandar SIEMPRE todas las claves, aunque vayan en None: SQLAlchemy
# falla si falta una, no la asume NULL.
_PARAMS_VACIOS: dict[str, Any] = {
    "ciudad": None,
    "especialidad": None,
    "tipo": None,
    "barrio": None,
    "hora": None,
    "lat": None,
    "lon": None,
    "radio_km": None,
    "limite": None,
}


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

_engine: Engine | None = None


def _obtener_engine() -> Engine:
    """Engine perezoso y compartido.

    `pool_pre_ping` evita el clásico "server closed the connection": la base es
    de desarrollo y se apaga y prende a mano entre demos.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            config.CENTROS_DB_URL,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
            connect_args={"connect_timeout": config.CENTROS_DB_TIMEOUT_S},
        )
    return _engine


def cerrar_engine() -> None:
    """Cierra el pool. Se llama al apagar la app."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def disponible() -> bool:
    """True si la base responde. Para el endpoint `/health`."""
    try:
        with _obtener_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError as exc:
        logger.warning("centros_db_no_disponible error=%s", type(exc).__name__)
        return False


# --------------------------------------------------------------------------- #
# Búsqueda
# --------------------------------------------------------------------------- #


def buscar(
    *,
    ciudad: str | None = None,
    especialidad: str | None = None,
    tipo: str | None = None,
    hora: str | None = None,
    barrio: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radio_km: float | None = None,
    limite: int | None = None,
) -> list[dict[str, Any]]:
    """Centros que cumplen los filtros, ordenados por cercanía.

    Devuelve `[]` ante cualquier problema de base: nunca levanta.

    Nota sobre `distancia_km`: hoy da None en todas las filas porque la base no
    tiene las direcciones geocodificadas (0 de 86). El orden cae entonces en el
    criterio de respaldo (ciudad, nombre). Cuando se carguen lat/lon esto pasa
    a ordenar por cercanía sin tocar una línea.
    """
    params = dict(_PARAMS_VACIOS)
    params.update(
        ciudad=ciudad,
        especialidad=especialidad,
        tipo=tipo,
        barrio=barrio,
        hora=hora,
        lat=lat,
        lon=lng,
        radio_km=radio_km,
        limite=limite,
    )

    try:
        with _obtener_engine().connect() as conn:
            filas = conn.execute(text(_Q_BUSCAR), params).mappings().all()
    except SQLAlchemyError as exc:
        # No se propaga: sin centros el resultado del triaje igual se entrega.
        logger.warning(
            "centros_db_error error=%s ciudad=%s especialidad=%s",
            type(exc).__name__,
            ciudad,
            especialidad,
        )
        return []

    return [dict(fila) for fila in filas]
