"""Modelos Pydantic del módulo de recolección conversacional.

Este archivo es el contrato entre todas las piezas del sistema:
- lo que Gemma tiene que devolver (`CamposExtraidos`),
- lo que se va acumulando en la sesión (`FichaClinica`),
- lo que el motor de reglas y el buscador de recursos devuelven
  (`Clasificacion`, `Recurso`) — implementados por otras personas del equipo,
- lo que ve el cliente HTTP (`MensajeRequest`, `RespuestaAPI`, ...).

Regla de diseño transversal: **todo campo clínico es opcional y arranca en
`None`**. La ficha nace vacía y se llena de a poco, turno a turno. Un `None`
significa "no lo sé", nunca "no". Esa distinción es la que le permite al motor
de reglas saber si le falta información o si tiene un dato negativo real.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Alias de tipos
# --------------------------------------------------------------------------- #

NivelConciencia = Literal["alerta", "somnoliento", "confuso", "no_responde"]
Inicio = Literal["subito", "gradual"]
ColorTriaje = Literal["rojo", "naranja", "amarillo", "verde", "azul"]
TipoRespuesta = Literal["pregunta", "resultado", "derivacion_inmediata", "error_seguro"]

# Valor que puede tomar un discriminador específico de un flowchart.
ValorDiscriminador = bool | int | float | str | None


# --------------------------------------------------------------------------- #
# Ficha clínica
# --------------------------------------------------------------------------- #


class DiscriminadoresGenerales(BaseModel):
    """Discriminadores que definen riesgo vital, aplicables a cualquier motivo.

    En Manchester estos se evalúan siempre, antes que los específicos del
    flowchart. Por eso el orquestador los prioriza al preguntar y el chequeo
    de bandera roja mira únicamente los cuatro primeros.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Riesgo vital inmediato (los que disparan derivación sin más preguntas) ---
    riesgo_via_aerea: bool | None = None
    respira_normalmente: bool | None = None
    nivel_conciencia: NivelConciencia | None = None
    hemorragia_mayor: bool | None = None

    # --- Modificadores generales ---
    dolor_eva: int | None = Field(default=None, ge=0, le=10)
    temperatura_c: float | None = Field(default=None, ge=25.0, le=45.0)
    inicio: Inicio | None = None
    tiempo_evolucion_horas: float | None = Field(default=None, ge=0)


class FichaClinica(BaseModel):
    """Estado clínico acumulado de una sesión.

    Se construye por merge sucesivo: cada turno solo puede agregar o corregir
    campos, nunca borrarlos (ver `gemma.merge_ficha`).
    """

    model_config = ConfigDict(extra="forbid")

    edad: int | None = Field(default=None, ge=0, le=120)
    # "mi hijo tiene fiebre" -> es_para_tercero=True. Cambia los umbrales
    # pediátricos que aplica el motor de reglas.
    es_para_tercero: bool = False
    # Slug del flowchart de Manchester. Los valores válidos viven en
    # `config.MOTIVOS_CONSULTA` y se le imponen a Gemma vía enum en el schema
    # de la tool; acá queda como str para no romper si el modelo inventa uno.
    motivo_consulta: str | None = None

    discriminadores_generales: DiscriminadoresGenerales = Field(
        default_factory=DiscriminadoresGenerales
    )
    # Discriminadores propios del flowchart elegido (ej. "irradia_brazo": True).
    discriminadores_especificos: dict[str, ValorDiscriminador] = Field(
        default_factory=dict
    )

    # Acumulado textual de lo que dijo la persona. Nunca se loguea.
    relato_libre: str = ""
    # Autorreportada por el modelo. Es una señal, no una garantía.
    confianza_extraccion: float | None = Field(default=None, ge=0.0, le=1.0)
    campos_faltantes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Contrato con Gemma
# --------------------------------------------------------------------------- #


