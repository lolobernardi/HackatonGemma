"""Búsqueda de centros de salud cercanos.

╔═══════════════════════════════════════════════════════════════════════════╗
║  STUB — LO IMPLEMENTA OTRA PERSONA DEL EQUIPO                             ║
║                                                                           ║
║  TODO(equipo): reemplazar los centros hardcodeados por la búsqueda real   ║
║  (dataset de efectores, cálculo de distancia, ocupación estimada).        ║
║                                                                           ║
║  Firma a respetar:                                                        ║
║      buscar_recurso(color, especialidad, lat, lng) -> list[Recurso]       ║
║                                                                           ║
║  Notas para quien lo implemente:                                          ║
║    - `color` condiciona el TIPO de centro: rojo/naranja → guardia de alta ║
║      complejidad; amarillo → guardia general; verde/azul → centro de      ║
║      atención primaria. No mandar un azul a una guardia de alta           ║
║      complejidad: satura el sistema.                                      ║
║    - `lat`/`lng` pueden venir en None (el cliente no compartió ubicación).║
║      En ese caso hay que devolver algo igual, sin distancia confiable.    ║
║    - Devolver la lista ordenada por conveniencia, no solo por distancia:  ║
║      un centro un poco más lejos pero vacío puede ser mejor opción.       ║
╚═══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

from app.schema import ColorTriaje, Recurso

__all__ = ["Recurso", "buscar_recurso"]


def buscar_recurso(
    color: ColorTriaje,
    especialidad: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> list[Recurso]:
    """Devuelve los centros de salud sugeridos para ese nivel de urgencia.

    TODO(equipo): implementar la búsqueda real. Por ahora, dos centros
    hardcodeados para que la demo tenga algo que mostrar.
    """
    return [
        Recurso(
            nombre="Hospital San Martín - Guardia",
            tipo="guardia_hospitalaria",
            distancia_km=2.4,
            ocupacion_estimada="alta",
        ),
        Recurso(
            nombre="Centro de Salud Nº 12",
            tipo="atencion_primaria",
            distancia_km=0.9,
            ocupacion_estimada="baja",
        ),
    ]
