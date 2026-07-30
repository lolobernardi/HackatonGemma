"""Motor de reglas de la escala de triaje de Manchester.

╔═══════════════════════════════════════════════════════════════════════════╗
║  STUB — LO IMPLEMENTA OTRA PERSONA DEL EQUIPO                             ║
║                                                                           ║
║  TODO(equipo): implementar los flowcharts de Manchester.                  ║
║                                                                           ║
║  Lo único que este archivo tiene que respetar es la firma de              ║
║  `clasificar(ficha) -> Clasificacion`. El orquestador no sabe ni le       ║
║  importa cómo está implementada adentro.                                  ║
║                                                                           ║
║  Contrato de salida:                                                      ║
║    - `color`: uno de rojo / naranja / amarillo / verde / azul.            ║
║    - `motivo_clasificacion`: por qué se llegó a ese color, en texto.      ║
║    - `discriminador_disparador`: QUÉ dato concreto de la ficha disparó    ║
║      el color. El orquestador se lo muestra a la persona como la razón    ║
║      de su clasificación, así que tiene que ser algo entendible.          ║
║                                                                           ║
║  Notas para quien lo implemente:                                          ║
║    - La ficha puede llegar INCOMPLETA. Si se agotaron las preguntas o se  ║
║      alcanzó el máximo de turnos, se clasifica con lo que haya. Campos    ║
║      en None son la norma, no la excepción.                               ║
║    - `None` significa "no se sabe", nunca "no". No tratar un None como    ║
║      negativo: ante la duda hay que subir la urgencia, no bajarla.        ║
║    - `ficha.es_para_tercero` suele indicar consulta pediátrica: los       ║
║      umbrales de temperatura y frecuencia cambian.                        ║
║    - Los casos de riesgo vital inmediato (no responde, no respira, vía    ║
║      aérea comprometida, hemorragia mayor) NO llegan acá: el orquestador  ║
║      corta antes con `derivacion_inmediata`. Igual conviene manejarlos    ║
║      por las dudas.                                                       ║
╚═══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

from app.schema import Clasificacion, FichaClinica

__all__ = ["Clasificacion", "clasificar"]


def clasificar(ficha: FichaClinica) -> Clasificacion:
    """Asigna un color de triaje a partir de la ficha clínica.

    TODO(equipo): implementar los flowcharts de Manchester.

    Este stub devuelve amarillo fijo **a propósito**: el fallback ante lo
    desconocido nunca debe ser un nivel bajo de urgencia. Si alguien deja el
    stub puesto por error, el sistema peca de precavido y no de lo contrario.
    """
    return Clasificacion(
        color="amarillo",
        motivo_clasificacion="STUB - motor de reglas no implementado",
        discriminador_disparador="ninguno",
    )