class CamposExtraidos(BaseModel):
    """Payload de la tool call `actualizar_ficha`.

    Espeja `FichaClinica` pero con **todo** opcional (incluido
    `es_para_tercero`, que en la ficha tiene default `False`): el modelo solo
    reporta lo que efectivamente pudo extraer del mensaje. Lo que no dice,
    no se toca.
    """

    model_config = ConfigDict(extra="ignore")

    edad: int | None = Field(default=None, ge=0, le=120)
    es_para_tercero: bool | None = None
    motivo_consulta: str | None = None
    discriminadores_generales: DiscriminadoresGenerales | None = None
    discriminadores_especificos: dict[str, ValorDiscriminador] | None = None
    campos_faltantes: list[str] | None = None


class RespuestaGemma(BaseModel):
    """Lo que el cliente de Ollama le devuelve al orquestador."""

    model_config = ConfigDict(extra="forbid")

    campos: CamposExtraidos = Field(default_factory=CamposExtraidos)
    # UNA sola pregunta, en lenguaje coloquial. None si el modelo no pidió nada.
    pregunta_aclaracion: str | None = None
    confianza_extraccion: float = Field(default=0.0, ge=0.0, le=1.0)
    # Latencia de la llamada, para logging. No es parte del contrato clínico.
    latencia_ms: int | None = None


# --------------------------------------------------------------------------- #
# Salidas de los módulos de otras personas del equipo
# --------------------------------------------------------------------------- #


class Clasificacion(BaseModel):
    """Veredicto del motor de reglas de Manchester (ver `reglas.py`)."""

    model_config = ConfigDict(extra="forbid")

    color: ColorTriaje
    motivo_clasificacion: str
    # Qué discriminador concreto disparó el color. Es lo que se le explica a
    # la persona en el mensaje final: el "por qué" de la clasificación.
    discriminador_disparador: str


class Recurso(BaseModel):
    """Centro de salud sugerido (ver `recursos.py`)."""

    model_config = ConfigDict(extra="forbid")

    nombre: str
    tipo: str
    distancia_km: float
    ocupacion_estimada: str


class Resultado(BaseModel):
    """Bloque `resultado` de la respuesta cuando `tipo == "resultado"`."""

    model_config = ConfigDict(extra="forbid")

    color: ColorTriaje
    motivo_clasificacion: str
    discriminador_disparador: str
    recursos: list[Recurso] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# API HTTP
# --------------------------------------------------------------------------- #


class SesionResponse(BaseModel):
    """Respuesta de `POST /sesion`."""

    session_id: str
    mensaje: str


class MensajeRequest(BaseModel):
    """Body de `POST /mensaje`."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    texto: str = Field(min_length=1, max_length=4000)
    # Base64 **sin** el prefijo `data:image/...;base64,` (formato Ollama).
    imagen_b64: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class DebugInfo(BaseModel):
    """Bloque de introspección. Solo se emite si `DEBUG_MODE` está prendido."""

    model_config = ConfigDict(extra="forbid")

    turno: int
    campos_faltantes: list[str] = Field(default_factory=list)
    confianza: float | None = None
    ficha: dict[str, Any] = Field(default_factory=dict)


class RespuestaAPI(BaseModel):
    """Respuesta de `POST /mensaje`."""

    model_config = ConfigDict(extra="forbid")

    tipo: TipoRespuesta
    mensaje: str
    debug: DebugInfo | None = None
    resultado: Resultado | None = None


# --------------------------------------------------------------------------- #
# Estado de sesión
# --------------------------------------------------------------------------- #


class TurnoHistorial(BaseModel):
    """Una entrada del historial conversacional."""

    model_config = ConfigDict(extra="forbid")

    rol: Literal["usuario", "asistente"]
    texto: str


class SesionState(BaseModel):
    """Estado completo de una sesión. Vive solo en memoria y se borra al cerrar."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    ficha: FichaClinica = Field(default_factory=FichaClinica)
    turnos: int = 0
    preguntas_aclaracion: int = 0
    historial: list[TurnoHistorial] = Field(default_factory=list)
    cerrada: bool = False
    creada_en: datetime
    # Marca para el barrido de inactividad.
    ultimo_acceso: datetime
