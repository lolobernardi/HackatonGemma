"""Búsqueda de centros de salud en la base `centros_salud`.

Los datos salen del proyecto hermano `centros_salud_db` (PostgreSQL en
127.0.0.1:5438).

## Quién decide qué

Este módulo **no decide clínica**. A qué especialista hay que ir y a qué tipo
de efector lo decide `reglas.py` y viene resuelto dentro de la `Clasificacion`
(`especialidad_sugerida`, `tipo_recurso_sugerido`). Acá sólo se traduce ese
vocabulario al de la base y se busca.

Cuando esto se duplicaba —un mapa motivo→especialidad acá y otro en el
ruleset— había dos fuentes de verdad para la misma pregunta clínica, que es
exactamente cómo se desincronizan.

## Los tres criterios de búsqueda

- **ciudad**: dónde está la persona. El frontend se lo pregunta. Si su
  localidad no tiene centros cargados, se busca en la ciudad con cobertura más
  cercana, calculada por distancia real, y el mensaje final se lo aclara.
- **especialista**: el que dice el motor de reglas.
- **horario**: la hora de la consulta, para no mandar a nadie a un centro
  cerrado.

## Por qué la búsqueda es en cascada

La cobertura de la base es muy despareja: Cardiología está en 2 centros de 86 y
Gastroenterología en 1, contra 44 de Medicina General. Además hay
especialidades que el ruleset pide y la base no tiene (dermatología,
neumonología, neurología, infectología: 0 centros). Una consulta rígida por
(ciudad + especialidad + tipo + hora) devolvería vacío casi siempre.

Se prueban criterios de más específico a más amplio y gana el primero que
devuelve algo, dejando registrado cuál fue. Ese dato llega hasta el frontend:
si a alguien le terminaron ofreciendo un centro de atención primaria porque no
había cardiología cerca, tiene que poder saberlo.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app import centros_db, config
from app.schema import Clasificacion, FichaClinica, Recurso

logger = logging.getLogger(__name__)

__all__ = [
    "Recurso",
    "ResultadoBusqueda",
    "buscar_para_clasificacion",
    "resolver_ciudad",
    "resolver_especialidad",
    "distancia_km",
]


@dataclass(frozen=True, slots=True)
class ResultadoBusqueda:
    """Centros encontrados más el rastro de cómo se los encontró."""

    recursos: list[Recurso] = field(default_factory=list)
    # Ciudad que se terminó consultando (puede no ser la de la persona).
    ciudad_buscada: str | None = None
    # Localidad declarada por la persona, antes de traducirla.
    ciudad_persona: str | None = None
    # Especialidad con la que se encontraron los centros, en nombre de la base.
    especialidad: str | None = None
    # Etiqueta corta del criterio que dio resultado, para loguear y mostrar.
    criterio: str = "sin_resultados"
    # Distancia entre la localidad de la persona y la ciudad donde se buscó.
    distancia_a_ciudad_km: float | None = None

    @property
    def hubo_derivacion_de_ciudad(self) -> bool:
        """True si se buscó en una ciudad distinta a la de la persona."""
        if not self.ciudad_persona or not self.ciudad_buscada:
            return False
        return _clave(self.ciudad_persona) != _clave(self.ciudad_buscada)


# --------------------------------------------------------------------------- #
# Ubicación
# --------------------------------------------------------------------------- #


def _clave(texto: str) -> str:
    return texto.strip().lower()


def distancia_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distancia Haversine en kilómetros entre dos puntos (lat, lon)."""
    radio = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * radio * math.asin(min(1.0, math.sqrt(h)))


def _coordenadas(ciudad: str | None) -> tuple[float, float] | None:
    if not ciudad:
        return None
    for nombre, punto in config.LOCALIDADES.items():
        if _clave(nombre) == _clave(ciudad):
            return punto
    return None


def resolver_ciudad(
    ciudad_persona: str | None,
    lat: float | None = None,
    lng: float | None = None,
) -> tuple[str | None, float | None]:
    """Ciudad con cobertura más cercana a la persona, y a qué distancia.

    El punto de referencia es, por orden: las coordenadas que compartió el
    navegador, o las de la localidad que eligió. Si no hay ninguna de las dos y
    la localidad no está entre las que tienen centros, se devuelve `None` y la
    búsqueda se hace sin filtro de ciudad, que es mejor que no devolver nada.
    """
    # Si ya está en una ciudad con cobertura, no hay nada que resolver.
    for cobertura in config.CIUDADES_CON_COBERTURA:
        if ciudad_persona and _clave(cobertura) == _clave(ciudad_persona):
            return cobertura, 0.0

    origen: tuple[float, float] | None = None
    if lat is not None and lng is not None:
        origen = (lat, lng)
    else:
        origen = _coordenadas(ciudad_persona)

    if origen is None:
        logger.info(
            "ciudad_sin_referencia ciudad=%s buscando_sin_filtro=true", ciudad_persona
        )
        return None, None

    candidatas = [
        (ciudad, distancia_km(origen, config.LOCALIDADES[ciudad]))
        for ciudad in config.CIUDADES_CON_COBERTURA
        if ciudad in config.LOCALIDADES
    ]
    if not candidatas:
        return None, None

    destino, distancia = min(candidatas, key=lambda par: par[1])
    logger.info(
        "ciudad_resuelta origen=%s destino=%s distancia_km=%.1f",
        ciudad_persona,
        destino,
        distancia,
    )
    return destino, round(distancia, 1)


# --------------------------------------------------------------------------- #
# Especialista
# --------------------------------------------------------------------------- #


def resolver_especialidad(
    especialidad_motor: str | None,
    edad: int | None = None,
    tipo_recurso: str | None = None,
) -> str:
    """Traduce el slug del ruleset al nombre que usa la base.

    Las especialidades que el ruleset pide y la base no tiene (dermatología,
    neumonología, neurología, infectología) caen en la de respaldo: es
    preferible ofrecer un centro de medicina general real que ninguno.
    """
    if especialidad_motor:
        slug = _clave(especialidad_motor)
        if slug in config.ESPECIALIDAD_DB:
            nombre = config.ESPECIALIDAD_DB[slug]
            if nombre:
                return nombre
            # Declarada como sin cobertura: se cae al respaldo a propósito.
            logger.info(
                "especialidad_sin_cobertura slug=%s usando=%s",
                slug,
                config.ESPECIALIDAD_FALLBACK,
            )
            return config.ESPECIALIDAD_FALLBACK
        # Slug nuevo en el ruleset que nadie tradujo todavía. Se avisa fuerte:
        # es desalineación entre el ruleset y este mapa, no un caso esperado.
        logger.warning("especialidad_desconocida slug=%s", slug)

    # El motor no sugirió especialista: si es un menor, igual va un pediatra.
    if edad is not None and edad <= config.EDAD_PEDIATRICA_MAX:
        return config.ESPECIALIDAD_PEDIATRICA

    # Sin especialista y con urgencia alta, lo que corresponde no es medicina
    # general sino un efector con guardia: filtrar por "Medicina General" podría
    # dejar afuera justo al hospital que sabe recibir una urgencia.
    if tipo_recurso in config.TIPOS_RECURSO_DE_URGENCIA:
        return config.ESPECIALIDAD_URGENCIA

    return config.ESPECIALIDAD_FALLBACK


def _hora_actual() -> str:
    """Hora de la consulta en el formato que espera la columna `time`."""
    return datetime.now().strftime("%H:%M")


# --------------------------------------------------------------------------- #
# Búsqueda
# --------------------------------------------------------------------------- #


def buscar_para_clasificacion(
    clasificacion: Clasificacion,
    ficha: FichaClinica,
    *,
    ciudad: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    hora: str | None = None,
) -> ResultadoBusqueda:
    """Centros adecuados para este veredicto, en la zona de la persona."""
    ciudad_persona = ciudad or config.CIUDAD_PACIENTE
    ciudad_buscada, distancia = resolver_ciudad(ciudad_persona, lat, lng)
    especialidad = resolver_especialidad(
        clasificacion.especialidad_sugerida,
        ficha.edad,
        clasificacion.tipo_recurso_sugerido,
    )
    hora = hora or _hora_actual()
    tipos = config.TIPOS_POR_RECURSO.get(clasificacion.tipo_recurso_sugerido, ())
    limite = config.MAX_CENTROS_SUGERIDOS

    # De más específico a más amplio. El primero que devuelva algo, gana.
    intentos: list[tuple[str, dict[str, Any]]] = []
    for tipo in tipos:
        intentos.append(
            (
                f"ciudad+especialidad+{tipo}+horario",
                {"ciudad": ciudad_buscada, "especialidad": especialidad,
                 "tipo": tipo, "hora": hora},
            )
        )
    for tipo in tipos:
        intentos.append(
            (
                f"ciudad+especialidad+{tipo}",
                {"ciudad": ciudad_buscada, "especialidad": especialidad, "tipo": tipo},
            )
        )
    # Sin la especialidad específica: mejor el efector del nivel correcto con
    # medicina general que un centro que no corresponde a la urgencia.
    if especialidad != config.ESPECIALIDAD_FALLBACK:
        for tipo in tipos:
            intentos.append(
                (
                    f"ciudad+{config.ESPECIALIDAD_FALLBACK}+{tipo}",
                    {"ciudad": ciudad_buscada,
                     "especialidad": config.ESPECIALIDAD_FALLBACK, "tipo": tipo},
                )
            )
    for tipo in tipos:
        intentos.append(
            (f"ciudad+{tipo}", {"ciudad": ciudad_buscada, "tipo": tipo})
        )
    intentos.append(
        ("ciudad+especialidad", {"ciudad": ciudad_buscada, "especialidad": especialidad})
    )
    intentos.append(("ciudad", {"ciudad": ciudad_buscada}))
    intentos.append(("especialidad", {"especialidad": especialidad}))

    for criterio, filtros in intentos:
        filas = centros_db.buscar(lat=lat, lng=lng, limite=limite, **filtros)
        if filas:
            logger.info(
                "centros_encontrados criterio=%s cantidad=%d ciudad=%s especialidad=%s",
                criterio,
                len(filas),
                ciudad_buscada,
                especialidad,
            )
            return ResultadoBusqueda(
                recursos=[_a_recurso(f) for f in filas],
                ciudad_buscada=ciudad_buscada,
                ciudad_persona=ciudad_persona,
                especialidad=filtros.get("especialidad"),
                criterio=criterio,
                distancia_a_ciudad_km=distancia,
            )

    logger.warning(
        "centros_sin_resultados ciudad=%s especialidad=%s color=%s",
        ciudad_buscada,
        especialidad,
        clasificacion.color,
    )
    return ResultadoBusqueda(
        ciudad_buscada=ciudad_buscada,
        ciudad_persona=ciudad_persona,
        especialidad=especialidad,
        distancia_a_ciudad_km=distancia,
    )


def _a_recurso(fila: dict[str, Any]) -> Recurso:
    """Convierte una fila de la base en el modelo que ve el resto del sistema."""
    distancia = fila.get("distancia_km")
    return Recurso(
        nombre=fila["nombre"],
        tipo=fila["tipo"],
        ciudad=fila.get("ciudad"),
        direccion=fila.get("direccion"),
        telefono=fila.get("telefono"),
        horario=fila.get("horario"),
        horario_informado=bool(fila.get("horario_informado")),
        especialidades=list(fila.get("especialidades") or []),
        # La base devuelve Decimal; el schema espera float.
        distancia_km=float(distancia) if distancia is not None else None,
        # No existe en la base. Se deja en None en vez de inventarla.
        ocupacion_estimada=None,
    )
